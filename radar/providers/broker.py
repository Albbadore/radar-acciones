"""Is a stock tradable at the user's broker?

Trade Republic has no public API; it executes US stocks on LS Exchange
(Lang & Schwarz), whose instrument search is public. A stock found there by
ISIN (or by ticker with a matching company name) is considered tradable.
This is an approximation: the user can override it in config.yaml.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests
import yfinance as yf

log = logging.getLogger(__name__)

LS_SEARCH_URL = "https://www.ls-tc.de/_rpc/json/.lstc/instrument/search/main"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}
_WORD = re.compile(r"[A-Z0-9]+")


@dataclass(frozen=True)
class Availability:
    available: bool | None   # None = could not check
    evidence: str


def ls_search(query: str) -> list[dict]:
    resp = requests.get(LS_SEARCH_URL, params={"localeId": 2, "q": query}, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return [h for h in resp.json() or [] if h.get("categorySymbol") == "STK"]


def yahoo_isin(ticker: str) -> str | None:
    try:
        isin = yf.Ticker(ticker).isin
    except Exception:
        return None
    return isin if isin and len(isin) == 12 else None


GENERIC = {"INC", "CORP", "CORPORATION", "CO", "LTD", "LIMITED", "PLC", "SA", "NV", "AG",
           "HOLDINGS", "HOLDING", "HLDGS", "HLD", "GROUP", "THE", "NEW", "CLASS", "CL", "A", "DL"}


def _key_words(name: str, n: int = 2) -> list[str]:
    words = [w for w in _WORD.findall((name or "").upper()) if w not in GENERIC and not w.isdigit()]
    return words[:n]


def _same_company(ours: str, theirs: str) -> bool:
    """Same significant words, up to two (LS truncates: ENTERTAINM ~ ENTERTAINMENT).

    One shared word is not enough: "Alpha Compute Corp" is not "ALPHA SYSTEMS"
    and "Capstone Holding" is not "CAPSTONE COPPER".
    """
    a, b = _key_words(ours), _key_words(theirs)
    if not a or len(a) != len(b):
        return False
    return all(x.startswith(y) or y.startswith(x) for x, y in zip(a, b))


def check_ls(ticker: str, name: str, isin: str | None) -> Availability:
    """Look the stock up on LS Exchange.

    With a known ISIN only an exact ISIN match counts (names are ambiguous).
    Without ISIN, a ticker search hit must match the company name closely.
    """
    try:
        if isin:
            if any(h.get("isin") == isin for h in ls_search(isin)):
                return Availability(True, f"LS Exchange (ISIN {isin})")
            return Availability(False, f"no encontrado en LS Exchange (ISIN {isin})")
        for hit in ls_search(ticker):
            if _same_company(name, hit.get("displayname", "")):
                return Availability(True, f"LS Exchange ({hit.get('displayname')}, {hit.get('isin')})")
    except Exception as exc:
        log.warning("LS Exchange %s: %s", ticker, exc)
        return Availability(None, f"no comprobado: {type(exc).__name__}")
    return Availability(False, "no encontrado en LS Exchange" + (f" (ISIN {isin})" if isin else ""))
