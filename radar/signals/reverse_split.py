"""SENAL 1 - Reverse split (0-20)."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from radar.models import ReverseSplitEvent, SignalScore
from radar.signals.tiers import at_least, at_most

NAME = "reverse_split"

_RECENCY_KEY = {
    "ejecutado": "executed_recency",
    "anunciado": "announced_recency",
    "propuesto": "proposed_recency",
}


def score_reverse_split(
    event: ReverseSplitEvent | None, today: date, cfg: Mapping[str, Any]
) -> SignalScore:
    max_points = float(cfg["max"])
    if event is None:
        return SignalScore(NAME, 0.0, max_points, False, "Sin reverse split reciente")

    days = (today - event.event_date).days
    if days < 0:
        # Announced with a future effective date: treat as fresh announcement.
        days = 0
    base = at_most(days, cfg[_RECENCY_KEY[event.status]])
    factor = (
        at_least(event.ratio, cfg["ratio_factor"])
        if event.ratio
        else float(cfg["unknown_ratio_factor"])
    )
    points = round(min(max_points, base * factor), 1)
    detail = (
        f"{event.status} {event.ratio_label} el {event.event_date.isoformat()} "
        f"(hace {days} d, fuente {event.source})"
    )
    return SignalScore(NAME, points, max_points, points >= cfg["active_min"], detail)
