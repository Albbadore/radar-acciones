"""Alert policy (when to notify) and alert message formatting."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from radar.clock import ET, MADRID, broker_open
from radar.models import ND, ScoreResult, StockData, val
from radar.scoring import LEVEL_RANK, LEVEL_VIGILAR, SIGNAL_LABELS

DISCLAIMER = (
    "Esta alerta identifica coincidencia de senales. "
    "No constituye una recomendacion de compra."
)
ALERT_HEADER = "\U0001F6A8 POSIBLE MOVIMIENTO ESPECULATIVO"

KIND_INITIAL = "INICIAL"
KIND_ESCALATION = "ESCALADA"
KIND_MAJOR_CHANGE = "CAMBIO IMPORTANTE"
KIND_UPDATE = "ACTUALIZACION"
KIND_WATCH = "VIGILAR"  # recorded, never notified


@dataclass(frozen=True)
class AlertState:
    """Last notified alert for a ticker."""

    ts: datetime
    level: str
    score: int
    price: float | None


@dataclass(frozen=True)
class AlertDecision:
    notify: bool
    kind: str = ""
    reason: str = ""


def _pct(new: float | None, old: float | None) -> float:
    if not new or not old:
        return 0.0
    return (new / old - 1) * 100


def decide_alert(
    last: AlertState | None,
    result: ScoreResult,
    price: float | None,
    now: datetime,
    cfg: Mapping[str, Any],
    previous_level: str | None = None,
    data_blocked: bool = False,
) -> AlertDecision:
    if data_blocked:
        return AlertDecision(False, reason="datos bloqueados por posible split no ajustado")
    if result.total < cfg["min_score"]:
        return AlertDecision(False, reason="puntuacion inferior al umbral")

    if last is None or last.ts.date() != now.date():
        if previous_level == LEVEL_VIGILAR:
            return AlertDecision(True, KIND_ESCALATION, f"{LEVEL_VIGILAR} -> {result.level}")
        return AlertDecision(True, KIND_INITIAL, "primera alerta del dia")

    if LEVEL_RANK[result.level] > LEVEL_RANK.get(last.level, 0):
        return AlertDecision(True, KIND_ESCALATION, f"{last.level} -> {result.level}")

    minutes = (now - last.ts).total_seconds() / 60
    score_delta = result.total - last.score
    price_delta = _pct(price, last.price)

    if minutes < cfg["cooldown_minutes"]:
        if score_delta >= cfg["major_change_score_delta"] or price_delta >= cfg["major_change_price_pct"]:
            return AlertDecision(
                True, KIND_MAJOR_CHANGE,
                f"puntuacion {score_delta:+d}, precio {price_delta:+.1f}% en {minutes:.0f} min",
            )
        return AlertDecision(False, reason=f"cooldown ({minutes:.0f} min)")

    if score_delta >= cfg["repeat_score_delta"] or price_delta >= cfg["repeat_price_pct"]:
        return AlertDecision(
            True, KIND_UPDATE, f"puntuacion {score_delta:+d}, precio {price_delta:+.1f}%"
        )
    return AlertDecision(False, reason="sin cambios relevantes")


# ---------------------------------------------------------------- formatting

def fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return ND
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(num) >= 1e9:
        return f"{num / 1e9:.2f}B"
    if abs(num) >= 1e6:
        return f"{num / 1e6:.2f}M"
    if abs(num) >= 1e3:
        return f"{num / 1e3:.1f}K"
    return f"{num:.{decimals}f}"


def fmt_price(value: Any) -> str:
    if value is None:
        return ND
    num = float(value)
    return f"${num:.4f}" if num < 1 else f"${num:.2f}"


def fmt_pct(value: Any) -> str:
    return ND if value is None else f"{float(value):+.1f}%"


def fmt_sourced(item: Any, formatter=fmt_num) -> str:
    if item is None or item.value is None:
        return ND
    return f"{formatter(item.value)} ({item.source}, {item.as_of or 'fecha N/D'})"


def time_lines(now: datetime, broker_cfg: Mapping[str, Any] | None = None) -> list[str]:
    """Time in New York and Spain, and whether the broker accepts orders now."""
    hours = tuple((broker_cfg or {}).get("trading_hours_madrid") or ("07:30", "23:00"))
    et, es = now.astimezone(ET), now.astimezone(MADRID)
    state = (
        f"Trade Republic ABIERTO ahora ({hours[0]}-{hours[1]} hora de Espana)"
        if broker_open(now, hours)
        else f"Trade Republic CERRADO ahora: una orden esperaria a las {hours[0]} hora de Espana"
    )
    return [f"Hora: {et:%H:%M} Nueva York / {es:%H:%M} Espana ({es:%d-%m-%Y})", state]


def format_alert(data: StockData, result: ScoreResult, kind: str, reason: str,
                 broker_cfg: Mapping[str, Any] | None = None) -> str:
    rs = data.reverse_split
    rvol = data.rvol
    news = [
        f"  - [{c.published.date().isoformat()}] {c.category}: {c.title[:120]} "
        f"({c.source}{', oficial' if c.official else ''})"
        for c in data.catalysts[:5]
    ]
    signals = [
        f"  - {SIGNAL_LABELS[s.name]}: {s.points:g}/{s.max_points:g}"
        f"{' [ACTIVA]' if s.active else ''} - {s.detail}"
        for s in result.signals
    ]
    lines = [
        ALERT_HEADER,
        f"[{result.level} | {kind}: {reason}]",
        "",
        f"Ticker: {data.ticker} - {val(data.name) or ND}",
        f"Trade Republic: {val(data.tradable) or ND}"
        f"{f' ({data.tradable.source})' if data.tradable else ''}",
        f"Precio: {fmt_price(val(data.price))}",
        f"Variacion: {fmt_pct(val(data.change_pct))}",
        f"Premarket: {fmt_pct(val(data.premarket_change_pct))}",
        f"Subida desde minimo 5 sesiones: {fmt_pct(val(data.run_up_pct))}",
        f"Volumen: {fmt_sourced(data.volume)}",
        f"Volumen medio 20 dias: {fmt_sourced(data.avg_volume_20d)}",
        f"Ratio de volumen: {f'{rvol:.1f}x' if rvol is not None else ND}",
        f"Float: {fmt_sourced(data.effective_float)}",
        f"Reverse split: {rs.status + ' ' + rs.ratio_label if rs else ND}",
        f"Fecha del reverse split: {rs.event_date.isoformat() if rs else ND}",
        "Noticias recientes:",
        *(news or ["  N/D"]),
        f"Puntuacion: {result.total}/100 (senales {result.raw_total:g} + bonus {result.bonus:g}"
        f"{f' - penalizacion {result.penalty:g}' if result.penalty else ''}"
        f"{', limitada' if result.capped else ''})",
        f"Senales activadas ({result.active_count}/5): "
        + (", ".join(SIGNAL_LABELS[n] for n in result.active_names) or "ninguna"),
        *signals,
    ]
    if result.late_note:
        lines.append(f"Aviso: {result.late_note}")
    if data.quality_notes:
        lines += ["Calidad de datos:", *(f"  - {n}" for n in data.quality_notes)]
    lines += [*time_lines(data.updated_at, broker_cfg), "", DISCLAIMER]
    return "\n".join(lines)
