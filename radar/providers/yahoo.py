"""Yahoo Finance via yfinance (unofficial, free). Used for bars, float and news."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


def _split_frames(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    if raw is None or raw.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in level0:
                frame = raw[t].dropna(how="all")
                if not frame.empty:
                    out[t] = frame
    elif len(tickers) == 1:
        out[tickers[0]] = raw.dropna(how="all")
    return out


def download(tickers: list[str], chunk: int = 150, **kwargs: Any) -> dict[str, pd.DataFrame]:
    """Batch download OHLCV, returned per ticker."""
    result: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i: i + chunk]
        try:
            raw = yf.download(
                part, group_by="ticker", progress=False, auto_adjust=False,
                threads=True, **kwargs,
            )
            result.update(_split_frames(raw, part))
        except Exception as exc:  # network/parse errors must not stop the cycle
            log.warning("yfinance download fallo (%s...): %s", part[:3], exc)
        time.sleep(0.5)
    return result


def daily_history(tickers: list[str], period: str = "3mo") -> dict[str, pd.DataFrame]:
    return download(tickers, period=period, interval="1d")


def intraday_bars(tickers: list[str], period: str = "2d") -> dict[str, pd.DataFrame]:
    """5-minute bars including pre/post market, index in America/New_York."""
    return download(tickers, period=period, interval="5m", prepost=True)


def bars_since(ticker: str, start: date) -> pd.DataFrame:
    return yf.Ticker(ticker).history(start=start.isoformat(), interval="5m", prepost=True)


INFO_KEYS = (
    "longName", "shortName", "floatShares", "sharesOutstanding", "marketCap",
    "lastSplitFactor", "lastSplitDate", "regularMarketPrice", "regularMarketPreviousClose",
)


def info(ticker: str, retries: int = 3) -> dict[str, Any]:
    """Quote summary. Yahoo sometimes answers empty under load: retry."""
    for attempt in range(retries):
        try:
            data = yf.Ticker(ticker).info or {}
        except Exception as exc:
            log.warning("yfinance info %s (intento %d): %s", ticker, attempt + 1, exc)
            data = {}
        if any(data.get(k) is not None for k in ("floatShares", "sharesOutstanding", "marketCap")):
            return data
        time.sleep(1.5 * (attempt + 1))
    return {}


def last_split(info_data: dict[str, Any]) -> tuple[date, float] | None:
    """(date, factor new/old) from info. '1:20' -> factor 0.05 (reverse split)."""
    factor_text = info_data.get("lastSplitFactor")
    ts = info_data.get("lastSplitDate")
    if not factor_text or not ts or ":" not in str(factor_text):
        return None
    try:
        new, old = (float(x) for x in str(factor_text).split(":"))
        split_day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    except (ValueError, OSError):
        return None
    if not old:
        return None
    return split_day, new / old


def news(ticker: str) -> list[dict[str, Any]]:
    try:
        items = yf.Ticker(ticker).news or []
    except Exception as exc:
        log.warning("yfinance news %s: %s", ticker, exc)
        return []
    out = []
    for item in items:
        content = item.get("content", item)
        pub = content.get("pubDate") or content.get("displayTime")
        if not pub:
            continue
        try:
            published = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            continue
        url = (content.get("canonicalUrl") or {}).get("url", "") or (
            content.get("clickThroughUrl") or {}
        ).get("url", "")
        out.append({
            "title": content.get("title", ""),
            "summary": content.get("summary", "") or "",
            "provider": (content.get("provider") or {}).get("displayName", "Yahoo Finance"),
            "published": published,
            "url": url,
        })
    return out
