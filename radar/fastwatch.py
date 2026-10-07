"""Fast watch: poll ALTA/MAXIMA tickers every ~90 s and alert when they START moving.

The regular cycle (5-10 min) says "the setup is there". This module says
"it is starting now", so the user can act before most of the move.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from radar.alerts import DISCLAIMER, fmt_num, fmt_pct, fmt_price, time_lines
from radar.clock import previous_trading_day
from radar.db import Database
from radar.notify import deliver
from radar.providers import nasdaq

log = logging.getLogger(__name__)

KIND_START = "ARRANQUE"
START_HEADER = "⚡ ARRANQUE DE MOVIMIENTO"
HOT_LEVELS = ("ALERTA ALTA", "ALERTA MAXIMA")


@dataclass(frozen=True)
class Tick:
    ts: datetime
    price: float
    volume: float | None


@dataclass(frozen=True)
class StartSignal:
    gain_pct: float
    minutes: float
    window_volume: float | None
    base_price: float
    price: float


def detect_start(
    ticks: list[Tick], avg_volume: float | None, cfg: Mapping[str, Any]
) -> StartSignal | None:
    """Price up >= min_gain_pct within window_minutes, with real volume behind it."""
    if len(ticks) < 2:
        return None
    last = ticks[-1]
    window_start = last.ts - timedelta(minutes=float(cfg["window_minutes"]))
    in_window = [t for t in ticks[:-1] if t.ts >= window_start]
    if not in_window:
        return None
    base = min(in_window, key=lambda t: t.price)  # lowest point of the window
    if base.price <= 0:
        return None
    gain = (last.price / base.price - 1) * 100
    if gain < float(cfg["min_gain_pct"]):
        return None
    window_volume = None
    if last.volume is not None and base.volume is not None:
        window_volume = max(0.0, last.volume - base.volume)
        if avg_volume and window_volume < avg_volume * float(cfg["min_window_volume_pct"]) / 100:
            return None  # price moved on thin volume: not a real start
    minutes = (last.ts - base.ts).total_seconds() / 60
    return StartSignal(round(gain, 1), round(minutes, 1), window_volume, base.price, last.price)


def format_start(ticker: str, hot: Mapping[str, Any], sig: StartSignal, quote: nasdaq.LiveQuote,
                 now: datetime, broker_cfg: Mapping[str, Any]) -> str:
    return "\n".join([
        START_HEADER,
        "",
        f"Ticker: {ticker} - {hot.get('name') or 'N/D'}",
        f"Sube {sig.gain_pct:+.1f}% en {sig.minutes:.0f} min "
        f"({fmt_price(sig.base_price)} -> {fmt_price(sig.price)})",
        f"Variacion del dia: {fmt_pct(quote.change_pct)}",
        f"Volumen en la ventana: {fmt_num(sig.window_volume)} "
        f"(media diaria 20d: {fmt_num(hot.get('avg_volume_20d'))})",
        f"Volumen acumulado: {fmt_num(quote.volume)} ({quote.source})",
        f"Puntuacion previa: {hot.get('total')}/100 - {hot.get('level')} ({(hot.get('ts') or '')[:16]})",
        *time_lines(now, broker_cfg),
        "",
        DISCLAIMER,
    ])


class FastWatcher:
    def __init__(self, db: Database, cfg: Mapping[str, Any],
                 quote_fn: Callable[[str], nasdaq.LiveQuote | None] = nasdaq.fetch_live,
                 send: Callable[[str, Mapping[str, Any]], Any] = deliver):
        self.db, self.cfg, self.quote_fn, self.send = db, cfg, quote_fn, send
        self.ticks: dict[str, deque[Tick]] = {}
        self.last_alert: dict[str, datetime] = {}

    def hot_list(self, now: datetime) -> dict[str, dict[str, Any]]:
        """Latest snapshot per ticker that reached ALTA/MAXIMA since the previous session."""
        since = previous_trading_day(now.date(), self.cfg["schedule"]["holidays"]).isoformat()
        rows = self.db.query(
            "SELECT s.ticker, s.name, s.total, s.level, s.ts, s.avg_volume_20d FROM snapshots s "
            "JOIN (SELECT ticker, MAX(ts) AS ts FROM snapshots WHERE ts >= ? AND data_blocked = 0 "
            "      AND level IN (?, ?) GROUP BY ticker) h ON h.ticker = s.ticker AND h.ts = s.ts",
            (since, *HOT_LEVELS),
        )
        return {r["ticker"]: r for r in rows}

    def poll(self, now: datetime) -> list[str]:
        fcfg = self.cfg["fastwatch"]
        sent: list[str] = []
        for ticker, hot in self.hot_list(now).items():
            try:
                quote = self.quote_fn(ticker)
            except Exception as exc:
                log.debug("Cotizacion rapida %s: %s", ticker, exc)
                continue
            if quote is None or not quote.price:
                continue
            series = self.ticks.setdefault(ticker, deque(maxlen=120))
            series.append(Tick(now, quote.price, quote.volume))
            sig = detect_start(list(series), hot.get("avg_volume_20d"), fcfg)
            if sig is None:
                continue
            last = self.last_alert.get(ticker)
            if last and now - last < timedelta(minutes=float(fcfg["cooldown_minutes"])):
                continue
            message = format_start(ticker, hot, sig, quote, now, self.cfg.get("broker", {}))
            self.send(message, self.cfg)
            self.last_alert[ticker] = now
            self.db.insert_alert({
                "ts": now.isoformat(timespec="seconds"), "ticker": ticker, "kind": KIND_START,
                "notified": 1, "reason": f"{sig.gain_pct:+.1f}% en {sig.minutes:.0f} min",
                "level": hot.get("level"), "total": hot.get("total"), "price": quote.price,
                "change_pct": quote.change_pct, "message": message,
                "payload": json.dumps({"signal": sig.__dict__, "quote": quote.__dict__}, default=str),
            })
            sent.append(ticker)
        return sent
