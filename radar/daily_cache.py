"""Once per session: 20-day average volume, previous close/high for the universe.

Every history goes through split_guard so that unadjusted pre-split volumes
do not inflate the average (which would understate the volume ratio).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Mapping

import pandas as pd

from radar.db import Database
from radar.metrics import daily_stats
from radar.models import ReverseSplitEvent
from radar.providers import yahoo
from radar.split_guard import check_daily_history

log = logging.getLogger(__name__)


def stats_row(
    ticker: str, frame: pd.DataFrame, today: date, event: ReverseSplitEvent | None
) -> dict[str, Any]:
    check = check_daily_history(frame, event)
    stats = daily_stats(check.frame, today)
    return {
        "session_date": today.isoformat(),
        "ticker": ticker,
        "avg_volume_20d": stats.avg_volume_20d,
        "prev_close": stats.prev_close,
        "prev_high": stats.prev_high,
        "low_5d": stats.low_5d,
        "last_session": stats.last_session.isoformat() if stats.last_session else None,
        "note": check.note,
        "suspicious": int(check.suspicious),
    }


def build(
    db: Database,
    tickers: list[str],
    today: date,
    events: Mapping[str, ReverseSplitEvent],
) -> int:
    """Compute and store daily stats for tickers missing today."""
    existing = db.daily_stats(today.isoformat())
    missing = [t for t in tickers if t not in existing]
    if not missing:
        return 0
    log.info("Calculando medias de volumen 20d para %d valores...", len(missing))
    frames = yahoo.daily_history(missing, period="3mo")
    rows = []
    for ticker, frame in frames.items():
        try:
            rows.append(stats_row(ticker, frame, today, events.get(ticker)))
        except Exception as exc:  # one bad history must not stop the cycle
            log.warning("Estadisticas diarias %s: %s", ticker, exc)
    db.upsert_daily_stats(rows)
    return len(rows)
