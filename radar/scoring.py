"""Combine the five signals into a 0-100 score and a level."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from radar.clock import PHASE_PREMARKET
from radar.models import ScoreResult, SignalScore, StockData, val
from radar.signals import (
    score_catalyst,
    score_float,
    score_price,
    score_reverse_split,
    score_volume,
)

LEVEL_MAXIMA = "ALERTA MAXIMA"
LEVEL_ALTA = "ALERTA ALTA"
LEVEL_VIGILAR = "VIGILAR"
LEVEL_NONE = "SIN ALERTA"

LEVEL_RANK = {LEVEL_NONE: 0, LEVEL_VIGILAR: 1, LEVEL_ALTA: 2, LEVEL_MAXIMA: 3}

CORE_TRIO = ("reverse_split", "float", "volume")

SIGNAL_LABELS = {
    "reverse_split": "Reverse split",
    "float": "Float pequeno",
    "volume": "Volumen anormal",
    "catalyst": "Catalizador",
    "price": "Movimiento de precio",
}


def level_for(total: float, levels: Mapping[str, float]) -> str:
    if total >= levels["maxima"]:
        return LEVEL_MAXIMA
    if total >= levels["alta"]:
        return LEVEL_ALTA
    if total >= levels["vigilar"]:
        return LEVEL_VIGILAR
    return LEVEL_NONE


def compute_signals(data: StockData, now: datetime, cfg: Mapping[str, Any]) -> tuple[SignalScore, ...]:
    # During the regular session the premarket move is already inside change_pct.
    premarket = val(data.premarket_change_pct) if data.phase == PHASE_PREMARKET else None
    price_signal = score_price(
        None if data.data_blocked else val(data.change_pct),
        None if data.data_blocked else premarket,
        val(data.price),
        val(data.day_high),
        val(data.prev_high),
        val(data.volume_acceleration),
        cfg["price"],
    )
    return (
        score_reverse_split(data.reverse_split, now.date(), cfg["reverse_split"]),
        score_float(data.effective_float, cfg["float"]),
        score_volume(data.rvol, val(data.premarket_volume), val(data.avg_volume_20d), cfg["volume"],
                     None if data.data_blocked else val(data.change_pct)),
        score_catalyst(data.catalysts, now, cfg["catalyst"]),
        price_signal,
    )


def late_adjustment(run_up_pct: float | None, cfg: Mapping[str, Any]) -> tuple[float, bool, str]:
    """(penalty, cap, note) when most of the move has probably already happened."""
    if run_up_pct is None:
        return 0.0, False, ""
    if run_up_pct >= cfg["late_from_pct"]:
        return 0.0, True, (
            f"TARDE: ya sube {run_up_pct:+.0f}% desde el minimo de 5 sesiones; "
            f"limitada a {LEVEL_VIGILAR}"
        )
    if run_up_pct >= cfg["advanced_from_pct"]:
        return float(cfg["advanced_penalty"]), False, (
            f"Movimiento avanzado: {run_up_pct:+.0f}% desde el minimo de 5 sesiones "
            f"(-{cfg['advanced_penalty']} puntos)"
        )
    return 0.0, False, ""


def combine(
    signals: tuple[SignalScore, ...],
    cfg: Mapping[str, Any],
    data_blocked: bool = False,
    run_up_pct: float | None = None,
) -> ScoreResult:
    conf = cfg["confluence"]
    active = tuple(s.name for s in signals if s.active)
    raw_total = sum(s.points for s in signals)

    bonus = 0.0
    if all(name in active for name in CORE_TRIO):
        bonus = float(
            conf["core_trio_catalyst_bonus"] if "catalyst" in active else conf["core_trio_bonus"]
        )

    penalty, late_cap, late_note = late_adjustment(run_up_pct, cfg["late"])
    total = max(0.0, min(100.0, raw_total + bonus - penalty))
    capped = False
    if len(active) < conf["min_active_signals"] or data_blocked or late_cap:
        if total > conf["cap_below_min"]:
            total = float(conf["cap_below_min"])
            capped = True

    total_int = int(round(total))
    return ScoreResult(
        signals=signals,
        raw_total=round(raw_total, 1),
        bonus=bonus,
        capped=capped,
        total=total_int,
        level=level_for(total_int, cfg["levels"]),
        active_names=active,
        penalty=penalty,
        late_note=late_note,
    )


def score_stock(data: StockData, now: datetime, cfg: Mapping[str, Any]) -> ScoreResult:
    return combine(compute_signals(data, now, cfg), cfg, data.data_blocked, val(data.run_up_pct))
