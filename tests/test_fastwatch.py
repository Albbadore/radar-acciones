from datetime import datetime, timedelta

from radar.alerts import time_lines
from radar.clock import ET, broker_open
from radar.db import Database
from radar.fastwatch import FastWatcher, Tick, detect_start
from radar.providers.nasdaq import LiveQuote, parse_live

FCFG = {"window_minutes": 10, "min_gain_pct": 8, "min_window_volume_pct": 5, "cooldown_minutes": 60}
T0 = datetime(2026, 9, 23, 16, 0, tzinfo=ET)


def ticks(prices, vols, step_s=90):
    return [Tick(T0 + timedelta(seconds=i * step_s), p, v) for i, (p, v) in enumerate(zip(prices, vols))]


class TestDetectStart:
    def test_gctk_like_start(self):
        # 2.03 -> 2.40 in 6 minutes with 400K shares (avg 1.3M -> 30 %)
        sig = detect_start(ticks([2.03, 2.05, 2.15, 2.28, 2.40], [0, 20e3, 90e3, 250e3, 400e3]), 1.3e6, FCFG)
        assert sig is not None and sig.gain_pct == 18.2 and sig.minutes == 6

    def test_thin_volume_ignored(self):
        assert detect_start(ticks([2.0, 2.2], [0, 1000]), 1.3e6, FCFG) is None

    def test_small_move_ignored(self):
        assert detect_start(ticks([2.0, 2.1], [0, 1e6]), 1.3e6, FCFG) is None

    def test_move_outside_window_ignored(self):
        old = ticks([2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.3], [0] * 8 + [9e6], step_s=120)
        # base 2.0 is only 2 min before -> still inside; make it outside:
        series = [Tick(T0, 1.0, 0), Tick(T0 + timedelta(minutes=30), 2.0, 5e5), Tick(T0 + timedelta(minutes=31), 2.05, 6e5)]
        assert detect_start(series, 1e6, FCFG) is None
        assert detect_start(old, 1e6, FCFG) is not None

    def test_needs_two_ticks(self):
        assert detect_start(ticks([2.0], [0]), 1e6, FCFG) is None


class TestFastWatcher:
    def setup_db(self, level="ALERTA MAXIMA", blocked=0):
        db = Database(":memory:")
        cid = db.insert_cycle(T0, "regular")
        db.insert_snapshots([{"cycle_id": cid, "ts": "2026-09-23T15:55:00-04:00", "ticker": "GCTK",
                              "name": "GlucoTrack", "total": 88, "level": level, "avg_volume_20d": 1.3e6,
                              "data_blocked": blocked}])
        return db

    def test_alerts_once_and_records(self, cfg):
        db, sent = self.setup_db(), []
        prices = iter([(2.03, 0), (2.10, 1e5), (2.40, 5e5), (2.60, 8e5)])

        def quote(t):
            p, v = next(prices)
            return LiveQuote(p, None, v, "", "Nasdaq tiempo real")

        w = FastWatcher(db, cfg, quote_fn=quote, send=lambda text, c: sent.append(text))
        results = [w.poll(T0 + timedelta(seconds=90 * i)) for i in range(4)]
        assert results == [[], [], ["GCTK"], []]          # cooldown blocks the 4th
        assert "ARRANQUE" in sent[0] and "GCTK" in sent[0] and "No constituye" in sent[0]
        row = db.alerts()[0]
        assert row["kind"] == "ARRANQUE" and row["notified"] == 1 and row["price"] == 2.40

    def test_only_hot_tickers(self, cfg):
        db = self.setup_db(level="VIGILAR")
        w = FastWatcher(db, cfg, quote_fn=lambda t: (_ for _ in ()).throw(AssertionError("no poll")))
        assert w.hot_list(T0) == {} and w.poll(T0) == []

    def test_quote_errors_ignored(self, cfg):
        def boom(t):
            raise ConnectionError("down")
        assert FastWatcher(self.setup_db(), cfg, quote_fn=boom).poll(T0) == []


class TestTimes:
    def test_broker_hours(self):
        assert broker_open(datetime(2026, 9, 23, 16, 5, tzinfo=ET))       # 22:05 Spain
        assert not broker_open(datetime(2026, 9, 23, 17, 30, tzinfo=ET))  # 23:30 Spain
        assert not broker_open(datetime(2026, 9, 26, 10, 0, tzinfo=ET))   # Saturday

    def test_time_lines(self):
        lines = time_lines(datetime(2026, 9, 23, 16, 5, tzinfo=ET))
        assert lines[0].startswith("Hora: 16:05 Nueva York / 22:05 Espana") and "ABIERTO" in lines[1]
        assert "CERRADO" in time_lines(datetime(2026, 9, 23, 18, 0, tzinfo=ET))[1]


def test_parse_live():
    q = parse_live({"primaryData": {"lastSalePrice": "$4.50", "percentageChange": "+121.67%",
                                    "volume": "8,222,161.548039", "isRealTime": True,
                                    "lastTradeTimestamp": "Sep 24, 2026 5:04 AM ET"}})
    assert (q.price, q.change_pct, round(q.volume), q.source) == (4.5, 121.67, 8222162, "Nasdaq tiempo real")
    assert parse_live({"primaryData": {"lastSalePrice": ""}}) is None and parse_live(None) is None


class TestRunSlices:
    """Cloud mode: a slice stops by itself (GitHub kills jobs after 6 h)."""

    def run(self, monkeypatch, cfg, phase, **kw):
        import argparse
        from radar import __main__ as cli
        calls = []

        class Svc:
            db = Database(":memory:")

        monkeypatch.setattr(cli, "build_services", lambda c: Svc())
        monkeypatch.setattr(cli, "market_phase", lambda now, s: phase)
        monkeypatch.setattr(cli, "run_cycle", lambda svc, now, ph: calls.append(ph) or {})
        monkeypatch.setattr(cli.outcomes, "update_all", lambda *a: 0)
        monkeypatch.setattr(cli, "_fast_watch_until", lambda *a: None)
        cli.cmd_run(cfg, argparse.Namespace(**kw))
        return calls

    def test_stops_after_max_minutes(self, monkeypatch, cfg):
        calls = self.run(monkeypatch, cfg, "regular", max_minutes=0.0005, exit_when_closed=True)
        assert calls  # it returned: the slice ended by itself

    def test_exits_when_market_closed(self, monkeypatch, cfg):
        assert self.run(monkeypatch, cfg, "cerrado", max_minutes=60, exit_when_closed=True) == []
