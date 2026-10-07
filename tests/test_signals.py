from datetime import date, datetime, timedelta

from radar.clock import ET
from radar.models import Catalyst, ReverseSplitEvent, Sourced
from radar.signals import (
    score_catalyst,
    score_float,
    score_price,
    score_reverse_split,
    score_volume,
)
from radar.signals.tiers import at_least, at_most, below

TODAY = date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 10, 30, tzinfo=ET)


def rs(status, days_ago, ratio):
    return ReverseSplitEvent(status, TODAY - timedelta(days=days_ago), ratio, "test")


class TestTiers:
    def test_at_least(self):
        assert at_least(10, [[10, 25], [5, 20]]) == 25
        assert at_least(7, [[10, 25], [5, 20]]) == 20
        assert at_least(1, [[10, 25], [5, 20]]) == 0
        assert at_least(None, [[1, 1]]) == 0

    def test_below_and_at_most(self):
        assert below(900_000, [[1e6, 20], [5e6, 16]]) == 20
        assert below(1e6, [[1e6, 20], [5e6, 16]]) == 16
        assert at_most(5, [[5, 20], [15, 17]]) == 20
        assert at_most(200, [[5, 20], [180, 3]]) == 0


class TestReverseSplit:
    def test_none(self, scfg):
        s = score_reverse_split(None, TODAY, scfg["reverse_split"])
        assert s.points == 0 and not s.active

    def test_recent_executed_big_ratio_is_max(self, scfg):
        s = score_reverse_split(rs("ejecutado", 2, 20), TODAY, scfg["reverse_split"])
        assert s.points == 20 and s.active

    def test_small_ratio_scores_less(self, scfg):
        big = score_reverse_split(rs("ejecutado", 2, 20), TODAY, scfg["reverse_split"])
        small = score_reverse_split(rs("ejecutado", 2, 3), TODAY, scfg["reverse_split"])
        assert small.points < big.points

    def test_recency_decays(self, scfg):
        pts = [score_reverse_split(rs("ejecutado", d, 10), TODAY, scfg["reverse_split"]).points
               for d in (3, 20, 50, 100, 170)]
        assert pts == sorted(pts, reverse=True) and pts[-1] > 0

    def test_announced_below_executed(self, scfg):
        ann = score_reverse_split(rs("anunciado", 3, 10), TODAY, scfg["reverse_split"])
        exe = score_reverse_split(rs("ejecutado", 3, 10), TODAY, scfg["reverse_split"])
        prop = score_reverse_split(rs("propuesto", 3, 10), TODAY, scfg["reverse_split"])
        assert exe.points > ann.points > prop.points > 0

    def test_unknown_ratio_uses_factor(self, scfg):
        s = score_reverse_split(rs("ejecutado", 2, None), TODAY, scfg["reverse_split"])
        assert s.points == 16


class TestFloat:
    def test_tiers(self, scfg):
        f = scfg["float"]
        assert score_float(Sourced(800_000, "x", "d"), f).points == 20
        assert score_float(Sourced(4_000_000, "x", "d"), f).points == 16
        assert score_float(Sourced(9_000_000, "x", "d"), f).points == 12
        assert score_float(Sourced(80_000_000, "x", "d"), f).points == 0

    def test_missing_is_nd(self, scfg):
        s = score_float(None, scfg["float"])
        assert s.points == 0 and "N/D" in s.detail

    def test_detail_shows_source_and_date(self, scfg):
        s = score_float(Sourced(1_500_000, "SEC 10-Q", "2026-08-17"), scfg["float"])
        assert "SEC 10-Q" in s.detail and "2026-08-17" in s.detail


class TestVolume:
    def test_thresholds(self, scfg):
        v = scfg["volume"]
        assert score_volume(3.0, None, None, v).points == 14
        assert score_volume(5.0, None, None, v).points == 20
        assert score_volume(10.0, None, None, v).points == 25
        assert score_volume(1.5, None, None, v).points == 0

    def test_premarket_bonus_capped(self, scfg):
        v = scfg["volume"]
        assert score_volume(3.0, 600, 1000, v).points == 19
        assert score_volume(12.0, 600, 1000, v).points == 25

    def test_labels(self, scfg):
        assert "muy fuerte" in score_volume(11, None, None, scfg["volume"]).detail


class TestCatalyst:
    def cat(self, impact, official, hours_ago, category="Contrato"):
        return Catalyst(NOW - timedelta(hours=hours_ago), category, impact, "t", "SEC 8-K", official)

    def test_none(self, scfg):
        assert score_catalyst([], NOW, scfg["catalyst"]).points == 0

    def test_official_fresh_high_impact(self, scfg):
        s = score_catalyst([self.cat("alto", True, 3)], NOW, scfg["catalyst"])
        assert s.points == 20 and s.active

    def test_non_official_scores_less(self, scfg):
        off = score_catalyst([self.cat("alto", True, 50)], NOW, scfg["catalyst"]).points
        non = score_catalyst([self.cat("alto", False, 50)], NOW, scfg["catalyst"]).points
        assert off > non

    def test_extra_categories_add(self, scfg):
        one = score_catalyst([self.cat("medio", False, 50)], NOW, scfg["catalyst"]).points
        two = score_catalyst(
            [self.cat("medio", False, 50), self.cat("medio", False, 60, "Patente")], NOW, scfg["catalyst"]
        ).points
        assert two == one + 2


class TestPrice:
    def test_change_tiers(self, scfg):
        p = scfg["price"]
        assert score_price(12, None, 1, 1, None, None, p).points == 5
        assert score_price(35, None, 1, 1, None, None, p).points == 9
        assert score_price(2, None, 1, 1, None, None, p).points == 0

    def test_breakout_and_acceleration(self, scfg):
        s = score_price(25, None, 2.2, 2.3, 2.0, 3.0, scfg["price"])
        assert s.points == 7 + 3 + 2

    def test_no_acceleration_when_falling(self, scfg):
        s = score_price(-5, None, 1.0, 1.1, 2.0, 5.0, scfg["price"])
        assert s.points == 0

    def test_premarket(self, scfg):
        assert score_price(None, 15, 1, None, None, None, scfg["price"]).points == 3

    def test_capped(self, scfg):
        assert score_price(80, 40, 5, 6, 1, 9, scfg["price"]).points == 15
