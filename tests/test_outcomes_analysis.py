from datetime import datetime

import pandas as pd
import pytest

from radar import analysis, outcomes
from radar.clock import ET
from radar.metrics import intraday_stats, volume_acceleration

HOLIDAYS = ["2026-11-26"]


def bars(start, n, prices, vols=None):
    idx = pd.date_range(start, periods=n, freq="5min", tz="America/New_York")
    prices = prices if isinstance(prices, list) else [prices] * n
    vols = vols or [1000] * n
    return pd.DataFrame({"Open": prices, "High": prices, "Low": prices, "Close": prices,
                         "Volume": vols}, index=idx)


class TestOutcomes:
    def test_windows_5d_skips_weekend(self):
        w = outcomes.windows(datetime(2026, 9, 24, 10, 0, tzinfo=ET), HOLIDAYS)
        assert w["5d"].date().isoformat() == "2026-10-01"

    def test_compute_max_after_alert(self):
        prices = [2.0] * 12 + [3.0] + [2.5] * 60
        b = bars("2026-09-24 10:00", len(prices), prices)
        alert = datetime(2026, 9, 24, 10, 55, tzinfo=ET)
        now = datetime(2026, 9, 24, 16, 0, tzinfo=ET)
        vals, complete = outcomes.compute(b, alert, 2.0, now, HOLIDAYS)
        assert vals["max_pct_1h"] == pytest.approx(50.0)
        assert "max_pct_1d" not in vals and not complete

    def test_base_price_from_bars_not_stale_alert_price(self):
        # Alert stored $0.20 but series is split-adjusted to $2.00 later.
        b = bars("2026-09-24 10:00", 30, [2.0] * 15 + [2.4] * 15)
        alert = datetime(2026, 9, 24, 10, 30, tzinfo=ET)
        vals, _ = outcomes.compute(b, alert, 0.20, datetime(2026, 9, 24, 15, 0, tzinfo=ET), HOLIDAYS)
        assert vals["max_pct_1h"] == pytest.approx(20.0)


class TestIntraday:
    def test_premarket_and_regular_split(self):
        pre = bars("2026-09-24 08:00", 18, 1.1, [500] * 18)
        reg = bars("2026-09-24 09:30", 12, 1.3, [2000] * 12)
        st = intraday_stats(pd.concat([pre, reg]), datetime(2026, 9, 24).date())
        assert st.premarket_volume == 9000 and st.premarket_last == 1.1
        assert st.regular_volume == 24000 and st.day_high == 1.3

    def test_zero_premarket_volume_is_unknown(self):
        pre = bars("2026-09-24 08:00", 6, 1.1, [0] * 6)
        st = intraday_stats(pre, datetime(2026, 9, 24).date())
        assert st.premarket_volume is None

    def test_volume_acceleration(self):
        prices = [1.0] * 9 + [1.1, 1.2, 1.3]
        vols = [1000] * 9 + [5000, 6000, 7000]
        assert volume_acceleration(bars("2026-09-24 09:30", 12, prices, vols)) == pytest.approx(6.0)


class TestAnalysis:
    def rows(self):
        return [
            {"ts": "2026-09-24T10:00", "ticker": "A", "level": "ALERTA MAXIMA",
             "active_signals": "reverse_split,float,volume", "max_pct_1d": 60.0, "max_pct_5d": 80.0,
             "max_pct_1h": 10.0, "max_pct_4h": 30.0},
            {"ts": "2026-09-24T10:30", "ticker": "A", "level": "ALERTA MAXIMA",
             "active_signals": "reverse_split,float,volume", "max_pct_1d": 1.0},
            {"ts": "2026-09-24T11:00", "ticker": "B", "level": "ALERTA ALTA",
             "active_signals": "volume,price,catalyst", "max_pct_1d": 5.0, "max_pct_5d": None},
        ]

    def test_dedup_and_hit_rate(self):
        rep = analysis.report(self.rows(), hit_pct=20)
        assert rep["eventos"] == 2
        assert rep["por_nivel"]["ALERTA MAXIMA"]["1d"]["acierto"] == 100.0
        assert rep["por_senal"]["Reverse split"]["activa"]["1d"]["media"] == 60.0
        assert rep["por_senal"]["Reverse split"]["inactiva"]["1d"]["media"] == 5.0
        assert "3" in rep["por_num_senales"]

    def test_format(self):
        assert "POR SENAL" in analysis.format_report(analysis.report(self.rows()))
