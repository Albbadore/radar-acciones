"""Regressions found running the scanner against real data (2026-09-24)."""
from datetime import date

from radar.enrich import _adjust_sec_shares
from radar.models import ReverseSplitEvent, Sourced
from radar.parsing.classifier import classify_items, classify_text, filing_body
from radar.signals import score_volume
from radar.split_guard import reconcile_prev_close

TODAY = date(2026, 9, 24)


class TestReconcileRealMoves:
    def test_genuine_crash_not_taken_as_split(self):
        # JAGX-like: -74 % real move, sources disagree x3.9 -> keep primary.
        chosen, note = reconcile_prev_close([Sourced(8.9, "Yahoo diario"), Sourced(34.46, "Nasdaq")])
        assert chosen.source == "Yahoo diario" and "discrepa" in note

    def test_known_split_ratio_accepted(self):
        event = ReverseSplitEvent("ejecutado", date(2026, 9, 23), 4, "t")
        chosen, _ = reconcile_prev_close([Sourced(1.0, "A"), Sourced(4.1, "B")], event)
        assert chosen.source == "B"

    def test_large_ratio_without_event_accepted(self):
        chosen, _ = reconcile_prev_close([Sourced(0.5, "A"), Sourced(10.0, "B")])
        assert chosen.source == "B"


class TestStaleSecShares:
    def test_old_cover_page_dropped(self):
        old = Sourced(171_000_000, "SEC 10-Q", "2018-05-15")
        assert _adjust_sec_shares(old, None, TODAY) is None

    def test_recent_pre_split_divided(self):
        recent = Sourced(28_842_069, "SEC 10-Q", "2026-08-17")
        event = ReverseSplitEvent("ejecutado", date(2026, 9, 22), 20, "t")
        assert _adjust_sec_shares(recent, event, TODAY).value == 28_842_069 / 20

    def test_post_split_count_kept(self):
        recent = Sourced(1_500_000, "SEC 10-Q", "2026-09-23")
        event = ReverseSplitEvent("ejecutado", date(2026, 9, 22), 20, "t")
        assert _adjust_sec_shares(recent, event, TODAY).value == 1_500_000


class TestCatalystNoise:
    def test_split_items_not_catalysts(self):
        assert classify_items("3.03,5.03,9.01") == []

    def test_cover_page_boilerplate_skipped(self):
        text = ("FORM 8-K Pre-commencement communications ... tender offer for ... "
                "Item 5.02 Departure of Directors. Mr X resigned. SIGNATURES pursuant ...")
        assert classify_text(filing_body(text)) == []

    def test_generic_acquisition_word_ignored(self):
        assert classify_text("costs related to the acquisition of equipment") == []
        assert classify_text("entered into a merger agreement with Acme")[0][0] == "Adquisicion/fusion"


class TestSelloffVolume:
    def test_volume_on_crash_not_active(self, scfg):
        s = score_volume(15.0, None, None, scfg["volume"], change_pct=-50.0)
        assert not s.active and s.points == 12.5 and "caida" in s.detail

    def test_volume_on_rise_active(self, scfg):
        assert score_volume(15.0, None, None, scfg["volume"], change_pct=20.0).active


class TestLateMove:
    """JAGX (+1190 % intraday) and WHLR (+191 %) were flagged after the move."""

    def signals(self):
        from radar.models import SignalScore
        return tuple(SignalScore(n, p, 20, True, "") for n, p in
                     (("reverse_split", 17), ("float", 20), ("volume", 25), ("catalyst", 10), ("price", 9)))

    def test_early_move_not_penalised(self, scfg):
        from radar.scoring import combine
        r = combine(self.signals(), scfg, run_up_pct=35.0)
        assert r.penalty == 0 and r.total == 96 and r.late_note == ""

    def test_advanced_move_penalised(self, scfg):
        from radar.scoring import combine
        r = combine(self.signals(), scfg, run_up_pct=150.0)
        assert r.penalty == 10 and r.total == 86 and "avanzado" in r.late_note

    def test_late_move_capped_to_watch(self, scfg):
        from radar.scoring import LEVEL_VIGILAR, combine
        r = combine(self.signals(), scfg, run_up_pct=279.0)
        assert r.total == 64 and r.level == LEVEL_VIGILAR and r.capped and "TARDE" in r.late_note

    def test_run_up_from_bars(self):
        import pandas as pd

        from radar.metrics import daily_stats
        idx = pd.bdate_range("2026-09-14", "2026-09-23", tz="America/New_York")
        lows = [4.4, 3.0, 3.1, 2.35, 2.65, 2.48, 2.75, 8.5]
        frame = pd.DataFrame({"Open": lows, "High": lows, "Low": lows, "Close": lows,
                              "Volume": [1] * len(lows)}, index=idx)
        assert daily_stats(frame, date(2026, 9, 24)).low_5d == 2.35


def test_director_resignation_mentioning_merger_is_not_merger(tmp_path):
    """GCTK 2026-09-18: 8-K item 5.02 cited 'the merger agreement' -> was scored as M&A."""
    from radar.catalysts import filing_categories
    from radar.db import Database
    from radar.providers.sec import Filing

    class Sec:
        def document_text(self, url, max_chars=30000):
            return "Item 5.02 Mr X resigned citing a conflict under the merger agreement. SIGNATURES"

    f = Filing("8-K", date(2026, 9, 18), "acc", "5.02,9.01", "a.htm", "", "https://s/a")
    cats = filing_categories(f, Database(":memory:"), Sec(), "now")
    assert cats == [("Cambios en consejo/directivos", "bajo")]
