"""Evaluate historically which signals preceded real moves (pure functions)."""
from __future__ import annotations

from statistics import mean, median
from typing import Any, Iterable

from radar.scoring import SIGNAL_LABELS

HORIZONS = ("1h", "4h", "1d", "5d")
DEFAULT_HIT_PCT = 20.0


def _summary(rows: list[dict[str, Any]], horizon: str, hit_pct: float) -> dict[str, Any]:
    vals = [r[f"max_pct_{horizon}"] for r in rows if r.get(f"max_pct_{horizon}") is not None]
    if not vals:
        return {"n": 0, "media": None, "mediana": None, "acierto": None}
    return {
        "n": len(vals),
        "media": round(mean(vals), 1),
        "mediana": round(median(vals), 1),
        "acierto": round(100 * sum(v >= hit_pct for v in vals) / len(vals), 1),
    }


def _first_per_ticker_day(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Avoid counting the same move several times: first event per ticker/day."""
    seen: set[tuple[str, str]] = set()
    out = []
    for row in sorted(rows, key=lambda r: r["ts"]):
        key = (row["ticker"], row["ts"][:10])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def report(rows: Iterable[dict[str, Any]], hit_pct: float = DEFAULT_HIT_PCT) -> dict[str, Any]:
    events = _first_per_ticker_day(rows)
    by_level: dict[str, Any] = {}
    for level in sorted({r["level"] for r in events}):
        group = [r for r in events if r["level"] == level]
        by_level[level] = {h: _summary(group, h, hit_pct) for h in HORIZONS}

    by_signal: dict[str, Any] = {}
    for name, label in SIGNAL_LABELS.items():
        on = [r for r in events if name in (r.get("active_signals") or "").split(",")]
        off = [r for r in events if name not in (r.get("active_signals") or "").split(",")]
        by_signal[label] = {
            "activa": {h: _summary(on, h, hit_pct) for h in HORIZONS},
            "inactiva": {h: _summary(off, h, hit_pct) for h in HORIZONS},
        }

    by_count: dict[str, Any] = {}
    for count in range(6):
        group = [
            r for r in events
            if len([s for s in (r.get("active_signals") or "").split(",") if s]) == count
        ]
        if group:
            by_count[str(count)] = {h: _summary(group, h, hit_pct) for h in HORIZONS}

    return {
        "umbral_acierto_pct": hit_pct,
        "eventos": len(events),
        "por_nivel": by_level,
        "por_senal": by_signal,
        "por_num_senales": by_count,
    }


def format_report(rep: dict[str, Any]) -> str:
    lines = [
        f"Eventos analizados: {rep['eventos']} (acierto = subida maxima >= "
        f"{rep['umbral_acierto_pct']:g}% tras la alerta)",
        "",
        "POR NIVEL                  n    media1d  acierto1d  media5d  acierto5d",
    ]

    def row(label: str, stats: dict[str, Any]) -> str:
        d1, d5 = stats["1d"], stats["5d"]
        fmt = lambda v: "   N/D" if v is None else f"{v:6.1f}"  # noqa: E731
        return (f"{label:<25} {d1['n']:>3}   {fmt(d1['media'])}   {fmt(d1['acierto'])}%   "
                f"{fmt(d5['media'])}   {fmt(d5['acierto'])}%")

    lines += [row(k, v) for k, v in rep["por_nivel"].items()]
    lines += ["", "POR SENAL (activa vs inactiva)"]
    for label, groups in rep["por_senal"].items():
        lines.append(row(f"{label} +", groups["activa"]))
        lines.append(row(f"{label} -", groups["inactiva"]))
    lines += ["", "POR NUMERO DE SENALES ACTIVAS"]
    lines += [row(f"{k} senales", v) for k, v in rep["por_num_senales"].items()]
    return "\n".join(lines)
