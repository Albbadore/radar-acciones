"""Enrichment + scan cycle with all network providers faked."""
from datetime import date, datetime

import pandas as pd
import pytest

from radar import scanner
from radar.clock import ET
from radar.db import Database
from radar.enrich import Services, enrich
from radar.providers import broker, nasdaq, yahoo
from radar.providers.nasdaq import ScreenerRow
from radar.providers.sec import Filing
from radar.scoring import LEVEL_MAXIMA

NOW = datetime(2026, 9, 24, 10, 30, tzinfo=ET)


def daily_frame(pre_close=0.3, post_close=5.2, pre_vol=2_000_000, post_vol=150_000):
    """Unadjusted history: 1:20 split on 2026-09-22 still raw in the source."""
    idx = pd.bdate_range("2026-08-10", "2026-09-23", tz="America/New_York")
    split = pd.Timestamp("2026-09-22", tz="America/New_York")
    closes = [pre_close if ts < split else post_close for ts in idx]
    vols = [pre_vol if ts < split else post_vol for ts in idx]
    return pd.DataFrame({"Open": closes, "High": [c * 1.05 for c in closes],
                         "Low": [c * 0.95 for c in closes], "Close": closes, "Volume": vols}, index=idx)


def intraday_frame():
    idx = pd.date_range("2026-09-24 08:00", "2026-09-24 10:25", freq="5min", tz="America/New_York")
    closes = [5.5 + 0.02 * i for i in range(len(idx))]
    vols = [3000] * (len(idx) - 3) + [90000, 120000, 150000]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": vols},
                        index=idx)


class FakeSec:
    def cik_for(self, ticker):
        return 2038439

    def shares_outstanding(self, cik):
        from radar.models import Sourced
        return Sourced(28_842_069.0, "SEC 10-Q (dei)", "2026-08-17", "presentado 2026-08-19")

    def recent_filings(self, cik, since):
        return [Filing("8-K", date(2026, 9, 23), "0001-26-1", "1.01,9.01", "a.htm", "",
                       "https://www.sec.gov/a.htm")]

    def document_text(self, url, max_chars=30000):
        return "Item 1.01 The Company entered into a supply agreement and purchase order. SIGNATURES"


@pytest.fixture
def fake_yahoo(monkeypatch):
    info = {"longName": "VisionWave Holdings", "floatShares": 1_101_550, "sharesOutstanding": 2_373_771,
            "marketCap": 13_577_970, "lastSplitFactor": "1:20", "lastSplitDate": 1790035200,
            "regularMarketPreviousClose": 5.2}
    monkeypatch.setattr(yahoo, "info", lambda t: info)
    monkeypatch.setattr(yahoo, "daily_history", lambda ts, period="3mo": {t: daily_frame() for t in ts})
    monkeypatch.setattr(yahoo, "news", lambda t: [{
        "title": "VisionWave awarded defense contract", "summary": "", "provider": "GlobeNewswire",
        "published": datetime(2026, 9, 24, 12, 0, tzinfo=ET), "url": "https://x"}])
    monkeypatch.setattr(yahoo, "intraday_bars", lambda ts, period="2d": {t: intraday_frame() for t in ts})
    monkeypatch.setattr(nasdaq, "fetch_extended", lambda t, s: None)
    monkeypatch.setattr(broker, "yahoo_isin", lambda t: "US0000000001")
    monkeypatch.setattr(broker, "check_ls", lambda t, n, i: broker.Availability(True, "LS test"))


@pytest.fixture
def svc(cfg):
    return Services(db=Database(":memory:"), sec=FakeSec(), cfg=cfg)


def screener_row():
    # Nasdaq shows an unadjusted previous close (0.30) -> fake +1900 %.
    return ScreenerRow("VWAV", "VisionWave", 6.0, 1900.0, 5.7, 3_000_000, 13e6)


class TestEnrich:
    def test_full_enrichment_with_split_guard(self, svc, fake_yahoo):
        data = enrich(svc, "VWAV", NOW, "regular", screener_row(), intraday_frame(), [])
        assert data.reverse_split.status == "ejecutado" and data.reverse_split.ratio == 20
        # Fake +1900 % is corrected to the real move vs adjusted close (6.0/5.2).
        assert data.change_pct.value == pytest.approx(15.38, abs=0.1)
        # Pre-split volume divided by 20 before averaging.
        assert data.avg_volume_20d.value < 200_000
        assert data.shares_outstanding.value == pytest.approx(28_842_069 / 20)
        assert data.float_shares.value == 1_101_550
        assert any("sin ajustar" in n for n in data.quality_notes)
        cats = {c.category for c in data.catalysts}
        assert "Contrato" in cats
        assert not data.data_blocked

    def test_premarket_uses_bars(self, svc, fake_yahoo):
        data = enrich(svc, "VWAV", datetime(2026, 9, 24, 9, 0, tzinfo=ET), "premarket", None,
                      intraday_frame().iloc[:12], [])
        assert data.price.source.endswith("premarket")
        assert data.volume.note == "volumen premarket acumulado"


class TestCycle:
    def test_regular_cycle_alerts_once(self, svc, fake_yahoo, monkeypatch, cfg):
        monkeypatch.setattr(scanner.nasdaq, "fetch_screener", lambda: [screener_row()])
        monkeypatch.setattr(scanner.rs_mod, "refresh_filings", lambda *a, **k: 0)
        sent = []
        monkeypatch.setattr(scanner, "deliver", lambda text, cfg: sent.append(text) or ["log"])

        out = scanner.run_cycle(svc, NOW, "regular")
        assert out["analizados"] == 1 and out["alertas"] == [("VWAV", "INICIAL")]
        assert "VWAV" in sent[0] and "No constituye una recomendacion de compra" in sent[0]

        snap = svc.db.snapshots_for_cycle(out["cycle_id"])[0]
        assert snap["level"] == LEVEL_MAXIMA and snap["rs_ratio"] == 20

        # Same data 5 minutes later -> cooldown, no repeated alert.
        again = scanner.run_cycle(svc, NOW.replace(minute=35), "regular")
        assert again["alertas"] == []
        assert len(svc.db.alerts(notified_only=True)) == 1

    def test_premarket_cycle_uses_watchlists(self, svc, fake_yahoo, monkeypatch, cfg):
        monkeypatch.setattr(scanner.nasdaq, "fetch_screener", lambda: [screener_row()])
        monkeypatch.setattr(scanner.rs_mod, "refresh_filings", lambda *a, **k: 0)
        monkeypatch.setattr(scanner, "deliver", lambda text, cfg: ["log"])
        svc.db.insert_rs_filing({"accession": "a1", "cik": "1", "tickers": "VWAV", "company": "V",
                                 "form": "8-K", "filed_date": "2026-09-18", "items": "", "ratio": 20,
                                 "ratio_text": "1:20", "effective_date": "2026-09-22", "url": ""})
        out = scanner.run_cycle(svc, datetime(2026, 9, 24, 8, 30, tzinfo=ET), "premarket")
        assert out["analizados"] == 1

    def test_errors_do_not_stop_cycle(self, svc, monkeypatch):
        monkeypatch.setattr(scanner.rs_mod, "refresh_filings",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sec down")))
        monkeypatch.setattr(scanner.yahoo, "intraday_bars", lambda ts: {})
        monkeypatch.setattr(broker, "yahoo_isin", lambda t: None)
        monkeypatch.setattr(broker, "check_ls", lambda t, n, i: broker.Availability(True, "LS test"))
        monkeypatch.setattr(scanner, "enrich", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
        svc.cfg["universe"]["extra_watchlist"].append("ZZZ")
        try:
            out = scanner.run_cycle(svc, NOW, "cerrado")
        finally:
            svc.cfg["universe"]["extra_watchlist"].remove("ZZZ")
        assert out["errores"] == 2 and out["analizados"] == 0


def test_yahoo_outage_uses_last_good_info(svc, fake_yahoo, monkeypatch):
    """GCTK scan lost split + float when Yahoo answered empty under load."""
    first = enrich(svc, "VWAV", NOW, "regular", screener_row(), intraday_frame(), [])
    assert first.float_shares.value == 1_101_550

    svc.cache = type(svc.cache)()  # new process / expired cache
    monkeypatch.setattr(yahoo, "info", lambda t: {})
    again = enrich(svc, "VWAV", NOW, "regular", screener_row(), intraday_frame(), [])
    assert again.float_shares.value == 1_101_550
    assert again.reverse_split.ratio == 20
    assert any("Yahoo no respondio" in n for n in again.quality_notes)


def test_empty_values_not_cached():
    from radar.ttl_cache import TTLCache
    cache, calls = TTLCache(), []
    cache.get_or_set("k", 60, lambda: calls.append(1) or {}, cache_empty=False)
    cache.get_or_set("k", 60, lambda: calls.append(1) or {}, cache_empty=False)
    assert len(calls) == 2


class TestExtendedHours:
    """GCTK 2026-09-23/24: +80 % after hours on 27.3M shares, +118 % premarket on 8.2M."""

    def quote(self, session, last, pct, vol, close):
        from radar.providers.nasdaq import ExtendedQuote
        return ExtendedQuote(session, last, pct, vol, None, None, close, "Sep 24, 2026 05:04 AM ET")

    def test_premarket_uses_nasdaq_volume(self, svc, fake_yahoo, monkeypatch):
        monkeypatch.setattr(nasdaq, "fetch_extended", lambda t, s: self.quote("pre", 11.3, 117.3, 8_223_515, 5.2))
        data = enrich(svc, "VWAV", datetime(2026, 9, 24, 5, 4, tzinfo=ET), "premarket", None, None, [])
        assert data.price.source == "Nasdaq premarket" and data.price.value == 11.3
        assert data.volume.value == 8_223_515 and data.premarket_volume.value == 8_223_515
        assert data.change_pct.value == pytest.approx(117.3, abs=0.1)

    def test_postmarket_change_vs_todays_close(self, svc, fake_yahoo, monkeypatch):
        monkeypatch.setattr(nasdaq, "fetch_extended", lambda t, s: self.quote("post", 9.36, 80.0, 27_301_978, 5.2))
        row = ScreenerRow("VWAV", "VisionWave", 5.2, 0.0, 0.0, 3_000_000, 13e6)
        data = enrich(svc, "VWAV", datetime(2026, 9, 24, 17, 0, tzinfo=ET), "postmarket", row, None, [])
        assert data.price.source == "Nasdaq after-hours"
        assert data.change_pct.value == pytest.approx(80.0, abs=0.1)
        assert data.volume.note == "volumen after-hours acumulado"


def test_parse_nasdaq_extended():
    from radar.providers.nasdaq import parse_extended
    data = {"previousInfo": " Market Close: $2.03",
            "lastUpdateInfo": ["This page refreshes every 30 seconds.",
                               "Data last updated Sep 24, 2026 05:04 AM ET."],
            "infoTable": {"rows": [{"consolidated": "$4.4197 +2.3897 (+117.72%)", "volume": "8,223,515",
                                    "highPrice": "$5.05 (04:24:20 AM)", "lowPrice": "$3.75 (04:00:00 AM)"}]}}
    q = parse_extended(data, "pre")
    assert (q.last, q.change_pct, q.volume, q.high, q.low, q.market_close) == (
        4.4197, 117.72, 8223515, 5.05, 3.75, 2.03)
    assert q.updated == "Sep 24, 2026 05:04 AM ET"
    assert parse_extended({"infoTable": {"rows": []}}, "pre") is None
    assert parse_extended(None, "post") is None
