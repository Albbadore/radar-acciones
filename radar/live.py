"""Live market snapshot for the whole universe.

The Nasdaq screener download only refreshes after the session (it showed
SMXT at the previous close all day on 2026-10-06), so it cannot be used to
spot today's movers. Today's 5-minute bars from Yahoo, downloaded in bulk,
give the current price and accumulated volume for every stock.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import pandas as pd

from radar.metrics import intraday_stats
from radar.providers.nasdaq import ScreenerRow

LIVE_SOURCE = "Yahoo Finance (barras 5m de hoy)"


def live_rows(
    rows: list[ScreenerRow],
    frames: Mapping[str, pd.DataFrame],
    stats: Mapping[str, Mapping[str, Any]],
    today: date,
) -> list[ScreenerRow]:
    """Screener rows rebuilt with today's price, change and volume.

    Stocks without trades today or without a previous close are left out.
    """
    out: list[ScreenerRow] = []
    for row in rows:
        intra = intraday_stats(frames.get(row.ticker), today)
        prev = (stats.get(row.ticker) or {}).get("prev_close")
        if intra.last_price is None or not prev:
            continue
        out.append(ScreenerRow(
            ticker=row.ticker,
            name=row.name,
            price=intra.last_price,
            change_pct=(intra.last_price / prev - 1) * 100,
            net_change=intra.last_price - prev,
            volume=intra.regular_volume,
            market_cap=row.market_cap,
            source=LIVE_SOURCE,
        ))
    return out
