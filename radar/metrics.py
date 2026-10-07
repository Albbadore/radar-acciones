"""Pure computations over daily and intraday bars."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

import pandas as pd

from radar.clock import ET

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


@dataclass(frozen=True)
class DailyStats:
    avg_volume_20d: float | None
    prev_close: float | None
    prev_high: float | None
    last_session: date | None
    low_5d: float | None = None


def daily_stats(frame: pd.DataFrame, today: date, window: int = 20) -> DailyStats:
    """Stats from completed sessions strictly before `today`."""
    if frame is None or frame.empty:
        return DailyStats(None, None, None, None)
    done = frame.dropna(subset=["Close"])
    done = done[[ts.date() < today for ts in done.index]]
    if done.empty:
        return DailyStats(None, None, None, None)
    lows = done["Low"].tail(5) if "Low" in done else done["Close"].tail(5)
    vols = done["Volume"].tail(window)
    avg = float(vols.mean()) if len(vols) >= min(10, window) else None
    last = done.iloc[-1]
    return DailyStats(
        avg_volume_20d=avg if avg and avg > 0 else None,
        prev_close=float(last["Close"]),
        prev_high=float(last["High"]),
        last_session=done.index[-1].date(),
        low_5d=float(lows.min()) if len(lows) else None,
    )


@dataclass(frozen=True)
class IntradayStats:
    last_price: float | None
    last_bar_time: str | None
    regular_volume: float | None
    day_high: float | None
    premarket_volume: float | None
    premarket_last: float | None
    volume_acceleration: float | None
    day_low: float | None = None


def to_et(frame: pd.DataFrame) -> pd.DataFrame:
    idx = frame.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out = frame.copy()
    out.index = idx.tz_convert(ET)
    return out


def volume_acceleration(regular: pd.DataFrame, recent: int = 3, min_prior: int = 6) -> float | None:
    """Mean volume of the last `recent` bars vs mean of earlier bars today."""
    if regular is None or len(regular) < recent + min_prior:
        return None
    vols = regular["Volume"].astype(float)
    prior = vols.iloc[:-recent].mean()
    if not prior or prior <= 0:
        return None
    closes = regular["Close"].astype(float)
    if closes.iloc[-1] <= closes.iloc[-recent - 1]:
        return None  # accelerate only counts when price is rising
    return float(vols.iloc[-recent:].mean() / prior)


def intraday_stats(frame: pd.DataFrame | None, today: date) -> IntradayStats:
    empty = IntradayStats(None, None, None, None, None, None, None)
    if frame is None or frame.empty:
        return empty
    bars = to_et(frame.dropna(subset=["Close"]))
    bars = bars[[ts.date() == today for ts in bars.index]]
    if bars.empty:
        return empty

    times = [ts.time() for ts in bars.index]
    pre = bars[[t < REGULAR_OPEN for t in times]]
    regular = bars[[REGULAR_OPEN <= t < REGULAR_CLOSE for t in times]]

    pm_vol = float(pre["Volume"].sum()) if not pre.empty else 0.0
    reg_vol = float(regular["Volume"].sum()) if not regular.empty else 0.0
    return IntradayStats(
        last_price=float(bars["Close"].iloc[-1]),
        last_bar_time=bars.index[-1].isoformat(),
        # Yahoo often reports 0 volume for extended hours: treat as unknown.
        regular_volume=reg_vol if reg_vol > 0 else None,
        day_high=float(regular["High"].max()) if not regular.empty else None,
        premarket_volume=pm_vol if pm_vol > 0 else None,
        premarket_last=float(pre["Close"].iloc[-1]) if not pre.empty else None,
        volume_acceleration=volume_acceleration(regular),
        day_low=float(bars["Low"].min()) if "Low" in bars else None,
    )


@dataclass(frozen=True)
class Outcome:
    price: float | None
    pct: float | None


def max_after(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, base: float) -> Outcome:
    local = to_et(bars.dropna(subset=["High"]))
    window = local[(local.index > start) & (local.index <= end)]
    if window.empty or not base:
        return Outcome(None, None)
    top = float(window["High"].max())
    return Outcome(top, round((top / base - 1) * 100, 2))


def min_after(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, base: float) -> Outcome:
    local = to_et(bars.dropna(subset=["Low"]))
    window = local[(local.index > start) & (local.index <= end)]
    if window.empty or not base:
        return Outcome(None, None)
    low = float(window["Low"].min())
    return Outcome(low, round((low / base - 1) * 100, 2))
