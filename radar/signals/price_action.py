"""SENAL 5 - Movimiento inicial del precio (0-15)."""
from __future__ import annotations

from typing import Any, Mapping

from radar.models import SignalScore
from radar.signals.tiers import at_least

NAME = "price"


def score_price(
    change_pct: float | None,
    premarket_change_pct: float | None,
    price: float | None,
    day_high: float | None,
    prev_high: float | None,
    volume_acceleration: float | None,
    cfg: Mapping[str, Any],
) -> SignalScore:
    max_points = float(cfg["max"])
    parts: list[str] = []

    points = at_least(change_pct, cfg["change_tiers"])
    if change_pct is not None:
        parts.append(f"variacion {change_pct:+.1f}%")

    pm_points = at_least(premarket_change_pct, cfg["premarket_tiers"])
    if pm_points:
        points += pm_points
        parts.append(f"premarket {premarket_change_pct:+.1f}%")

    top = max(v for v in (price, day_high, 0.0) if v is not None)
    if prev_high and top > prev_high:
        points += float(cfg["breakout_prev_high"])
        parts.append(f"rompe maximo anterior {prev_high:.4g}")

    rising = (change_pct or 0) > 0 or (premarket_change_pct or 0) > 0
    if (
        rising
        and volume_acceleration is not None
        and volume_acceleration >= cfg["volume_acceleration_min_ratio"]
    ):
        points += float(cfg["volume_acceleration"])
        parts.append(f"aceleracion volumen {volume_acceleration:.1f}x")

    points = min(max_points, points)
    detail = ", ".join(parts) if parts else "Sin movimiento relevante"
    return SignalScore(NAME, points, max_points, points >= cfg["active_min"], detail)
