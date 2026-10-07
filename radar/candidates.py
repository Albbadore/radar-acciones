"""Pre-selection of tickers to analyse in depth each cycle (pure functions)."""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from radar.providers.nasdaq import ScreenerRow

# Relaxed thresholds for tickers with a recent reverse split filing.
RS_MIN_RVOL = 1.2
RS_MIN_CHANGE = 3.0


NON_COMMON = re.compile(
    r"\b(preferred|warrants?|units?|rights?|depositary|notes? due|debentures?)\b", re.I
)


def in_universe(row: ScreenerRow, ucfg: Mapping[str, Any]) -> bool:
    """Common stock within the price / market-cap limits."""
    if NON_COMMON.search(row.name):
        return False
    if row.price is None or not (ucfg["min_price"] <= row.price <= ucfg["max_price"]):
        return False
    cap_limit = ucfg.get("max_market_cap")
    if cap_limit and row.market_cap and row.market_cap > cap_limit:
        return False
    return True


def universe(rows: Iterable[ScreenerRow], ucfg: Mapping[str, Any]) -> list[ScreenerRow]:
    return [r for r in rows if in_universe(r, ucfg)]


def select_candidates(
    rows: Iterable[ScreenerRow],
    stats: Mapping[str, Mapping[str, Any]],
    always: Iterable[str],
    reverse_split_tickers: Iterable[str],
    ucfg: Mapping[str, Any],
) -> list[str]:
    """Tickers with abnormal volume or price move, plus the user watchlist.

    Tickers with a recent reverse split filing enter with relaxed thresholds.
    The screener % change is NOT trusted for scoring (it may be computed from
    an unadjusted previous close); here it only decides what to look at.
    """
    always_set = {t.upper() for t in always}
    rs_set = {t.upper() for t in reverse_split_tickers}
    ranked: list[tuple[float, str]] = []
    for row in universe(rows, ucfg):
        if row.ticker in always_set:
            continue
        avg = (stats.get(row.ticker) or {}).get("avg_volume_20d")
        rvol = (row.volume / avg) if (row.volume and avg) else 0.0
        change = row.change_pct or 0.0
        has_rs = row.ticker in rs_set
        min_rvol = RS_MIN_RVOL if has_rs else ucfg["candidate_min_rvol"]
        min_change = RS_MIN_CHANGE if has_rs else ucfg["candidate_min_change_pct"]
        if rvol >= min_rvol or change >= min_change:
            rank = min(rvol, 50) + max(change, 0) / 5 + (5 if has_rs else 0)
            ranked.append((rank, row.ticker))

    ranked.sort(reverse=True)
    limit = int(ucfg["max_candidates_per_cycle"])
    selected = sorted(always_set)[:limit]
    selected += [t for _, t in ranked][: max(0, limit - len(selected))]
    return selected
