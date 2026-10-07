"""Build a fully-sourced StockData for one ticker."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Mapping

import pandas as pd

from radar import catalysts as cat_mod
from radar.clock import PHASE_POSTMARKET, PHASE_PREMARKET, PHASE_REGULAR
from radar.daily_cache import stats_row
from radar.db import Database
from radar.metrics import intraday_stats
from radar.models import ReverseSplitEvent, Sourced, StockData
from radar.providers import yahoo
from radar.providers import nasdaq
from radar.providers.nasdaq import ExtendedQuote, ScreenerRow
from radar.providers.sec import SecClient
from radar.reverse_splits import resolve_event
from radar.split_guard import check_float, check_live_change, reconcile_prev_close
from radar.ttl_cache import TTLCache

log = logging.getLogger(__name__)

INFO_TTL = 6 * 3600
DAILY_TTL = 3 * 3600
NEWS_TTL = 15 * 60
FILINGS_TTL = 15 * 60
SHARES_TTL = 12 * 3600
EXTENDED_LIVE_TTL = 60
EXTENDED_PRE_IN_SESSION_TTL = 30 * 60

SESSION_LABEL = {"pre": "premarket", "post": "after-hours"}

SRC_NASDAQ = "Nasdaq screener"
SRC_YAHOO = "Yahoo Finance"
SRC_YAHOO_BARS = "Yahoo Finance (barras 5m)"
SRC_YAHOO_DAILY = "Yahoo Finance (historico diario, validado split)"


@dataclass
class Services:
    db: Database
    sec: SecClient | None
    cfg: Mapping[str, Any]
    cache: TTLCache = field(default_factory=TTLCache)


def _src(value: Any, source: str, as_of: str | None, note: str = "") -> Sourced | None:
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
    except TypeError:
        pass
    return Sourced(value, source, as_of, note)


def _sec_shares(svc: Services, cik: int | None) -> Sourced | None:
    if svc.sec is None or cik is None:
        return None
    return svc.cache.get_or_set(f"shares:{cik}", SHARES_TTL, lambda: svc.sec.shares_outstanding(cik))


MAX_SEC_SHARES_AGE_DAYS = 400


def _adjust_sec_shares(
    shares: Sourced | None, event: ReverseSplitEvent | None, today: date
) -> Sourced | None:
    """Drop stale SEC counts; divide counts reported before an executed reverse split."""
    if shares is not None and shares.as_of:
        age = (today - date.fromisoformat(shares.as_of[:10])).days
        if age > MAX_SEC_SHARES_AGE_DAYS:
            return None
    if shares is None or event is None or event.status != "ejecutado" or not event.ratio:
        return shares
    if shares.as_of and shares.as_of < event.event_date.isoformat():
        return Sourced(
            shares.value / event.ratio, shares.source, shares.as_of,
            f"{shares.note}; dividido entre {event.ratio} por reverse split del "
            f"{event.event_date.isoformat()} (calculado)",
        )
    return shares


def _info_with_fallback(svc: Services, ticker: str, stamp: str) -> tuple[dict[str, Any], str]:
    """Yahoo quote summary; if Yahoo does not answer, the last good copy (dated)."""
    fresh = svc.cache.get_or_set(f"info:{ticker}", INFO_TTL, lambda: yahoo.info(ticker), cache_empty=False)
    key = f"yahoo_info:{ticker}"
    if fresh:
        keep = {k: fresh.get(k) for k in yahoo.INFO_KEYS}
        svc.db.set_kv(key, json.dumps({"ts": stamp, "info": keep}))
        return fresh, ""
    stored = svc.db.get_kv(key)
    if stored:
        saved = json.loads(stored)
        return saved["info"], (
            f"Yahoo no respondio: float, acciones y split tomados de la consulta del {saved['ts'][:16]}"
        )
    return {}, "Yahoo no respondio y no hay copia previa: float y split de Yahoo N/D"


def _extended(svc: Services, ticker: str, session: str, ttl: float,
              errors: list[str]) -> ExtendedQuote | None:
    try:
        return svc.cache.get_or_set(
            f"ext:{session}:{ticker}", ttl, lambda: nasdaq.fetch_extended(ticker, session),
            cache_empty=False,
        )
    except Exception as exc:
        errors.append(f"Nasdaq {SESSION_LABEL[session]}: {exc}")
        return None


def _live(svc: Services, ticker: str, errors: list[str]) -> nasdaq.LiveQuote | None:
    try:
        quote = svc.cache.get_or_set(
            f"live:{ticker}", EXTENDED_LIVE_TTL, lambda: nasdaq.fetch_live(ticker), cache_empty=False
        )
    except Exception as exc:
        errors.append(f"Nasdaq tiempo real: {exc}")
        return None
    return quote if quote is not None and quote.price else None


def _catalysts(svc: Services, ticker: str, cik: int | None, now: datetime) -> tuple:
    days = int(svc.cfg["sec"]["catalyst_lookback_days"])
    sec_items = []
    if svc.sec is not None and cik is not None:
        since = (now - timedelta(days=days)).date()
        filings = svc.cache.get_or_set(
            f"filings:{cik}", FILINGS_TTL, lambda: svc.sec.recent_filings(cik, since)
        )
        sec_items = cat_mod.sec_catalysts(filings, svc.db, svc.sec, now.isoformat(timespec="seconds"))
    news = svc.cache.get_or_set(f"news:{ticker}", NEWS_TTL, lambda: yahoo.news(ticker))
    return cat_mod.merge(sec_items, cat_mod.news_catalysts(news, now, days))


def enrich(
    svc: Services,
    ticker: str,
    now: datetime,
    phase: str,
    row: ScreenerRow | None,
    bars: pd.DataFrame | None,
    rs_rows: list[Mapping[str, Any]],
) -> StockData:
    today = now.date()
    stamp = now.isoformat(timespec="minutes")
    notes: list[str] = []
    errors: list[str] = []

    info, info_note = _info_with_fallback(svc, ticker, stamp)
    if info_note:
        notes.append(info_note)
    event = resolve_event(
        yahoo.last_split(info), rs_rows, today, int(svc.cfg["sec"]["reverse_split_lookback_days"])
    )

    # Daily stats per candidate, validated against the resolved split event.
    daily_frames = svc.cache.get_or_set(
        f"daily:{ticker}", DAILY_TTL, lambda: yahoo.daily_history([ticker], period="3mo")
    )
    daily = stats_row(ticker, daily_frames.get(ticker), today, event) if daily_frames else {}
    if daily.get("note"):
        notes.append(daily["note"])
    blocked = bool(daily.get("suspicious"))
    daily_as_of = daily.get("last_session")

    intra = intraday_stats(bars, today)

    # Nasdaq extended-hours data (official, real time; Yahoo reports 0 volume there).
    ext = None
    if phase == PHASE_PREMARKET:
        ext = _extended(svc, ticker, "pre", EXTENDED_LIVE_TTL, errors)
    elif phase == PHASE_POSTMARKET:
        ext = _extended(svc, ticker, "post", EXTENDED_LIVE_TTL, errors)
    pre_ext = ext if phase == PHASE_PREMARKET else (
        _extended(svc, ticker, "pre", EXTENDED_PRE_IN_SESSION_TTL, errors)
        if phase == PHASE_REGULAR else None
    )
    ext_src = f"Nasdaq {SESSION_LABEL[ext.session]}" if ext else ""

    live_q = _live(svc, ticker, errors) if phase == PHASE_REGULAR else None

    # ---- price
    if live_q is not None:
        price = _src(live_q.price, live_q.source, live_q.timestamp or stamp)
    elif phase == PHASE_REGULAR and row is not None and row.price:
        price = _src(row.price, row.source, stamp)
    elif ext is not None:
        price = _src(ext.last, ext_src, ext.updated or stamp)
    elif phase == PHASE_PREMARKET and intra.premarket_last:
        price = _src(intra.premarket_last, SRC_YAHOO_BARS + " premarket", intra.last_bar_time)
    elif intra.last_price:
        price = _src(intra.last_price, SRC_YAHOO_BARS, intra.last_bar_time)
    else:
        price = _src(info.get("regularMarketPrice"), SRC_YAHOO, stamp)

    # ---- previous close, cross-checked between sources
    if phase == PHASE_POSTMARKET:
        # After the close the reference is TODAY's regular close.
        prev_sources = [
            _src(ext.market_close if ext else None, "Nasdaq (cierre de hoy)", stamp),
            _src(row.price if row else None, SRC_NASDAQ + " (cierre de hoy)", stamp),
        ]
    else:
        prev_sources = [_src(daily.get("prev_close"), SRC_YAHOO_DAILY, daily_as_of)]
    if phase == PHASE_REGULAR:
        prev_sources.append(_src(row.prev_close if row else None, row.source if row else SRC_NASDAQ, stamp))
        prev_sources.append(_src(info.get("regularMarketPreviousClose"), SRC_YAHOO, stamp))
    elif phase == PHASE_PREMARKET:
        prev_sources.append(_src(ext.market_close if ext else None, "Nasdaq (ultimo cierre)", stamp))
        prev_sources.append(_src(info.get("regularMarketPrice"), SRC_YAHOO + " (ultimo cierre)", stamp))
    prev_close, prev_note = reconcile_prev_close(prev_sources, event)
    if prev_note:
        notes.append(prev_note)

    prev_value = prev_close.value if prev_close else None
    change = check_live_change(price.value if price else None, prev_value, event, today)
    if change.note:
        notes.append(change.note)
    blocked = blocked or change.suspicious
    if change.adjusted and prev_close is not None:
        prev_close = Sourced(change.prev_close, prev_close.source, prev_close.as_of,
                             "multiplicado por ratio del reverse split (calculado)")

    if pre_ext is not None:
        pm_last, pm_source, pm_time = pre_ext.last, "Nasdaq premarket", pre_ext.updated or stamp
        pm_volume = _src(pre_ext.volume, "Nasdaq premarket", pm_time)
    else:
        pm_last, pm_source, pm_time = intra.premarket_last, SRC_YAHOO_BARS + " premarket", intra.last_bar_time
        pm_volume = _src(intra.premarket_volume, pm_source, pm_time)

    pm_change = None
    if pm_last and change.prev_close and phase != PHASE_POSTMARKET:
        pm_check = check_live_change(pm_last, change.prev_close, event, today)
        pm_change = _src(pm_check.change_pct, pm_source, pm_time)
        blocked = blocked or pm_check.suspicious

    # ---- volume
    if live_q is not None and live_q.volume:
        volume = _src(live_q.volume, live_q.source, live_q.timestamp or stamp)
    elif phase == PHASE_REGULAR and row is not None and row.volume:
        volume = _src(row.volume, row.source, stamp)
    elif phase == PHASE_PREMARKET:
        volume = (_src(pm_volume.value, pm_volume.source, pm_volume.as_of, "volumen premarket acumulado")
                  if pm_volume else None)
    elif phase == PHASE_POSTMARKET and ext is not None:
        volume = _src(ext.volume, ext_src, ext.updated or stamp, "volumen after-hours acumulado")
    else:
        volume = _src(intra.regular_volume, SRC_YAHOO_BARS, intra.last_bar_time)

    # ---- shares / float
    cik = None
    if svc.sec is not None:
        try:
            cik = svc.sec.cik_for(ticker)
        except Exception as exc:
            errors.append(f"SEC CIK: {exc}")
    sec_shares = _adjust_sec_shares(_sec_shares(svc, cik), event, today)
    yahoo_out = _src(info.get("sharesOutstanding"), SRC_YAHOO, stamp,
                     "fecha de referencia no publicada por la fuente")
    outstanding = sec_shares or yahoo_out
    raw_float = _src(info.get("floatShares"), SRC_YAHOO, stamp,
                     "fecha de referencia no publicada por la fuente")
    float_shares, float_note = check_float(raw_float, yahoo_out or outstanding, event, today)
    if float_note:
        notes.append(float_note)

    run_up = None
    lows = [v for v in (daily.get("low_5d"), intra.day_low) if v]
    if price is not None and lows and not blocked:
        run_up = _src(round((price.value / min(lows) - 1) * 100, 1),
                      "calculado (precio / minimo de 5 sesiones)", stamp)

    try:
        catalysts = _catalysts(svc, ticker, cik, now)
    except Exception as exc:
        errors.append(f"catalizadores: {exc}")
        catalysts = ()

    return StockData(
        ticker=ticker,
        updated_at=now,
        phase=phase,
        name=_src(info.get("longName") or info.get("shortName") or (row.name if row else None),
                  SRC_YAHOO, stamp),
        price=price,
        prev_close=prev_close,
        change_pct=_src(change.change_pct, "calculado (precio / cierre anterior)", stamp),
        prev_high=_src(daily.get("prev_high"), SRC_YAHOO_DAILY, daily_as_of),
        day_high=_src(intra.day_high, SRC_YAHOO_BARS, intra.last_bar_time),
        market_cap=_src(info.get("marketCap"), SRC_YAHOO, stamp)
        or _src(row.market_cap if row else None, SRC_NASDAQ, stamp),
        shares_outstanding=outstanding,
        float_shares=float_shares,
        volume=volume,
        avg_volume_20d=_src(daily.get("avg_volume_20d"), SRC_YAHOO_DAILY, daily_as_of),
        premarket_volume=pm_volume,
        premarket_change_pct=pm_change,
        volume_acceleration=_src(intra.volume_acceleration, SRC_YAHOO_BARS, intra.last_bar_time),
        run_up_pct=run_up,
        reverse_split=event,
        catalysts=catalysts,
        quality_notes=tuple(notes),
        data_blocked=blocked,
        errors=tuple(errors),
    )
