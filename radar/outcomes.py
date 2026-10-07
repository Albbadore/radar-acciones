"""Post-alert performance: max price after 1h, 4h, 1 day and 5 sessions.

The base price is the close of the bar at the alert time taken from the SAME
bar series used for the maxima, so a later split adjustment by the source does
not distort the percentages.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

import pandas as pd

from radar.clock import ET, add_trading_days
from radar.db import Database
from radar.metrics import max_after, min_after, to_et
from radar.providers import yahoo

log = logging.getLogger(__name__)

MAX_AGE_DAYS = 55  # Yahoo keeps 5-minute bars for ~60 days


def windows(alert_ts: datetime, holidays: Iterable[str]) -> dict[str, datetime]:
    fifth = add_trading_days(alert_ts.date(), 5, holidays)
    return {
        "1h": alert_ts + timedelta(hours=1),
        "4h": alert_ts + timedelta(hours=4),
        "1d": alert_ts + timedelta(days=1),
        "5d": datetime(fifth.year, fifth.month, fifth.day, 20, 0, tzinfo=ET),
    }


def base_price(bars: pd.DataFrame, alert_ts: datetime, fallback: float | None) -> float | None:
    local = to_et(bars.dropna(subset=["Close"]))
    before = local[local.index <= pd.Timestamp(alert_ts)]
    if before.empty:
        return fallback
    return float(before["Close"].iloc[-1])


def compute(
    bars: pd.DataFrame, alert_ts: datetime, alert_price: float | None,
    now: datetime, holidays: Iterable[str],
) -> tuple[dict[str, Any], bool]:
    """Outcome columns for windows already closed, and whether all are done."""
    if bars is None or bars.empty:
        return {}, False
    base = base_price(bars, alert_ts, alert_price)
    start = pd.Timestamp(alert_ts)
    values: dict[str, Any] = {}
    wins = windows(alert_ts, holidays)
    for label, end in wins.items():
        if end > now:
            continue
        out = max_after(bars, start, pd.Timestamp(end), base)
        values[f"max_price_{label}"] = out.price
        values[f"max_pct_{label}"] = out.pct
    if wins["5d"] <= now:
        low = min_after(bars, start, pd.Timestamp(wins["5d"]), base)
        values["min_price_5d"] = low.price
        values["min_pct_5d"] = low.pct
    return values, wins["5d"] <= now


def update_all(db: Database, now: datetime, cfg: Mapping[str, Any]) -> int:
    holidays = cfg["schedule"]["holidays"]
    pending = db.pending_outcomes()
    updated = 0
    bars_cache: dict[str, pd.DataFrame] = {}
    for alert in pending:
        ts = datetime.fromisoformat(alert["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ET)
        if (now - ts).days > MAX_AGE_DAYS:
            db.update_outcomes(alert["id"], {}, True, now.isoformat(timespec="seconds"))
            continue
        if now < ts + timedelta(hours=1):
            continue
        ticker = alert["ticker"]
        if ticker not in bars_cache:
            try:
                bars_cache[ticker] = yahoo.bars_since(ticker, ts.date() - timedelta(days=1))
            except Exception as exc:
                log.warning("Barras %s: %s", ticker, exc)
                bars_cache[ticker] = pd.DataFrame()
        values, complete = compute(bars_cache[ticker], ts, alert["price"], now, holidays)
        if values:
            db.update_outcomes(alert["id"], values, complete, now.isoformat(timespec="seconds"))
            updated += 1
    return updated
