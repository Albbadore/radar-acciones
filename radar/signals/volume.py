"""SENAL 3 - Volumen anormal (0-25)."""
from __future__ import annotations

from typing import Any, Mapping

from radar.models import SignalScore
from radar.signals.tiers import at_least

NAME = "volume"


def volume_label(rvol: float | None) -> str:
    if rvol is None:
        return "N/D"
    if rvol >= 10:
        return "senal muy fuerte"
    if rvol >= 5:
        return "senal fuerte"
    if rvol >= 3:
        return "senal"
    return "normal"


def score_volume(
    rvol: float | None,
    premarket_volume: float | None,
    avg_volume_20d: float | None,
    cfg: Mapping[str, Any],
    change_pct: float | None = None,
) -> SignalScore:
    max_points = float(cfg["max"])
    points = at_least(rvol, cfg["tiers"])
    parts = [f"ratio {rvol:.1f}x ({volume_label(rvol)})" if rvol is not None else "ratio N/D"]

    if premarket_volume and avg_volume_20d:
        pm_ratio = premarket_volume / avg_volume_20d
        parts.append(f"premarket {pm_ratio:.2f}x media diaria")
        if pm_ratio >= cfg["premarket_bonus_min_ratio"]:
            points += float(cfg["premarket_bonus"])

    points = min(max_points, points)
    if change_pct is not None and change_pct <= cfg["selloff_change_pct"]:
        # Heavy volume on a strong drop (e.g. dilutive offering) is not the
        # start of a speculative rally: half points and never active.
        parts.append(f"volumen en caida ({change_pct:+.1f}%)")
        return SignalScore(NAME, points / 2, max_points, False, ", ".join(parts))
    return SignalScore(NAME, points, max_points, points >= cfg["active_min"], ", ".join(parts))
