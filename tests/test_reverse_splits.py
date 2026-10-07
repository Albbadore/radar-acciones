from datetime import date

from radar.reverse_splits import resolve_event

TODAY = date(2026, 9, 24)


def sec_row(form="8-K", filed="2026-09-18", ratio=20, eff=None):
    return {"form": form, "filed_date": filed, "ratio": ratio, "ratio_text": f"1:{ratio}",
            "effective_date": eff, "url": "https://sec.gov/x"}


def test_yahoo_executed_wins_and_links_sec():
    ev = resolve_event((date(2026, 9, 22), 0.05), [sec_row()], TODAY)
    assert ev.status == "ejecutado" and ev.ratio == 20 and ev.event_date == date(2026, 9, 22)
    assert "SEC" in ev.source and ev.url


def test_sec_announced_future():
    ev = resolve_event(None, [sec_row(eff="2026-09-30")], TODAY)
    assert ev.status == "anunciado" and ev.event_date == date(2026, 9, 18)


def test_sec_effective_passed_counts_as_executed():
    ev = resolve_event(None, [sec_row(eff="2026-09-23")], TODAY)
    assert ev.status == "ejecutado" and ev.event_date == date(2026, 9, 23)


def test_proxy_is_proposal():
    ev = resolve_event(None, [sec_row(form="DEF 14A")], TODAY)
    assert ev.status == "propuesto"


def test_forward_split_ignored():
    assert resolve_event((date(2026, 9, 1), 2.0), [], TODAY) is None


def test_old_split_ignored():
    assert resolve_event((date(2025, 1, 1), 0.1), [], TODAY) is None


class TestRefresh:
    def test_refresh_parses_and_filters(self, cfg):
        from radar.db import Database
        from radar.providers.sec import SearchHit
        from radar.reverse_splits import refresh_filings, watchlist

        class FakeSec:
            def full_text_search(self, phrase, forms, start, end):
                if phrase != "reverse stock split":
                    return [], 0
                return [
                    SearchHit("a1", 1, ("ACME",), "Acme", "8-K", date(2026, 9, 20), "3.03",
                              ("https://s/main", "https://s/ex99")),
                    SearchHit("a2", 2, ("BOIL",), "Boiler", "8-K", date(2026, 9, 21), "8.01",
                              ("https://s/boiler",)),
                    SearchHit("a3", 3, (), "Private", "8-K", date(2026, 9, 21), "", ("https://s/p",)),
                    SearchHit("a4", 4, ("RNG",), "Range", "8-K", date(2026, 9, 21), "", ("https://s/rng",)),
                ], 0

            def document_text(self, url):
                return {
                    "https://s/main": "cover page only",
                    "https://s/ex99": "Acme announces 1-for-25 reverse stock split; shares begin trading "
                                      "on a split-adjusted basis on September 29, 2026.",
                    "https://s/boiler": "warrants adjust for any reverse split or dividend",
                    "https://s/rng": "board approved a reverse stock split between 1-for-5 and 1-for-30",
                }[url]

        db = Database(":memory:")
        assert refresh_filings(db, FakeSec(), TODAY, cfg) == 3
        rows = {r["accession"]: r for r in db.rs_filings_since("2026-01-01")}
        assert rows["a1"]["ratio"] == 25 and rows["a1"]["url"] == "https://s/ex99"
        assert rows["a1"]["effective_date"] == "2026-09-29"
        assert rows["a4"]["ratio"] is None and "rango" in rows["a4"]["ratio_text"]
        assert set(watchlist(db, TODAY)) == {"ACME", "RNG"}
        assert db.get_kv("rs_refresh_date") == TODAY.isoformat()
