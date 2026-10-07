"""SENAL 4 - Noticias o catalizadores recientes (0-20)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from radar.models import Catalyst, SignalScore

NAME = "catalyst"


def _catalyst_points(cat: Catalyst, now: datetime, cfg: Mapping[str, Any]) -> float:
    points = float(cfg["impact_points"].get(cat.impact, 0))
    if cat.official:
        points += float(cfg["official_bonus"])
    if (now - cat.published).total_seconds() <= 86400:
        points += float(cfg["fresh_bonus"])
    return points


def score_catalyst(
    catalysts: Iterable[Catalyst], now: datetime, cfg: Mapping[str, Any]
) -> SignalScore:
    max_points = float(cfg["max"])
    items = list(catalysts)
    if not items:
        return SignalScore(NAME, 0.0, max_points, False, "Sin catalizadores en 7 dias")

    ranked = sorted(items, key=lambda c: _catalyst_points(c, now, cfg), reverse=True)
    best = ranked[0]
    points = _catalyst_points(best, now, cfg)
    extra_categories = {
        c.category for c in ranked[1:]
        if c.impact in ("alto", "medio") and c.category != best.category
    }
    points += len(extra_categories) * float(cfg["extra_category_points"])
    points = min(max_points, points)

    origin = "oficial" if best.official else "no oficial"
    detail = f"{best.category} ({origin}, {best.source}, {best.published.date().isoformat()})"
    if extra_categories:
        detail += f" + {len(extra_categories)} categoria(s) mas"
    return SignalScore(NAME, points, max_points, points >= cfg["active_min"], detail)
