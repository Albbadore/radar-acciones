"""Nasdaq public stock screener (NASDAQ, NYSE and AMEX listings)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests

SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
EXTENDED_URL = "https://api.nasdaq.com/api/quote/{symbol}/extended-trading"
_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
_PCT = re.compile(r"\(([+-]?[\d.,]+)%\)")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
}


@dataclass(frozen=True)
class ScreenerRow:
    ticker: str          # Yahoo-style symbol (BRK-B)
    name: str
    price: float | None
    change_pct: float | None
    net_change: float | None
    volume: float | None
    market_cap: float | None

    @property
    def prev_close(self) -> float | None:
        if self.price is None or self.net_change is None:
            return None
        prev = self.price - self.net_change
        return prev if prev > 0 else None


def _num(text: str | None) -> float | None:
    if text in (None, "", "NA", "N/A"):
        return None
    try:
        return float(str(text).replace("$", "").replace(",", "").replace("%", ""))
    except ValueError:
        return None


def parse_rows(rows: list[dict]) -> list[ScreenerRow]:
    out = []
    for r in rows:
        symbol = (r.get("symbol") or "").strip()
        if not symbol or "^" in symbol:
            continue
        out.append(ScreenerRow(
            ticker=symbol.replace("/", "-"),
            name=(r.get("name") or "").strip(),
            price=_num(r.get("lastsale")),
            change_pct=_num(r.get("pctchange")),
            net_change=_num(r.get("netchange")),
            volume=_num(r.get("volume")),
            market_cap=_num(r.get("marketCap")),
        ))
    return out


def fetch_screener(session: requests.Session | None = None) -> list[ScreenerRow]:
    sess = session or requests.Session()
    resp = sess.get(
        SCREENER_URL, params={"tableonly": "true", "download": "true"}, headers=HEADERS, timeout=40
    )
    resp.raise_for_status()
    return parse_rows(resp.json()["data"]["rows"])


@dataclass(frozen=True)
class ExtendedQuote:
    """Nasdaq pre-market ("pre") or after-hours ("post") consolidated data."""

    session: str
    last: float | None
    change_pct: float | None
    volume: float | None
    high: float | None
    low: float | None
    market_close: float | None   # regular close the change refers to
    updated: str


def _money(text: str | None) -> float | None:
    match = _MONEY.search(text or "")
    return float(match.group(1).replace(",", "")) if match else None


def parse_extended(data: dict | None, session: str) -> ExtendedQuote | None:
    if not data:
        return None
    rows = (data.get("infoTable") or {}).get("rows") or []
    if not rows:
        return None
    row = rows[0]
    pct = _PCT.search(row.get("consolidated") or "")
    updated = next((u for u in data.get("lastUpdateInfo") or [] if "updated" in u), "")
    quote = ExtendedQuote(
        session=session,
        last=_money(row.get("consolidated")),
        change_pct=float(pct.group(1).replace(",", "")) if pct else None,
        volume=_num(row.get("volume")),
        high=_money(row.get("highPrice")),
        low=_money(row.get("lowPrice")),
        market_close=_money(data.get("previousInfo")),
        updated=updated.replace("Data last updated", "").strip().rstrip("."),
    )
    return quote if quote.last else None


def fetch_extended(ticker: str, session: str, sess: requests.Session | None = None) -> ExtendedQuote | None:
    """session: "pre" (04:00-09:30 ET) or "post" (16:00-20:00 ET)."""
    client = sess or requests
    resp = client.get(
        EXTENDED_URL.format(symbol=ticker.replace("-", ".")),
        params={"markettype": session, "assetclass": "stocks", "time": 0},
        headers=HEADERS, timeout=20,
    )
    resp.raise_for_status()
    return parse_extended(resp.json().get("data"), session)


INFO_URL = "https://api.nasdaq.com/api/quote/{symbol}/info"


@dataclass(frozen=True)
class LiveQuote:
    price: float | None
    change_pct: float | None
    volume: float | None
    timestamp: str
    source: str


def parse_live(data: dict | None) -> LiveQuote | None:
    primary = (data or {}).get("primaryData") or {}
    price = _money(primary.get("lastSalePrice"))
    if not price:
        return None
    return LiveQuote(
        price=price,
        change_pct=_num(primary.get("percentageChange")),
        volume=_num(primary.get("volume")),
        timestamp=primary.get("lastTradeTimestamp") or "",
        source="Nasdaq tiempo real" if primary.get("isRealTime") else "Nasdaq",
    )


def fetch_live(ticker: str, sess: requests.Session | None = None) -> LiveQuote | None:
    """Last trade (includes pre/after-hours session) and session volume."""
    client = sess or requests
    resp = client.get(INFO_URL.format(symbol=ticker.replace("-", ".")),
                      params={"assetclass": "stocks"}, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return parse_live(resp.json().get("data"))
