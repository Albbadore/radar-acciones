"""Immutable domain models. Every market datum carries its source and date."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

ND = "N/D"


@dataclass(frozen=True)
class Sourced:
    """A value plus where it came from and when it was valid."""

    value: Any
    source: str
    as_of: str | None = None
    note: str = ""


def val(item: Sourced | None) -> Any:
    return None if item is None else item.value


@dataclass(frozen=True)
class ReverseSplitEvent:
    status: str                  # "ejecutado" | "anunciado" | "propuesto"
    event_date: date             # execution date, or filing date if not executed
    ratio: int | None            # 20 means 1:20
    source: str
    url: str = ""
    ratio_text: str = ""
    filed_date: date | None = None
    effective_date: date | None = None

    @property
    def ratio_label(self) -> str:
        return f"1:{self.ratio}" if self.ratio else ND


@dataclass(frozen=True)
class Catalyst:
    published: datetime
    category: str
    impact: str                  # "alto" | "medio" | "bajo"
    title: str
    source: str
    official: bool
    url: str = ""


@dataclass(frozen=True)
class StockData:
    ticker: str
    updated_at: datetime
    phase: str
    name: Sourced | None = None
    price: Sourced | None = None
    prev_close: Sourced | None = None
    change_pct: Sourced | None = None
    prev_high: Sourced | None = None
    day_high: Sourced | None = None
    market_cap: Sourced | None = None
    shares_outstanding: Sourced | None = None
    float_shares: Sourced | None = None
    volume: Sourced | None = None
    avg_volume_20d: Sourced | None = None
    premarket_volume: Sourced | None = None
    premarket_change_pct: Sourced | None = None
    volume_acceleration: Sourced | None = None
    # % from the lowest low of the last 5 sessions (incl. today) to the price.
    run_up_pct: Sourced | None = None
    reverse_split: ReverseSplitEvent | None = None
    tradable: Sourced | None = None   # availability at the user's broker
    catalysts: tuple[Catalyst, ...] = ()
    # Data-quality notes (e.g. split adjustments). If `data_blocked` is True the
    # price data is not trustworthy and no alert may be sent.
    quality_notes: tuple[str, ...] = ()
    data_blocked: bool = False
    errors: tuple[str, ...] = ()

    @property
    def rvol(self) -> float | None:
        vol, avg = val(self.volume), val(self.avg_volume_20d)
        if vol is None or not avg:
            return None
        return vol / avg

    @property
    def effective_float(self) -> Sourced | None:
        """Public float if known, else shares outstanding."""
        return self.float_shares or self.shares_outstanding


@dataclass(frozen=True)
class SignalScore:
    name: str
    points: float
    max_points: float
    active: bool
    detail: str


@dataclass(frozen=True)
class ScoreResult:
    signals: tuple[SignalScore, ...]
    raw_total: float
    bonus: float
    capped: bool
    total: int
    level: str
    active_names: tuple[str, ...] = field(default_factory=tuple)
    penalty: float = 0.0
    late_note: str = ""

    @property
    def active_count(self) -> int:
        return len(self.active_names)

    def points(self, name: str) -> float:
        return next((s.points for s in self.signals if s.name == name), 0.0)


def to_jsonable(obj: Any) -> Any:
    """Convert dataclasses/dates to JSON-safe structures."""
    if hasattr(obj, "__dataclass_fields__"):
        return to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float) and obj != obj:  # NaN
        return None
    return obj
