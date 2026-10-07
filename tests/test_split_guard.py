from datetime import date

import pandas as pd
import pytest

from radar.metrics import daily_stats
from radar.models import ReverseSplitEvent, Sourced
from radar.split_guard import (
    check_daily_history,
    check_float,
    check_live_change,
    looks_unadjusted,
    nearest_ratio,
    reconcile_prev_close,
)

TODAY = date(2026, 9, 24)
SPLIT_DAY = date(2026, 9, 22)
EVENT = ReverseSplitEvent("ejecutado", SPLIT_DAY, 10, "test")


def frame(closes, volumes, start="2026-08-20"):
    idx = pd.bdate_range(start, periods=len(closes), tz="America/New_York")
    return pd.DataFrame({
        "Open": closes, "High": [c * 1.05 for c in closes], "Low": [c * 0.95 for c in closes],
        "Close": closes, "Volume": volumes,
    }, index=idx)


def unadjusted_history():
    """20 sessions at $0.50 with 1M volume, then 1:10 split -> $5 with 100K."""
    n_pre = 22
    closes = [0.5] * n_pre + [5.0, 5.1]
    vols = [1_000_000] * n_pre + [150_000, 120_000]
    df = frame(closes, vols)
    return df, df.index[n_pre].date()


class TestHelpers:
    def test_nearest_ratio(self):
        assert nearest_ratio(9.8) == 10
        assert nearest_ratio(20.5) == 20
        assert nearest_ratio(1.3) is None

    def test_looks_unadjusted(self):
        assert looks_unadjusted(10.3, 10)
        assert looks_unadjusted(6.0, 10)
        assert not looks_unadjusted(1.4, 10)   # genuine +40 % move
        assert not looks_unadjusted(2.5, 10)   # genuine +150 % is still closer to 1


class TestDailyHistory:
    def test_known_split_unadjusted_is_fixed(self):
        df, split_day = unadjusted_history()
        event = ReverseSplitEvent("ejecutado", split_day, 10, "test")
        check = check_daily_history(df, event)
        assert check.adjusted and not check.suspicious
        stats = daily_stats(check.frame, TODAY + pd.Timedelta(days=30))
        # Pre-split volume divided by 10 -> average close to 100K, not ~1M.
        assert stats.avg_volume_20d < 200_000

    def test_without_guard_average_would_be_inflated(self):
        df, _ = unadjusted_history()
        stats = daily_stats(df, TODAY + pd.Timedelta(days=30))
        assert stats.avg_volume_20d > 800_000

    def test_already_adjusted_left_alone(self):
        df = frame([5.0] * 24, [100_000] * 24)
        event = ReverseSplitEvent("ejecutado", df.index[22].date(), 10, "test")
        check = check_daily_history(df, event)
        assert not check.adjusted
        pd.testing.assert_frame_equal(check.frame, df, check_dtype=False)

    def test_prices_adjusted_but_volume_raw(self):
        closes = [5.0] * 24
        vols = [1_000_000] * 22 + [100_000, 90_000]
        df = frame(closes, vols)
        event = ReverseSplitEvent("ejecutado", df.index[22].date(), 10, "test")
        check = check_daily_history(df, event)
        assert check.adjusted
        assert check.frame["Volume"].iloc[0] == pytest.approx(100_000)

    def test_unknown_split_flagged(self):
        df, _ = unadjusted_history()
        check = check_daily_history(df, None)
        assert check.adjusted and check.suspicious and check.ratio == 10

    def test_nan_rows_ignored(self):
        df, split_day = unadjusted_history()
        df.iloc[5] = float("nan")
        check = check_daily_history(df, ReverseSplitEvent("ejecutado", split_day, 10, "t"))
        assert check.adjusted


class TestLiveChange:
    def test_fake_gain_after_split_corrected(self):
        # Yesterday $0.52 unadjusted, today $5.70 after 1:10 -> real change ~ +9.6 %
        chk = check_live_change(5.70, 0.52, EVENT, TODAY)
        assert chk.adjusted and not chk.suspicious
        assert chk.change_pct == pytest.approx(9.6, abs=0.1)

    def test_genuine_gain_kept(self):
        chk = check_live_change(6.5, 5.0, EVENT, TODAY)
        assert not chk.adjusted and chk.change_pct == pytest.approx(30.0)

    def test_unknown_jump_blocked(self):
        chk = check_live_change(10.0, 0.5, None, TODAY)
        assert chk.suspicious

    def test_normal_move_without_event(self):
        chk = check_live_change(1.3, 1.0, None, TODAY)
        assert not chk.suspicious and chk.change_pct == pytest.approx(30.0)


class TestReconcile:
    def test_sources_disagree_by_ratio(self):
        chosen, note = reconcile_prev_close([Sourced(0.52, "A"), Sourced(5.2, "B")])
        assert chosen.source == "B" and "1:10" in note

    def test_sources_agree(self):
        chosen, note = reconcile_prev_close([Sourced(5.2, "A"), Sourced(5.25, "B")])
        assert chosen.source == "A" and note == ""

    def test_empty(self):
        assert reconcile_prev_close([None]) == (None, "")


class TestFloat:
    def test_float_pre_split_is_divided(self):
        f, note = check_float(Sourced(20_000_000, "Yahoo"), Sourced(2_100_000, "Yahoo"), EVENT, TODAY)
        assert f.value == 2_000_000 and note

    def test_float_above_outstanding_uses_outstanding(self):
        f, note = check_float(Sourced(3_000_000, "Yahoo"), Sourced(2_000_000, "SEC"), None, TODAY)
        assert f.source == "SEC" and note

    def test_consistent_float_kept(self):
        f, note = check_float(Sourced(1_100_000, "Yahoo"), Sourced(2_300_000, "Yahoo"), EVENT, TODAY)
        assert f.value == 1_100_000 and note == ""


class TestIslands:
    """VWAV 2026-09: Yahoo left 3 sessions unadjusted between adjusted data."""

    def vwav(self):
        idx = pd.to_datetime(["2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17",
                              "2026-09-18", "2026-09-21", "2026-09-23"]).tz_localize("America/New_York")
        o = [9.66, 0.404, 0.42, 0.594, 7.46, 6.06, 5.28, 6.09]
        c = [9.34, 0.453, 0.532, 0.377, 7.32, 5.26, 6.20, 5.72]
        lo = [8.90, 0.404, 0.42, 0.36, 7.20, 5.20, 5.26, 4.53]
        v = [53055, 729300, 4536500, 12668900, 129190, 275930, 428875, 336900]
        return pd.DataFrame({"Open": o, "High": c, "Low": lo, "Close": c, "Volume": v}, index=idx)

    def test_island_rescaled_with_known_split(self):
        chk = check_daily_history(self.vwav(), ReverseSplitEvent("ejecutado", date(2026, 9, 22), 20, "t"))
        assert chk.adjusted and not chk.suspicious
        assert chk.frame["Close"].iloc[3] == pytest.approx(7.54)      # 0.377 x 20
        assert chk.frame["Close"].iloc[0] == pytest.approx(9.34)      # outside island untouched
        assert chk.frame["Volume"].iloc[3] == pytest.approx(633_445)  # 12.67M / 20
        assert chk.frame["Low"].min() > 4

    def test_island_without_event_is_suspicious(self):
        chk = check_daily_history(self.vwav(), None)
        assert chk.adjusted and chk.suspicious


def test_known_ratio_preferred_over_closer_blind_ratio():
    # NFE 1:50: last pre-split close 0.33, first open 13.69 (x41, closer to 40).
    idx = pd.bdate_range("2026-09-08", periods=6, tz="America/New_York")
    closes = [0.258, 0.273, 0.33, 12.77, 12.51, 12.41]
    opens = [0.28, 0.26, 0.33, 13.69, 12.8, 12.63]
    df = pd.DataFrame({"Open": opens, "High": closes, "Low": closes, "Close": closes,
                       "Volume": [2e6, 5e6, 1e6, 5e5, 4e5, 4e5]}, index=idx)
    chk = check_daily_history(df, ReverseSplitEvent("ejecutado", date(2026, 9, 14), 50, "t"))
    assert chk.adjusted and not chk.suspicious and chk.ratio == 50
