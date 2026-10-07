"""Protection against data that free sources have not yet adjusted for splits.

After a 1:N reverse split, some sources keep for a while:
  * the previous close unadjusted  -> fake +((N-1)*100)% daily gain;
  * pre-split volumes unadjusted   -> 20-day average inflated N times,
    so the volume ratio is understated.

Pure functions only: they receive data frames / numbers and return new ones.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from radar.models import ReverseSplitEvent, Sourced

COMMON_RATIOS = (
    2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 35, 40, 45, 50,
    60, 70, 75, 80, 100, 150, 200, 250, 300, 400, 500, 1000,
)
SUSPICIOUS_JUMP = 3.0      # x3 in one session with no known split: verify
RATIO_TOLERANCE = 0.12

PRICE_COLS = ("Open", "High", "Low", "Close", "Adj Close")


def nearest_ratio(jump: float, tolerance: float = RATIO_TOLERANCE) -> int | None:
    """Return a typical split ratio close to `jump`, if any."""
    if jump is None or jump <= 0 or math.isnan(jump):
        return None
    for ratio in COMMON_RATIOS:
        if abs(jump / ratio - 1) <= tolerance:
            return ratio
    return None


def looks_unadjusted(jump: float, ratio: int) -> bool:
    """True when a price jump is closer (in log terms) to the split ratio than to 1.

    A genuine same-session move rarely exceeds sqrt(N): for 1:10 that is +216 %.
    """
    if jump is None or jump <= 0 or not ratio or ratio <= 1:
        return False
    return abs(math.log(jump) - math.log(ratio)) < abs(math.log(jump))


@dataclass(frozen=True)
class DailyCheck:
    frame: pd.DataFrame
    adjusted: bool
    suspicious: bool
    ratio: int | None
    split_date: date | None
    note: str = ""


def _scale_before(frame: pd.DataFrame, cut: pd.Timestamp, factor: float) -> pd.DataFrame:
    """Multiply prices before `cut` by factor and divide their volume by it."""
    out = frame.copy()
    mask = out.index < cut
    for col in PRICE_COLS:
        if col in out.columns:
            out.loc[mask, col] = out.loc[mask, col] * factor
    if "Volume" in out.columns:
        out.loc[mask, "Volume"] = out.loc[mask, "Volume"] / factor
    return out


def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.dropna(subset=["Close"]).sort_index()
    return out.astype({c: "float64" for c in out.columns if c in (*PRICE_COLS, "Volume")})


def _jump_at(frame: pd.DataFrame, pos: int) -> tuple[float, float]:
    prev_close = float(frame["Close"].iloc[pos - 1])
    close_jump = float(frame["Close"].iloc[pos]) / prev_close
    open_jump = float(frame["Open"].iloc[pos]) / prev_close if "Open" in frame else close_jump
    return close_jump, open_jump


def _best_ratio(jump: float, ratios: set[int], known: int | None = None) -> int | None:
    """Ratio in `ratios` that explains a jump (both directions), if any.

    The confirmed split ratio wins whenever it explains the jump: prices drift
    between the last pre-split close and the first post-split open.
    """
    size = jump if jump >= 1 else 1 / jump
    fits = [r for r in ratios if looks_unadjusted(size, r)]
    if known in fits:
        return known
    return min(fits, key=lambda r: abs(math.log(size) - math.log(r))) if fits else None


def _fix_discontinuities(
    frame: pd.DataFrame, known_ratio: int | None
) -> tuple[pd.DataFrame, list[tuple[date, int, str]]]:
    """Make the price series continuous at every split-sized jump.

    Free sources sometimes adjust only part of the history, leaving "islands"
    of unadjusted rows (e.g. x20 lower for three sessions, then adjusted again).
    Each boundary is checked on the RAW series: an upward jump of ~N scales
    everything before it by N; a downward jump of ~1/N scales it by 1/N.
    Returns the fixed frame and (date, ratio, direction) per correction.
    """
    ratios: set[int] = {known_ratio} if known_ratio else set()
    for pos in range(1, len(frame)):
        close_jump, open_jump = _jump_at(frame, pos)
        blind = nearest_ratio(open_jump)
        if open_jump >= SUSPICIOUS_JUMP and blind and blind >= MIN_BLIND_RATIO:
            ratios.add(blind)
    if not ratios:
        return frame, []

    out = frame
    fixes: list[tuple[date, int, str]] = []
    for pos in range(len(frame) - 1, 0, -1):
        close_jump, open_jump = _jump_at(frame, pos)
        if 1 / SUSPICIOUS_JUMP < open_jump < SUSPICIOUS_JUMP:
            continue
        ratio = _best_ratio(open_jump, ratios, known_ratio)
        if not ratio or _best_ratio(close_jump, {ratio}) is None:
            continue
        if (open_jump > 1) != (close_jump > 1):
            continue
        up = open_jump > 1
        out = _scale_before(out, frame.index[pos], ratio if up else 1 / ratio)
        fixes.append((frame.index[pos].date(), ratio, "sube" if up else "baja"))
    return out, fixes


def _known_volume_only(frame: pd.DataFrame, event: ReverseSplitEvent) -> DailyCheck | None:
    """Prices already adjusted but pre-split volume still raw (medians compared)."""
    ratio = event.ratio
    cut = pd.Timestamp(event.event_date)
    if frame.index.tz is not None:
        cut = cut.tz_localize(frame.index.tz)
    positions = [i for i, ts in enumerate(frame.index) if ts >= cut]
    if not positions or positions[0] == 0:
        return None
    pos = positions[0]
    pre = frame["Volume"].iloc[:pos].tail(10)
    post = frame["Volume"].iloc[pos:]
    if len(pre) >= 3 and len(post) >= 1 and post.median() > 0:
        vol_ratio = float(pre.median() / post.median())
        if vol_ratio >= ratio / 2:
            out = frame.copy()
            mask = out.index < frame.index[pos]
            out.loc[mask, "Volume"] = out.loc[mask, "Volume"] / ratio
            note = (
                f"Volumen previo al reverse split 1:{ratio} parece sin ajustar "
                f"(mediana previa {vol_ratio:.1f}x la posterior): dividido entre {ratio}"
            )
            return DailyCheck(out, True, False, ratio, event.event_date, note)
    return None


def check_daily_history(
    frame: pd.DataFrame, event: ReverseSplitEvent | None = None
) -> DailyCheck:
    """Validate/adjust a daily OHLCV frame before computing averages."""
    if frame is None or frame.empty or "Close" not in frame:
        return DailyCheck(frame, False, False, None, None)
    clean = _clean(frame)
    if len(clean) < 2:
        return DailyCheck(clean, False, False, None, None)

    known = event.ratio if event is not None and event.status == "ejecutado" and event.ratio else None
    fixed, fixes = _fix_discontinuities(clean, known)
    if fixes:
        suspicious = any(ratio != known for _, ratio, _ in fixes)
        detail = ", ".join(f"{d.isoformat()} ({'x' if dirn == 'sube' else '/'}{r})" for d, r, dirn in fixes)
        note = (
            f"Historico con datos sin ajustar por split corregido en: {detail}; "
            f"precios y volumen reescalados (calculado)"
            + (". Split no confirmado: VERIFICAR" if suspicious else "")
        )
        last_day, last_ratio, _ = fixes[0]
        return DailyCheck(fixed, True, suspicious, last_ratio, last_day, note)

    if known:
        volume_fix = _known_volume_only(clean, event)
        if volume_fix is not None:
            return volume_fix
        return DailyCheck(clean, False, False, known, event.event_date)
    return DailyCheck(clean, False, False, None, None)


MIN_BLIND_RATIO = 5  # without a known split, x2-x4 gaps can be genuine moves


def reconcile_prev_close(
    candidates: list[Sourced | None], event: ReverseSplitEvent | None = None
) -> tuple[Sourced | None, str]:
    """Pick a previous close when sources disagree by a split ratio.

    The first candidate is the primary source. After a reverse split the
    adjusted previous close is the HIGHER value, so it is preferred only when
    the discrepancy matches the known split ratio, or is >= 1:5 without one.
    """
    usable = [c for c in candidates if c is not None and c.value]
    if not usable:
        return None, ""
    low = min(usable, key=lambda c: c.value)
    high = max(usable, key=lambda c: c.value)
    factor = high.value / low.value
    if factor < 1.8:
        return usable[0], ""
    known = event.ratio if event is not None and event.ratio else None
    ratio = nearest_ratio(factor)
    if (known and looks_unadjusted(factor, known)) or (ratio and ratio >= MIN_BLIND_RATIO):
        note = (
            f"Cierre anterior discrepa x{factor:.1f} entre {low.source} y {high.source} "
            f"(compatible con split 1:{known or ratio}); se usa el ajustado de {high.source}"
        )
        return high, note
    note = (
        f"Cierre anterior discrepa x{factor:.1f} entre {low.source} y {high.source}; "
        f"se usa {usable[0].source}"
    )
    return usable[0], note


def check_float(
    float_shares: Sourced | None,
    outstanding: Sourced | None,
    event: ReverseSplitEvent | None,
    today: date,
    recent_days: int = 60,
) -> tuple[Sourced | None, str]:
    """Detect a float that still reflects pre-split share counts."""
    if float_shares is None or not float_shares.value:
        return float_shares, ""
    if outstanding is not None and outstanding.value and float_shares.value > outstanding.value * 1.05:
        recent = (
            event is not None and event.status == "ejecutado" and event.ratio
            and 0 <= (today - event.event_date).days <= recent_days
        )
        if recent and looks_unadjusted(float_shares.value / outstanding.value, event.ratio):
            adjusted = Sourced(
                float_shares.value / event.ratio, float_shares.source, float_shares.as_of,
                f"dividido entre {event.ratio} por reverse split no reflejado (calculado)",
            )
            return adjusted, (
                f"Float de {float_shares.source} parece previo al reverse split 1:{event.ratio}; ajustado"
            )
        return outstanding, (
            f"Float ({float_shares.value:,.0f}) mayor que acciones en circulacion "
            f"({outstanding.value:,.0f}): se usa acciones en circulacion"
        )
    return float_shares, ""


@dataclass(frozen=True)
class LiveCheck:
    prev_close: float | None
    change_pct: float | None
    adjusted: bool
    suspicious: bool
    note: str = ""


def check_live_change(
    price: float | None,
    prev_close: float | None,
    event: ReverseSplitEvent | None,
    today: date,
    recent_days: int = 5,
) -> LiveCheck:
    """Validate today's % change against a possibly unadjusted previous close."""
    if price is None or not prev_close:
        return LiveCheck(prev_close, None, False, False)

    jump = price / prev_close
    raw_change = (jump - 1) * 100

    recent_split = (
        event is not None
        and event.ratio
        and event.status in ("ejecutado", "anunciado")
        and 0 <= (today - event.event_date).days <= recent_days
    )
    if recent_split and looks_unadjusted(jump, event.ratio):
        adj_prev = prev_close * event.ratio
        change = (price / adj_prev - 1) * 100
        note = (
            f"Cierre anterior sin ajustar por reverse split 1:{event.ratio} "
            f"(variacion bruta {raw_change:+.0f}% descartada; ajustada {change:+.1f}%)"
        )
        return LiveCheck(adj_prev, change, True, False, note)

    ratio = nearest_ratio(jump)
    if jump >= SUSPICIOUS_JUMP and ratio:
        note = (
            f"Variacion {raw_change:+.0f}% compatible con reverse split 1:{ratio} "
            f"no ajustado y sin split confirmado: dato bloqueado hasta verificar"
        )
        return LiveCheck(prev_close, raw_change, False, True, note)

    return LiveCheck(prev_close, raw_change, False, False)
