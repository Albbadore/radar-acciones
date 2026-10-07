"""Keep only stocks the user can actually buy (Trade Republic via LS Exchange)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from radar.db import Database
from radar.providers import broker

log = logging.getLogger(__name__)

Checker = Callable[[str, str, "str | None"], broker.Availability]


def _default_checker(ticker: str, name: str, isin: str | None) -> broker.Availability:
    return broker.check_ls(ticker, name, isin if isin is not None else broker.yahoo_isin(ticker))


def availability(
    db: Database, tickers: list[str], names: Mapping[str, str], now: datetime,
    cfg: Mapping[str, Any], checker: Checker = _default_checker,
) -> dict[str, broker.Availability]:
    """Availability per ticker, from cache (recheck_days) or a fresh lookup."""
    allow = {t.upper() for t in cfg.get("always_allow") or []}
    block = {t.upper() for t in cfg.get("always_block") or []}
    cutoff = (now - timedelta(days=int(cfg.get("recheck_days", 7)))).isoformat()
    cached = db.broker_availability(tickers)

    out: dict[str, broker.Availability] = {}
    todo: list[str] = []
    for t in tickers:
        if t in allow:
            out[t] = broker.Availability(True, "permitido en config (always_allow)")
        elif t in block:
            out[t] = broker.Availability(False, "excluido en config (always_block)")
        elif t in cached and cached[t]["checked_at"] >= cutoff:
            row = cached[t]
            out[t] = broker.Availability(bool(row["available"]), row["evidence"])
        else:
            todo.append(t)

    if todo:
        with ThreadPoolExecutor(max_workers=6) as pool:
            fresh = list(pool.map(lambda t: checker(t, names.get(t, ""), None), todo))
        for t, av in zip(todo, fresh):
            out[t] = av
            if av.available is not None:  # never cache a failed lookup
                db.set_broker_availability(t, av.available, av.evidence, now.isoformat())
    return out


# Bump when the matching rules change: positive results from older rules are rechecked.
# v2: an ISIN mismatch is final and one shared name word is not a match (ALP != ALPHA SYSTEMS).
CHECK_VERSION = "2"


def _invalidate_old_checks(db: Database) -> None:
    if db.get_kv("broker_check_version") == CHECK_VERSION:
        return
    with db.tx() as c:
        c.execute("DELETE FROM broker_availability WHERE available = 1")
    db.set_kv("broker_check_version", CHECK_VERSION)
    log.info("Comprobaciones de broker anteriores invalidadas (reglas v%s)", CHECK_VERSION)


def filter_tradable(
    db: Database, tickers: list[str], names: Mapping[str, str], now: datetime,
    cfg: Mapping[str, Any], limit: int, checker: Checker = _default_checker,
) -> tuple[list[str], dict[str, broker.Availability]]:
    """Drop tickers not available at the broker. Unknown (lookup failed) are kept."""
    if not cfg.get("only_tradable"):
        return tickers[:limit], {}
    _invalidate_old_checks(db)
    avail = availability(db, tickers, names, now, cfg, checker)
    kept = [t for t in tickers if avail[t].available is not False]
    dropped = len(tickers) - len(kept)
    if dropped:
        log.info("Descartados %d valores no disponibles en el broker", dropped)
    return kept[:limit], avail
