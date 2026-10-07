from datetime import date, datetime, timedelta

from radar.clock import ET
from radar.models import Catalyst, ReverseSplitEvent, SignalScore, Sourced, StockData
from radar.scoring import (
    LEVEL_ALTA,
    LEVEL_MAXIMA,
    LEVEL_NONE,
    LEVEL_VIGILAR,
    combine,
    level_for,
    score_stock,
)

NOW = datetime(2026, 9, 24, 10, 30, tzinfo=ET)


def sig(name, points, active):
    return SignalScore(name, points, 20, active, "")


def s(v, src="test"):
    return Sourced(v, src, "2026-09-24")


class TestLevels:
    def test_boundaries(self, scfg):
        lv = scfg["levels"]
        assert level_for(80, lv) == LEVEL_MAXIMA
        assert level_for(79, lv) == LEVEL_ALTA
        assert level_for(65, lv) == LEVEL_ALTA
        assert level_for(64, lv) == LEVEL_VIGILAR
        assert level_for(50, lv) == LEVEL_VIGILAR
        assert level_for(49, lv) == LEVEL_NONE


class TestCombine:
    def test_price_only_never_alerts(self, scfg):
        signals = (sig("reverse_split", 0, False), sig("float", 0, False),
                   sig("volume", 25, True), sig("catalyst", 0, False), sig("price", 15, True))
        r = combine(signals, scfg)
        assert r.total <= 64 and r.level != LEVEL_ALTA

    def test_two_signals_capped(self, scfg):
        signals = (sig("reverse_split", 20, True), sig("float", 20, True), sig("volume", 25, False),
                   sig("catalyst", 5, False), sig("price", 5, False))
        r = combine(signals, scfg)
        assert r.capped and r.total == 64

    def test_core_trio_bonus(self, scfg):
        signals = (sig("reverse_split", 17, True), sig("float", 16, True), sig("volume", 20, True),
                   sig("catalyst", 0, False), sig("price", 5, True))
        r = combine(signals, scfg)
        assert r.bonus == 8 and r.total == 66 and r.level == LEVEL_ALTA

    def test_core_trio_with_catalyst_bonus(self, scfg):
        signals = (sig("reverse_split", 17, True), sig("float", 16, True), sig("volume", 20, True),
                   sig("catalyst", 14, True), sig("price", 5, True))
        r = combine(signals, scfg)
        assert r.bonus == 15 and r.total == 87 and r.level == LEVEL_MAXIMA

    def test_blocked_data_capped(self, scfg):
        signals = (sig("reverse_split", 20, True), sig("float", 20, True), sig("volume", 25, True),
                   sig("catalyst", 20, True), sig("price", 15, True))
        r = combine(signals, scfg, data_blocked=True)
        assert r.total == 64 and r.capped

    def test_total_capped_at_100(self, scfg):
        signals = (sig("reverse_split", 20, True), sig("float", 20, True), sig("volume", 25, True),
                   sig("catalyst", 20, True), sig("price", 15, True))
        assert combine(signals, scfg).total == 100


class TestScoreStock:
    def full_setup(self, **overrides):
        base = dict(
            ticker="TEST", updated_at=NOW, phase="regular",
            price=s(2.4), change_pct=s(25.0), prev_high=s(2.0), day_high=s(2.5),
            float_shares=s(1_400_000), volume=s(6_000_000), avg_volume_20d=s(500_000),
            reverse_split=ReverseSplitEvent("ejecutado", date(2026, 9, 22), 20, "test"),
            catalysts=(Catalyst(NOW - timedelta(hours=2), "FDA: aprobacion/autorizacion", "alto",
                                "FDA approves", "SEC 8-K", True),),
        )
        base.update(overrides)
        return StockData(**base)

    def test_full_confluence_is_maxima(self, scfg):
        r = score_stock(self.full_setup(), NOW, scfg)
        assert r.level == LEVEL_MAXIMA and r.active_count == 5

    def test_blocked_suppresses_price_signal(self, scfg):
        r = score_stock(self.full_setup(data_blocked=True), NOW, scfg)
        assert r.points("price") <= 3  # only breakout, change ignored
        assert r.total <= 64

    def test_premarket_change_ignored_in_regular(self, scfg):
        data = self.full_setup(change_pct=s(1.0), premarket_change_pct=s(40.0), day_high=None, prev_high=None)
        assert score_stock(data, NOW, scfg).points("price") == 0
