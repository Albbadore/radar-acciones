"""SEC EDGAR client (official, free). Respects the 10 req/s fair-access limit."""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests

from radar.models import Sourced

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/dei/{concept}.json"
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_DISPLAY_TICKERS = re.compile(r"\(([A-Z0-9.\-, ]+)\)\s*\(CIK")


@dataclass(frozen=True)
class Filing:
    form: str
    filed: date
    accession: str
    items: str
    primary_doc: str
    description: str
    url: str


@dataclass(frozen=True)
class SearchHit:
    accession: str
    cik: int
    tickers: tuple[str, ...]
    company: str
    form: str
    filed: date
    items: str
    urls: tuple[str, ...]  # main document first, then exhibits


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    return _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", raw))).strip()


def tickers_from_display(name: str) -> tuple[str, ...]:
    match = _DISPLAY_TICKERS.search(name)
    if not match:
        return ()
    return tuple(t.strip() for t in match.group(1).split(",") if t.strip())


class SecClient:
    def __init__(self, user_agent: str, cache_dir: str, min_interval_s: float = 0.15,
                 session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval_s
        self._lock = threading.Lock()
        self._last = 0.0
        self._ticker_map: dict[str, int] | None = None

    # ---------------------------------------------------------------- http
    def _get(self, url: str, params: dict | None = None, retries: int = 3) -> requests.Response:
        for attempt in range(retries):
            with self._lock:
                wait = self.min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError(f"SEC no disponible: {url}")

    # ---------------------------------------------------------------- tickers
    def ticker_map(self) -> dict[str, int]:
        if self._ticker_map is not None:
            return self._ticker_map
        cache = self.cache_dir / "company_tickers.json"
        fresh = cache.exists() and time.time() - cache.stat().st_mtime < 86400
        if fresh:
            data = json.loads(cache.read_text(encoding="utf-8"))
        else:
            data = self._get(TICKERS_URL).json()
            cache.write_text(json.dumps(data), encoding="utf-8")
        self._ticker_map = {
            row["ticker"].upper().replace(".", "-"): int(row["cik_str"]) for row in data.values()
        }
        return self._ticker_map

    def cik_for(self, ticker: str) -> int | None:
        return self.ticker_map().get(ticker.upper().replace(".", "-"))

    # ---------------------------------------------------------------- filings
    def recent_filings(self, cik: int, since: date) -> list[Filing]:
        data = self._get(SUBMISSIONS_URL.format(cik=cik)).json()
        recent = data.get("filings", {}).get("recent", {})
        out: list[Filing] = []
        for i, form in enumerate(recent.get("form", [])):
            filed = date.fromisoformat(recent["filingDate"][i])
            if filed < since:
                break
            acc = recent["accessionNumber"][i]
            doc = recent["primaryDocument"][i]
            out.append(Filing(
                form=form,
                filed=filed,
                accession=acc,
                items=recent.get("items", [""] * (i + 1))[i] or "",
                primary_doc=doc,
                description=(recent.get("primaryDocDescription", [""] * (i + 1))[i] or ""),
                url=ARCHIVE_URL.format(cik=cik, acc_nodash=acc.replace("-", ""), doc=doc),
            ))
        return out

    def document_text(self, url: str, max_chars: int = 60000) -> str:
        return html_to_text(self._get(url).text)[:max_chars]

    def shares_outstanding(self, cik: int) -> Sourced | None:
        """Latest dei:EntityCommonStockSharesOutstanding (cover page of 10-Q/10-K)."""
        try:
            data = self._get(CONCEPT_URL.format(cik=cik, concept="EntityCommonStockSharesOutstanding")).json()
        except requests.HTTPError:
            return None
        facts = data.get("units", {}).get("shares", [])
        if not facts:
            return None
        latest = max(facts, key=lambda f: (f.get("end", ""), f.get("filed", "")))
        return Sourced(
            float(latest["val"]),
            f"SEC {latest.get('form', '')} (dei:EntityCommonStockSharesOutstanding)",
            latest.get("end"),
            f"presentado {latest.get('filed', 'N/D')}",
        )

    # ---------------------------------------------------------------- full-text search
    def full_text_search(self, phrase: str, forms: list[str], start: date, end: date,
                         chunk_days: int = 20) -> tuple[list[SearchHit], int]:
        """Search in date chunks (EDGAR fails on deep pagination).

        Returns (hits, failed_chunks); partial results are kept if a chunk fails.
        """
        hits: dict[str, SearchHit] = {}
        failed = 0
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + timedelta(days=chunk_days - 1))
            try:
                self._search_chunk(phrase, forms, chunk_start, chunk_end, hits)
            except Exception as exc:
                failed += 1
                log.warning("SEC busqueda '%s' %s..%s fallo: %s", phrase, chunk_start, chunk_end, exc)
            chunk_start = chunk_end + timedelta(days=1)
        return list(hits.values()), failed

    def _search_chunk(self, phrase: str, forms: list[str], start: date, end: date,
                      hits: dict[str, SearchHit], max_pages: int = 8) -> None:
        offset = 0
        for _ in range(max_pages):
            params = {
                "q": f'"{phrase}"', "forms": ",".join(forms), "dateRange": "custom",
                "startdt": start.isoformat(), "enddt": end.isoformat(), "from": offset,
            }
            payload = self._get(EFTS_URL, params=params).json()
            page = payload.get("hits", {}).get("hits", [])
            if not page:
                break
            for hit in page:
                src = hit.get("_source", {})
                acc = src.get("adsh", "")
                if not acc or not src.get("ciks"):
                    continue
                doc = hit.get("_id", ":").split(":", 1)[1]
                cik = int(src["ciks"][0])
                url = ARCHIVE_URL.format(cik=cik, acc_nodash=acc.replace("-", ""), doc=doc)
                form = (src.get("root_forms") or [src.get("form", "")])[0]
                is_main = src.get("file_type", "").upper().startswith(form.upper())
                if acc in hits:
                    prev = hits[acc]
                    urls = (url, *prev.urls) if is_main else (*prev.urls, url)
                    hits[acc] = SearchHit(prev.accession, prev.cik, prev.tickers, prev.company,
                                          prev.form, prev.filed, prev.items, urls)
                    continue
                names = src.get("display_names") or [""]
                hits[acc] = SearchHit(
                    accession=acc,
                    cik=cik,
                    tickers=tickers_from_display(names[0]),
                    company=names[0].split("(")[0].strip(),
                    form=form,
                    filed=date.fromisoformat(src["file_date"]),
                    items=",".join(src.get("items") or []),
                    urls=(url,),
                )
            offset += len(page)
            total = payload.get("hits", {}).get("total", {}).get("value", 0)
            if offset >= total:
                break
