from radar.candidates import select_candidates
from radar.db import Database
from radar.providers.nasdaq import ScreenerRow

UCFG = {"min_price": 0.05, "max_price": 20, "max_market_cap": 500e6, "candidate_min_rvol": 2.0,
        "candidate_min_change_pct": 8.0, "max_candidates_per_cycle": 3}


def row(t, price=2.0, change=0.0, vol=100_000, cap=10e6):
    return ScreenerRow(t, t, price, change, None, vol, cap)


class TestCandidates:
    def test_filters_and_ranking(self):
        rows = [row("VOL", vol=1_000_000), row("MOVE", change=12), row("FLAT"),
                row("BIG", cap=2e9, vol=5_000_000), row("PRICEY", price=50, change=30)]
        stats = {t: {"avg_volume_20d": 100_000} for t in ("VOL", "MOVE", "FLAT", "BIG", "PRICEY")}
        assert select_candidates(rows, stats, [], [], UCFG) == ["VOL", "MOVE"]

    def test_reverse_split_relaxed_threshold(self):
        rows = [row("RS", change=4)]
        assert select_candidates(rows, {}, [], ["RS"], UCFG) == ["RS"]
        assert select_candidates(rows, {}, [], [], UCFG) == []

    def test_watchlist_first_and_cap(self):
        rows = [row(t, change=50) for t in ("A", "B", "C", "D")]
        out = select_candidates(rows, {}, ["zz"], [], UCFG)
        assert out[0] == "ZZ" and len(out) == 3


class TestDatabase:
    def test_alert_roundtrip_and_outcomes(self):
        db = Database(":memory:")
        aid = db.insert_alert({"ts": "2026-09-24T10:00:00", "ticker": "A", "kind": "INICIAL",
                               "notified": 1, "level": "ALERTA ALTA", "total": 70, "price": 2.0})
        assert db.last_notified_alert("A")["total"] == 70
        db.update_outcomes(aid, {"max_pct_1h": 12.5}, False, "2026-09-24T11:00:00")
        assert db.pending_outcomes()[0]["max_pct_1h"] == 12.5
        db.update_outcomes(aid, {}, True, "2026-10-02T11:00:00")
        assert db.pending_outcomes() == []

    def test_watch_event_once_per_day(self):
        db = Database(":memory:")
        assert not db.has_watch_event("A", "2026-09-24")
        db.insert_alert({"ts": "2026-09-24T10:00:00", "ticker": "A", "kind": "VIGILAR", "notified": 0})
        assert db.has_watch_event("A", "2026-09-24")

    def test_kv(self):
        db = Database(":memory:")
        db.set_kv("k", "v")
        assert db.get_kv("k") == "v" and db.get_kv("x") is None


def test_non_common_shares_excluded():
    from radar.candidates import in_universe
    pref = ScreenerRow("WHLRP", "Wheeler REIT Series B Convertible Preferred Stock", 9.4, 0, None, 1, 1e6)
    warr = ScreenerRow("ABCW", "Abc Corp Warrant", 0.2, 0, None, 1, 1e6)
    common = ScreenerRow("ABC", "Abc Corp Common Stock", 2.0, 0, None, 1, 1e6)
    assert not in_universe(pref, UCFG) and not in_universe(warr, UCFG) and in_universe(common, UCFG)


def test_compact_keeps_history_small():
    """radar.db reached 104.7 MB (GitHub limit 100 MB): SIN ALERTA payloads were 98 %."""
    db = Database(":memory:")
    cid = db.insert_cycle(__import__("datetime").datetime(2026, 11, 2, 10), "regular")
    rows = [
        {"cycle_id": cid, "ts": "2026-11-02T10:00:00-05:00", "ticker": "A", "level": "SIN ALERTA",
         "total": 30, "payload": "x" * 5000},
        {"cycle_id": cid, "ts": "2026-11-02T10:00:00-05:00", "ticker": "B", "level": "VIGILAR",
         "total": 55, "payload": "y" * 5000},
        {"cycle_id": cid, "ts": "2020-01-01T10:00:00-05:00", "ticker": "OLD", "level": "VIGILAR",
         "total": 55, "payload": "z"},
    ]
    db.insert_snapshots(rows)
    db.upsert_daily_stats([{"session_date": f"2026-10-{d:02d}", "ticker": "A", "avg_volume_20d": 1.0}
                           for d in range(1, 21)])
    out = db.compact(keep_daily_sessions=7)
    snaps = {r["ticker"]: r for r in db.query("SELECT ticker, payload FROM snapshots")}
    assert snaps["A"]["payload"] is None and snaps["B"]["payload"] == "y" * 5000
    assert "OLD" not in snaps
    assert len(db.query("SELECT DISTINCT session_date FROM daily_stats")) == 7
    assert out["payloads_vaciados"] == 1 and out["snapshots_borrados"] == 1
