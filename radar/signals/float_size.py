"""SENAL 2 - Float reducido (0-20)."""
from __future__ import annotations

from typing import Any, Mapping

from radar.models import Sourced, SignalScore
from radar.signals.tiers import below

NAME = "float"


def score_float(float_shares: Sourced | None, cfg: Mapping[str, Any]) -> SignalScore:
    max_points = float(cfg["max"])
    if float_shares is None or float_shares.value is None:
        return SignalScore(NAME, 0.0, max_points, False, "Float N/D")

    shares = float(float_shares.value)
    points = min(max_points, below(shares, cfg["tiers"]))
    detail = (
        f"{shares / 1e6:.2f}M acciones ({float_shares.source}, "
        f"{float_shares.as_of or 'fecha N/D'})"
    )
    if float_shares.note:
        detail += f" - {float_shares.note}"
    return SignalScore(NAME, points, max_points, points >= cfg["active_min"], detail)
