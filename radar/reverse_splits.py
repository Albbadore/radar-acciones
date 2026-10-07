"""Reverse split detection: SEC full-text search (announced) + Yahoo (executed)."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Mapping

from radar.db import Database
from radar.models import ReverseSplitEvent
from radar.parsing.split_text import parse_split_text
from radar.providers.sec import SecClient

log = logging.getLogger(__name__)

ANNOUNCE_FORMS = ["8-K", "6-K"]
PROXY_FORMS = ["DEF 14A", "PRE 14A", "DEF 14C", "PRE 14C"]
PHRASES = ["reverse stock split", "reverse split", "share consolidation"]


def refresh_filings(db: Database, sec: SecClient, today: date, cfg: Mapping[str, Any]) -> int:
    """Pull new SEC filings mentioning a reverse split and parse ratio/date."""
    lookback = int(cfg["sec"]["reverse_split_lookback_days"])
    max_docs = int(cfg["sec"]["reverse_split_max_docs_per_refresh"])
    last = db.get_kv("rs_refresh_date")
    start = today - timedelta(days=lookback)
    if last:
        start = max(start, date.fromisoformat(last) - timedelta(days=3))

    known = db.known_rs_accessions()
    hits = {}
    failed = 0
    for phrase in PHRASES:
        found, errors = sec.full_text_search(phrase, ANNOUNCE_FORMS + PROXY_FORMS, start, today)
        failed += errors
        for hit in found:
            hits.setdefault(hit.accession, hit)

    # Only listed companies (with a ticker) matter.
    new = [h for h in hits.values() if h.accession not in known and h.tickers]
    new.sort(key=lambda h: h.filed, reverse=True)
    stored = 0
    for hit in new[:max_docs]:
        parsed, url = _parse_hit(sec, hit)
        if parsed is None:
            continue
        is_proxy = hit.form.upper() in PROXY_FORMS
        # A range in an announcement means the final ratio is not stated.
        ratio = parsed.ratio if (is_proxy or "rango" not in parsed.ratio_text) else None
        db.insert_rs_filing({
            "accession": hit.accession,
            "cik": str(hit.cik),
            "tickers": ",".join(hit.tickers),
            "company": hit.company,
            "form": hit.form,
            "filed_date": hit.filed.isoformat(),
            "items": hit.items,
            "ratio": ratio,
            "ratio_text": parsed.ratio_text,
            # A proxy date is the meeting/record date, not the split date.
            "effective_date": (
                parsed.effective_date.isoformat()
                if parsed.effective_date and not is_proxy else None
            ),
            "url": url,
        })
        stored += 1
    if failed == 0 and len(new) <= max_docs:
        db.set_kv("rs_refresh_date", today.isoformat())
    return stored


def _parse_hit(sec: SecClient, hit, max_docs: int = 3):
    """Parse the filing's documents until one states a ratio."""
    first = None
    for url in hit.urls[:max_docs]:
        try:
            parsed = parse_split_text(sec.document_text(url))
        except Exception as exc:
            log.warning("Documento SEC %s fallo: %s", url, exc)
            continue
        if parsed.ratio:
            return parsed, url
        first = first or (parsed, url)
    return first if first else (None, "")


def watchlist(db: Database, today: date, days: int = 90) -> dict[str, list[dict[str, Any]]]:
    """Tickers with reverse-split filings in the last `days` days."""
    since = (today - timedelta(days=days)).isoformat()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in db.rs_filings_since(since):
        # A filing that mentions "reverse split" without stating a ratio is
        # usually boilerplate (warrant terms, risk factors): not an event.
        if not row.get("ratio_text"):
            continue
        for ticker in filter(None, (row["tickers"] or "").split(",")):
            out.setdefault(ticker.replace(".", "-"), []).append(row)
    return out


def _sec_event(row: Mapping[str, Any], today: date) -> ReverseSplitEvent:
    filed = date.fromisoformat(row["filed_date"])
    eff = date.fromisoformat(row["effective_date"]) if row.get("effective_date") else None
    is_proxy = row["form"].upper() in PROXY_FORMS
    if is_proxy:
        status, event_date = "propuesto", filed
    elif eff and eff <= today:
        status, event_date = "ejecutado", eff
    else:
        status, event_date = "anunciado", filed
    return ReverseSplitEvent(
        status=status,
        event_date=event_date,
        ratio=row.get("ratio"),
        source=f"SEC {row['form']} ({row['filed_date']})",
        url=row.get("url", ""),
        ratio_text=row.get("ratio_text") or "",
        filed_date=filed,
        effective_date=eff,
    )


_STATUS_RANK = {"ejecutado": 3, "anunciado": 2, "propuesto": 1}


def resolve_event(
    yahoo_split: tuple[date, float] | None,
    sec_rows: list[Mapping[str, Any]],
    today: date,
    lookback_days: int = 180,
) -> ReverseSplitEvent | None:
    """Most relevant reverse split event combining Yahoo (executed) and SEC."""
    sec_events = [_sec_event(r, today) for r in sec_rows]
    candidates: list[ReverseSplitEvent] = []

    if yahoo_split and yahoo_split[1] < 1:
        split_day, factor = yahoo_split
        if 0 <= (today - split_day).days <= lookback_days:
            ratio = round(1 / factor)
            match = next(
                (e for e in sec_events if e.status != "propuesto"
                 and abs(((e.effective_date or e.filed_date or e.event_date) - split_day).days) <= 30),
                None,
            )
            candidates.append(ReverseSplitEvent(
                status="ejecutado",
                event_date=split_day,
                ratio=ratio,
                source="Yahoo Finance (historico de splits)" + (f" + {match.source}" if match else ""),
                url=match.url if match else "",
                ratio_text=f"1:{ratio}",
                filed_date=match.filed_date if match else None,
                effective_date=split_day,
            ))

    candidates.extend(
        e for e in sec_events if (today - e.event_date).days <= lookback_days
    )
    if not candidates:
        return None
    # Executed beats announced beats proposed; within a status, the most recent.
    return max(candidates, key=lambda e: (_STATUS_RANK[e.status], e.event_date))
