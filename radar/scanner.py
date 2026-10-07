"""One scan cycle: universe -> candidates -> enrich -> score -> store -> alert."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from radar import candidates as cand_mod
from radar import daily_cache
from radar import reverse_splits as rs_mod
from radar import tradable as tradable_mod
from radar.alerts import KIND_WATCH, AlertState, decide_alert, format_alert
from radar.clock import PHASE_POSTMARKET, PHASE_PREMARKET, PHASE_REGULAR, previous_trading_day
from radar.enrich import Services, enrich
from radar.models import ScoreResult, Sourced, StockData, to_jsonable, val
from radar.notify import deliver
from radar.providers import nasdaq, yahoo
from radar.scoring import LEVEL_VIGILAR, score_stock

log = logging.getLogger(__name__)

WORKERS = 6


def refresh_reverse_splits(svc: Services, now: datetime, force: bool = False) -> None:
    if svc.sec is None:
        return
    last = svc.db.get_kv("rs_refresh_ts")
    hours = float(svc.cfg["schedule"]["reverse_split_refresh_hours"])
    if not force and last and datetime.fromisoformat(last) > now - timedelta(hours=hours):
        return
    n = rs_mod.refresh_filings(svc.db, svc.sec, now.date(), svc.cfg)
    svc.db.set_kv("rs_refresh_ts", now.isoformat())
    log.info("Reverse splits SEC: %d documentos nuevos analizados", n)


def _watch_candidates(
    svc: Services, now: datetime, allowed: set[str], first: list[str] | None = None,
    limit: int | None = None,
) -> list[str]:
    """Extended-hours list: user watchlist, earlier detections, recent reverse splits.

    `allowed` is the common-stock universe (drops warrants, units, preferreds).
    """
    ucfg = svc.cfg["universe"]
    recent = rs_mod.watchlist(svc.db, now.date(), days=30)
    since = now.date() if now.hour >= 16 else previous_trading_day(now.date(), svc.cfg["schedule"]["holidays"])
    seen = svc.db.tickers_seen_since(since.isoformat(), int(svc.cfg["scoring"]["levels"]["vigilar"]))
    extra = [t.upper() for t in ucfg["extra_watchlist"]]
    pool = [t for t in [*(first or []), *seen, *recent.keys()] if t in allowed]
    ordered = list(dict.fromkeys([*extra, *pool]))
    return ordered[: limit or int(ucfg["max_candidates_per_cycle"])]


def _snapshot_row(cycle_id: int, data: StockData, result: ScoreResult) -> dict[str, Any]:
    rs = data.reverse_split
    eff_float = data.effective_float
    return {
        "cycle_id": cycle_id,
        "ts": data.updated_at.isoformat(timespec="seconds"),
        "ticker": data.ticker,
        "name": val(data.name),
        "phase": data.phase,
        "price": val(data.price),
        "change_pct": val(data.change_pct),
        "premarket_change_pct": val(data.premarket_change_pct),
        "market_cap": val(data.market_cap),
        "float_shares": val(eff_float),
        "volume": val(data.volume),
        "avg_volume_20d": val(data.avg_volume_20d),
        "rvol": data.rvol,
        "run_up_pct": val(data.run_up_pct),
        "rs_status": rs.status if rs else None,
        "rs_ratio": rs.ratio if rs else None,
        "rs_date": rs.event_date.isoformat() if rs else None,
        "n_catalysts": len(data.catalysts),
        **{f"score_{s.name}": s.points for s in result.signals},
        "bonus": result.bonus,
        "total": result.total,
        "level": result.level,
        "active_signals": ",".join(result.active_names),
        "data_blocked": int(data.data_blocked),
        "payload": json.dumps(
            {"data": to_jsonable(data), "score": to_jsonable(result)}, ensure_ascii=False
        ),
    }


def _alert_row(data: StockData, result: ScoreResult, kind: str, reason: str,
               notified: bool, message: str) -> dict[str, Any]:
    return {
        "ts": data.updated_at.isoformat(timespec="seconds"),
        "ticker": data.ticker,
        "kind": kind,
        "notified": int(notified),
        "reason": reason,
        "level": result.level,
        "total": result.total,
        "price": val(data.price),
        "change_pct": val(data.change_pct),
        "rvol": data.rvol,
        "run_up_pct": val(data.run_up_pct),
        "float_shares": val(data.effective_float),
        **{f"score_{s.name}": s.points for s in result.signals},
        "bonus": result.bonus,
        "active_signals": ",".join(result.active_names),
        "message": message,
        "payload": json.dumps(
            {"data": to_jsonable(data), "score": to_jsonable(result)}, ensure_ascii=False
        ),
    }


def _last_state(svc: Services, ticker: str) -> AlertState | None:
    row = svc.db.last_notified_alert(ticker)
    if not row:
        return None
    return AlertState(datetime.fromisoformat(row["ts"]), row["level"], row["total"], row["price"])


def handle_alerts(svc: Services, data: StockData, result: ScoreResult) -> str | None:
    ts = data.updated_at.isoformat(timespec="seconds")
    day = data.updated_at.date().isoformat()
    previous = svc.db.previous_level(data.ticker, ts, day)
    decision = decide_alert(
        _last_state(svc, data.ticker), result, val(data.price), data.updated_at,
        svc.cfg["alerts"], previous_level=previous, data_blocked=data.data_blocked,
    )
    if decision.notify:
        message = format_alert(data, result, decision.kind, decision.reason, svc.cfg.get("broker"))
        deliver(message, svc.cfg)
        svc.db.insert_alert(_alert_row(data, result, decision.kind, decision.reason, True, message))
        return decision.kind
    # Record (without notifying) the first VIGILAR of the day, for later analysis.
    if result.level == LEVEL_VIGILAR and not svc.db.has_watch_event(data.ticker, day):
        svc.db.insert_alert(_alert_row(data, result, KIND_WATCH, "primer VIGILAR del dia", False, ""))
    return None


def run_cycle(svc: Services, now: datetime, phase: str) -> dict[str, Any]:
    started = time.monotonic()
    errors: list[str] = []
    cycle_id = svc.db.insert_cycle(now, phase)

    try:
        refresh_reverse_splits(svc, now)
    except Exception as exc:
        errors.append(f"reverse splits SEC: {exc}")
        log.warning("Refresco reverse splits fallo: %s", exc)
    rs_watch = rs_mod.watchlist(svc.db, now.date(), days=int(svc.cfg["sec"]["reverse_split_lookback_days"]))

    rows_by_ticker = {}
    uni = []
    if phase in (PHASE_PREMARKET, PHASE_REGULAR, PHASE_POSTMARKET):
        uni = cand_mod.universe(nasdaq.fetch_screener(), svc.cfg["universe"])
        # In premarket the screener still shows yesterday: use it only for the universe.
        if phase in (PHASE_REGULAR, PHASE_POSTMARKET):
            rows_by_ticker = {r.ticker: r for r in uni}
        events = {t: rs_mod.resolve_event(None, rows, now.date()) for t, rows in rs_watch.items()}
        daily_cache.build(svc.db, [r.ticker for r in uni], now.date(),
                          {t: e for t, e in events.items() if e})

    allowed = {r.ticker for r in uni}
    limit = int(svc.cfg["universe"]["max_candidates_per_cycle"])
    # Pre-select more than needed: most micro caps are not tradable at the broker (~85 %).
    wide = limit * 4
    if phase in (PHASE_REGULAR, PHASE_POSTMARKET):
        stats = svc.db.daily_stats(now.date().isoformat())
        tickers = cand_mod.select_candidates(
            uni, stats, svc.cfg["universe"]["extra_watchlist"], rs_watch.keys(),
            {**svc.cfg["universe"], "max_candidates_per_cycle": wide},
        )
        if phase == PHASE_POSTMARKET:
            # Most catalysts land after the close: re-check today's movers and detections.
            tickers = _watch_candidates(svc, now, allowed, first=tickers, limit=wide)
    elif phase == PHASE_PREMARKET:
        tickers = _watch_candidates(svc, now, allowed, limit=wide)
    else:
        tickers = list(svc.cfg["universe"]["extra_watchlist"])

    names = {r.ticker: r.name for r in uni}
    tickers, avail = tradable_mod.filter_tradable(
        svc.db, tickers, names, now, svc.cfg.get("broker", {}), limit
    )

    log.info("Ciclo %s (%s): %d candidatos", cycle_id, phase, len(tickers))
    bars = yahoo.intraday_bars(tickers) if tickers else {}

    def work(ticker: str) -> tuple[StockData, ScoreResult] | None:
        try:
            data = enrich(svc, ticker, now, phase, rows_by_ticker.get(ticker), bars.get(ticker),
                          rs_watch.get(ticker, []))
            if ticker in avail:
                av = avail[ticker]
                label = {True: "disponible", False: "no disponible", None: "no comprobado"}[av.available]
                data = replace(data, tradable=Sourced(label, av.evidence, now.date().isoformat()))
            return data, score_stock(data, now, svc.cfg["scoring"])
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")
            log.warning("Error analizando %s: %s", ticker, exc)
            return None

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = [r for r in pool.map(work, tickers) if r is not None]

    svc.db.insert_snapshots([_snapshot_row(cycle_id, d, s) for d, s in results])
    sent = []
    for data, result in sorted(results, key=lambda r: r[1].total, reverse=True):
        kind = handle_alerts(svc, data, result)
        if kind:
            sent.append((data.ticker, kind))

    duration = time.monotonic() - started
    svc.db.finish_cycle(cycle_id, len(results), duration, errors)
    return {"cycle_id": cycle_id, "phase": phase, "analizados": len(results),
            "alertas": sent, "errores": len(errors), "segundos": round(duration, 1)}
