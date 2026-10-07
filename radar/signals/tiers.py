"""Helpers to evaluate [threshold, points] tier tables from config."""
from __future__ import annotations

from typing import Sequence

Tiers = Sequence[Sequence[float]]


def at_least(value: float | None, tiers: Tiers) -> float:
    """Points of the first tier whose threshold is <= value (tiers sorted desc)."""
    if value is None:
        return 0.0
    for threshold, points in sorted(tiers, key=lambda t: t[0], reverse=True):
        if value >= threshold:
            return float(points)
    return 0.0


def below(value: float | None, tiers: Tiers) -> float:
    """Points of the first tier whose threshold is > value (tiers sorted asc)."""
    if value is None:
        return 0.0
    for threshold, points in sorted(tiers, key=lambda t: t[0]):
        if value < threshold:
            return float(points)
    return 0.0


def at_most(value: float | None, tiers: Tiers) -> float:
    """Points of the first tier whose threshold is >= value (tiers sorted asc)."""
    if value is None:
        return 0.0
    for threshold, points in sorted(tiers, key=lambda t: t[0]):
        if value <= threshold:
            return float(points)
    return 0.0
