from datetime import date, datetime, timedelta

from radar.alerts import (
    DISCLAIMER,
    KIND_ESCALATION,
    KIND_INITIAL,
    KIND_MAJOR_CHANGE,
    KIND_UPDATE,
    AlertState,
    decide_alert,
    format_alert,
)
from radar.clock import ET
from radar.models import ReverseSplitEvent, ScoreResult, SignalScore, Sourced, StockData
from radar.scoring import LEVEL_ALTA, LEVEL_MAXIMA, LEVEL_VIGILAR

NOW = datetime(2026, 9, 24, 10, 30, tzinfo=ET)


def result(total, level):
    sigs = tuple(SignalScore(n, 10, 20, True, "d") for n in
                 ("reverse_split", "float", "volume", "catalyst", "price"))
    return ScoreResult(sigs, 50, 0, False, total, level, tuple(s.name for s in sigs))


def state(minutes_ago, level=LEVEL_ALTA, score=70, price=2.0):
    return AlertState(NOW - timedelta(minutes=minutes_ago), level, score, price)


class TestDecide:
    def test_below_threshold(self, cfg):
        d = decide_alert(None, result(60, LEVEL_VIGILAR), 2.0, NOW, cfg["alerts"])
        assert not d.notify

    def test_first_alert(self, cfg):
        d = decide_alert(None, result(70, LEVEL_ALTA), 2.0, NOW, cfg["alerts"])
        assert d.notify and d.kind == KIND_INITIAL

    def test_from_vigilar_is_escalation(self, cfg):
        d = decide_alert(None, result(70, LEVEL_ALTA), 2.0, NOW, cfg["alerts"], previous_level=LEVEL_VIGILAR)
        assert d.notify and d.kind == KIND_ESCALATION

    def test_alta_to_maxima_within_cooldown(self, cfg):
        d = decide_alert(state(5), result(82, LEVEL_MAXIMA), 2.1, NOW, cfg["alerts"])
        assert d.notify and d.kind == KIND_ESCALATION

    def test_cooldown_blocks_repeat(self, cfg):
        d = decide_alert(state(10), result(72, LEVEL_ALTA), 2.1, NOW, cfg["alerts"])
        assert not d.notify

    def test_major_change_within_cooldown(self, cfg):
        d = decide_alert(state(10), result(70, LEVEL_ALTA), 2.5, NOW, cfg["alerts"])
        assert d.notify and d.kind == KIND_MAJOR_CHANGE

    def test_after_cooldown_needs_change(self, cfg):
        same = decide_alert(state(40), result(71, LEVEL_ALTA), 2.05, NOW, cfg["alerts"])
        more = decide_alert(state(40), result(76, LEVEL_ALTA), 2.05, NOW, cfg["alerts"])
        assert not same.notify
        assert more.notify and more.kind == KIND_UPDATE

    def test_new_day_resets(self, cfg):
        yesterday = AlertState(NOW - timedelta(days=1), LEVEL_MAXIMA, 90, 2.0)
        d = decide_alert(yesterday, result(70, LEVEL_ALTA), 2.0, NOW, cfg["alerts"])
        assert d.notify and d.kind == KIND_INITIAL

    def test_blocked_data_never_alerts(self, cfg):
        d = decide_alert(None, result(90, LEVEL_MAXIMA), 2.0, NOW, cfg["alerts"], data_blocked=True)
        assert not d.notify


def test_format_contains_required_fields():
    data = StockData(
        ticker="VWAV", updated_at=NOW, phase="regular", name=Sourced("VisionWave", "Yahoo"),
        price=Sourced(5.72, "Nasdaq"), change_pct=Sourced(12.3, "calc"),
        volume=Sourced(3_000_000, "Nasdaq", "2026-09-24"), avg_volume_20d=Sourced(300_000, "Yahoo", "2026-09-23"),
        float_shares=Sourced(1_100_000, "Yahoo", "2026-09-24"),
        reverse_split=ReverseSplitEvent("ejecutado", date(2026, 9, 22), 20, "Yahoo"),
    )
    text = format_alert(data, result(85, LEVEL_MAXIMA), KIND_INITIAL, "primera")
    for field in ("Ticker:", "Precio:", "Variacion:", "Volumen:", "Volumen medio 20 dias:",
                  "Ratio de volumen: 10.0x", "Float:", "Reverse split: ejecutado 1:20",
                  "Fecha del reverse split: 2026-09-22", "Noticias recientes:", "Puntuacion: 85",
                  "Senales activadas", DISCLAIMER, "POSIBLE MOVIMIENTO ESPECULATIVO"):
        assert field in text
    assert "N/D" in text  # no news -> N/D, never invented
