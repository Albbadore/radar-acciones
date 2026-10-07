"""Collect catalysts of the last N days: SEC filings first, then press/news."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from radar.clock import ET
from radar.db import Database
from radar.models import Catalyst
from radar.parsing.classifier import (
    classify_form,
    classify_items,
    classify_text,
    filing_body,
    is_press_wire,
    is_split_only,
)
from radar.providers.sec import Filing, SecClient

log = logging.getLogger(__name__)

TEXT_ITEMS = {"7.01", "8.01"}


def _filed_dt(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 8, 0, tzinfo=ET)


def _wants_text(form: str, items: str) -> bool:
    """Keyword-classify only press-release-like filings.

    An 8-K about a director resignation (item 5.02) may mention "merger
    agreement" in passing; that is not a merger catalyst. Only 6-K and 8-K
    items 7.01 / 8.01 (company announcements) get text classification.
    """
    base = form.split("/")[0]
    if base == "6-K":
        return True
    codes = {c.strip() for c in items.split(",")}
    return base == "8-K" and bool(codes & TEXT_ITEMS)


def filing_categories(filing: Filing, db: Database, sec: SecClient, now_iso: str) -> list[tuple[str, str]]:
    """Categories for a filing: form type, 8-K items and text keywords (cached)."""
    cats: list[tuple[str, str]] = []
    form = filing.form.upper()
    if form.startswith("8-K"):
        if is_split_only(filing.items):
            return []
        cats.extend(classify_items(filing.items))
    else:
        by_form = classify_form(form)
        if by_form:
            cats.append(by_form)

    if _wants_text(form, filing.items):
        cached = db.get_doc_categories(filing.accession)
        if cached is None:
            try:
                text = sec.document_text(filing.url, max_chars=30000)
                cached = [list(c) for c in classify_text(filing_body(text))]
            except Exception as exc:
                log.warning("Texto SEC %s: %s", filing.url, exc)
                cached = []
            db.set_doc_categories(filing.accession, cached, now_iso)
        # Text categories are more specific (e.g. FDA) -> put them first.
        cats = [tuple(c) for c in cached] + [c for c in cats if list(c) not in cached]
    return cats


def _filing_title(filing: Filing) -> str:
    desc = filing.description if filing.description and filing.description != filing.form else ""
    items = f"items {filing.items}" if filing.items else ""
    return " ".join(p for p in (filing.form, desc, items) if p)


def sec_catalysts(filings: Iterable[Filing], db: Database, sec: SecClient, now_iso: str) -> list[Catalyst]:
    out: list[Catalyst] = []
    for filing in filings:
        for category, impact in filing_categories(filing, db, sec, now_iso)[:2]:
            out.append(Catalyst(
                published=_filed_dt(filing.filed),
                category=category,
                impact=impact,
                title=_filing_title(filing),
                source=f"SEC {filing.form}",
                official=True,
                url=filing.url,
            ))
    return out


def news_catalysts(items: Iterable[dict[str, Any]], now: datetime, days: int = 7) -> list[Catalyst]:
    """Classify news headlines. Unclassified items count only if they are
    company press releases (distributed via a press wire)."""
    cutoff = now - timedelta(days=days)
    out: list[Catalyst] = []
    for item in items:
        published = item["published"]
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if published < cutoff:
            continue
        official = is_press_wire(item.get("provider", ""))
        cats = classify_text(f"{item.get('title', '')} {item.get('summary', '')}")
        if not cats:
            if not official:
                continue
            cats = [("Comunicado oficial de la empresa", "medio")]
        category, impact = cats[0]
        out.append(Catalyst(
            published=published.astimezone(ET),
            category=category,
            impact=impact,
            title=item.get("title", ""),
            source=item.get("provider", "N/D"),
            official=official,
            url=item.get("url", ""),
        ))
    return out


def merge(sec_items: list[Catalyst], news_items: list[Catalyst]) -> tuple[Catalyst, ...]:
    """Official first, newest first; drop exact duplicates."""
    seen: set[tuple[str, str, str]] = set()
    merged: list[Catalyst] = []
    for cat in sorted(sec_items + news_items, key=lambda c: (not c.official, -c.published.timestamp())):
        key = (cat.category, cat.published.date().isoformat(), cat.source)
        if key not in seen:
            seen.add(key)
            merged.append(cat)
    return tuple(merged)
