"""US market calendar and session phases (America/New_York)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MADRID = ZoneInfo("Europe/Madrid")

PHASE_PREMARKET = "premarket"
PHASE_REGULAR = "regular"
PHASE_POSTMARKET = "postmarket"
PHASE_CLOSED = "cerrado"

POSTMARKET_END = time(20, 0)


def now_et() -> datetime:
    return datetime.now(ET)


def _t(text: str) -> time:
    hh, mm = text.split(":")
    return time(int(hh), int(mm))


def is_trading_day(day: date, holidays: Iterable[str]) -> bool:
    return day.weekday() < 5 and day.isoformat() not in set(holidays)


def market_phase(now: datetime, sched: Mapping[str, Any]) -> str:
    local = now.astimezone(ET)
    if not is_trading_day(local.date(), sched["holidays"]):
        return PHASE_CLOSED
    t = local.time()
    if _t(sched["premarket_start"]) <= t < _t(sched["regular_open"]):
        return PHASE_PREMARKET
    if _t(sched["regular_open"]) <= t < _t(sched["regular_close"]):
        return PHASE_REGULAR
    if _t(sched["regular_close"]) <= t < POSTMARKET_END:
        return PHASE_POSTMARKET
    return PHASE_CLOSED


def add_trading_days(day: date, n: int, holidays: Iterable[str]) -> date:
    hol = set(holidays)
    current = day
    count = 0
    while count < n:
        current += timedelta(days=1)
        if is_trading_day(current, hol):
            count += 1
    return current


def previous_trading_day(day: date, holidays: Iterable[str]) -> date:
    hol = set(holidays)
    current = day - timedelta(days=1)
    while not is_trading_day(current, hol):
        current -= timedelta(days=1)
    return current


def broker_open(now: datetime, hours: Iterable[str] = ("07:30", "23:00")) -> bool:
    """Trade Republic / LS Exchange usual hours: Mon-Fri, Madrid time."""
    local = now.astimezone(MADRID)
    start, end = (_t(h) for h in hours)
    return local.weekday() < 5 and start <= local.time() < end
