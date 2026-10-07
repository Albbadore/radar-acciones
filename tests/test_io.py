"""Providers, notifications, dashboard and small utilities (network faked)."""
import json
import threading
import urllib.request
from datetime import date, datetime, timezone
from http.server import ThreadingHTTPServer

import pandas as pd
import pytest

from radar import notify, outcomes
from radar.catalysts import merge, news_catalysts
from radar.clock import ET, add_trading_days, market_phase, previous_trading_day
from radar.dashboard.server import make_handler
from radar.db import Database
from radar.envfile import load_env
from radar.providers import yahoo
from radar.providers.sec import SecClient
from radar.ttl_cache import TTLCache


class FakeResp:
    def __init__(self, payload=None, text="", status=200):
        self._payload, self.text, self.status_code = payload, text, status
        self.ok = status < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            import requests
            raise requests.HTTPError(response=self)


class FakeSession:
    def __init__(self, routes):
        self.routes, self.headers, self.calls = routes, {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for key, resp in self.routes.items():
            if key in url:
                return resp(params) if callable(resp) else resp
        return FakeResp(status=404)


class TestSec:
    def client(self, tmp_path, routes):
        return SecClient("test agent@x.com", str(tmp_path), min_interval_s=0, session=FakeSession(routes))

    def test_ticker_map_and_cache(self, tmp_path):
        sec = self.client(tmp_path, {"company_tickers": FakeResp({"0": {"cik_str": 5, "ticker": "BRK.B"}})})
        assert sec.cik_for("BRK-B") == 5
        assert (tmp_path / "company_tickers.json").exists()

    def test_recent_filings(self, tmp_path):
        recent = {"form": ["8-K", "10-Q"], "filingDate": ["2026-09-23", "2026-01-01"],
                  "accessionNumber": ["0001-26-9", "0001-26-1"], "items": ["1.01", ""],
                  "primaryDocument": ["a.htm", "b.htm"], "primaryDocDescription": ["8-K", "10-Q"]}
        sec = self.client(tmp_path, {"submissions": FakeResp({"filings": {"recent": recent}})})
        out = sec.recent_filings(42, date(2026, 9, 17))
        assert len(out) == 1 and out[0].url.endswith("/42/0001269/a.htm")

    def test_shares_outstanding_latest(self, tmp_path):
        facts = {"units": {"shares": [
            {"end": "2026-05-20", "val": 1, "form": "10-Q", "filed": "2026-05-20"},
            {"end": "2026-08-17", "val": 2, "form": "10-Q", "filed": "2026-08-19"}]}}
        sec = self.client(tmp_path, {"companyconcept": FakeResp(facts)})
        s = sec.shares_outstanding(1)
        assert s.value == 2 and s.as_of == "2026-08-17"

    def test_shares_outstanding_missing(self, tmp_path):
        sec = self.client(tmp_path, {})
        assert sec.shares_outstanding(1) is None

    def test_full_text_search_chunks_and_groups(self, tmp_path):
        def search(params):
            if params["startdt"] == "2026-09-01":
                return FakeResp({"hits": {"total": {"value": 2}, "hits": [
                    {"_id": "0001-26-5:ex99.htm", "_source": {
                        "adsh": "0001-26-5", "ciks": ["0000000009"], "file_date": "2026-09-02",
                        "display_names": ["Acme Inc (ACME) (CIK 0000000009)"], "root_forms": ["8-K"],
                        "file_type": "EX-99.1", "items": ["8.01"]}},
                    {"_id": "0001-26-5:main.htm", "_source": {
                        "adsh": "0001-26-5", "ciks": ["0000000009"], "file_date": "2026-09-02",
                        "display_names": ["Acme Inc (ACME) (CIK 0000000009)"], "root_forms": ["8-K"],
                        "file_type": "8-K", "items": ["8.01"]}},
                ]}})
            return FakeResp(status=500)

        sec = self.client(tmp_path, {"efts": search})
        hits, failed = sec.full_text_search("reverse split", ["8-K"], date(2026, 9, 1), date(2026, 9, 30),
                                            chunk_days=20)
        assert failed == 1 and len(hits) == 1
        assert hits[0].tickers == ("ACME",) and hits[0].urls[0].endswith("main.htm")

    def test_document_text(self, tmp_path):
        sec = self.client(tmp_path, {"Archives": FakeResp(text="<p>1-for-10 reverse split</p>")})
        assert sec.document_text("https://www.sec.gov/Archives/x") == "1-for-10 reverse split"


class TestYahooParsing:
    def test_last_split(self):
        assert yahoo.last_split({"lastSplitFactor": "1:20", "lastSplitDate": 1790035200}) == (
            date(2026, 9, 22), 0.05)
        assert yahoo.last_split({}) is None
        assert yahoo.last_split({"lastSplitFactor": "bad", "lastSplitDate": 1}) is None

    def test_split_frames(self):
        cols = pd.MultiIndex.from_product([["A", "B"], ["Close", "Volume"]])
        raw = pd.DataFrame([[1, 2, None, None]], columns=cols)
        out = yahoo._split_frames(raw, ["A", "B"])
        assert list(out) == ["A"]
        single = pd.DataFrame({"Close": [1.0]})
        assert list(yahoo._split_frames(single, ["X"])) == ["X"]

    def test_news_parsing(self, monkeypatch):
        class T:
            def __init__(self, t):
                self.news = [{"content": {"title": "FDA approves", "pubDate": "2026-09-24T12:00:00Z",
                                          "provider": {"displayName": "PR Newswire"},
                                          "canonicalUrl": {"url": "https://n"}}},
                             {"content": {"title": "no date"}}]
        monkeypatch.setattr(yahoo.yf, "Ticker", T)
        items = yahoo.news("X")
        assert len(items) == 1 and items[0]["provider"] == "PR Newswire"


class TestNewsCatalysts:
    def test_classification_and_official(self):
        now = datetime(2026, 9, 24, 12, 0, tzinfo=ET)
        items = [
            {"title": "Acme receives FDA approval", "provider": "GlobeNewswire",
             "published": datetime(2026, 9, 24, 13, 0, tzinfo=timezone.utc)},
            {"title": "Is Acme a buy?", "provider": "Simply Wall St.",
             "published": datetime(2026, 9, 23, tzinfo=timezone.utc)},
            {"title": "Acme announces update", "provider": "Business Wire",
             "published": datetime(2026, 9, 22, tzinfo=timezone.utc)},
            {"title": "old FDA news", "provider": "X", "published": datetime(2026, 9, 1, tzinfo=timezone.utc)},
        ]
        cats = news_catalysts(items, now)
        assert [c.category for c in cats] == ["FDA: aprobacion/autorizacion", "Comunicado oficial de la empresa"]
        assert all(c.official for c in cats)
        assert len(merge(cats, cats)) == 2


class TestNotify:
    def test_deliver_log_console_telegram(self, tmp_path, monkeypatch, capsys, cfg):
        posted = []
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t0k")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        monkeypatch.setattr(notify.requests, "post",
                            lambda url, json, timeout: posted.append(json) or FakeResp({"ok": True}))
        local = dict(cfg, log_file=str(tmp_path / "a.log"))
        channels = notify.deliver("hola", local)
        assert channels == ["log", "consola", "telegram"]
        assert posted[0]["chat_id"] == "123" and "hola" in capsys.readouterr().out
        assert "hola" in (tmp_path / "a.log").read_text(encoding="utf-8")

    def test_telegram_failure_never_leaks_token(self, monkeypatch, caplog):
        import requests

        def boom(*a, **k):
            raise requests.ConnectionError("https://api.telegram.org/botSECRET/sendMessage")
        monkeypatch.setattr(notify.requests, "post", boom)
        assert not notify.send_telegram("x", "SECRET", "1")
        assert "SECRET" not in caplog.text

    def test_chat_ids(self, monkeypatch):
        payload = {"result": [{"message": {"chat": {"id": 9, "type": "private", "username": "yo"}}}]}
        monkeypatch.setattr(notify.requests, "get", lambda url, timeout: FakeResp(payload))
        assert notify.telegram_chat_ids("t") == [{"chat_id": 9, "tipo": "private", "nombre": "yo"}]


@pytest.fixture
def server(tmp_path):
    db = Database(str(tmp_path / "d.db"))
    cid = db.insert_cycle(datetime(2026, 9, 24, 10, 0), "regular")
    payload = json.dumps({"data": {"ticker": "ABC"}, "score": {}})
    row = {"cycle_id": cid, "ts": "2026-09-24T10:00:00", "ticker": "ABC", "name": "<b>x</b>",
           "total": 70, "level": "ALERTA ALTA", "payload": payload}
    db.insert_snapshots([row])
    db.insert_alert({"ts": "2026-09-24T10:00:00", "ticker": "ABC", "kind": "INICIAL", "notified": 1,
                     "level": "ALERTA ALTA", "total": 70, "active_signals": "float,volume", "max_pct_1d": 30})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read().decode("utf-8"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), e.headers


class TestDashboard:
    def test_pages_and_api(self, server):
        status, body, headers = get(server + "/")
        assert status == 200 and "Radar" in body and "default-src 'self'" in headers["Content-Security-Policy"]
        assert get(server + "/app.js")[0] == 200
        latest = json.loads(get(server + "/api/latest")[1])
        assert latest["rows"][0]["ticker"] == "ABC"
        assert json.loads(get(server + "/api/alerts")[1])[0]["kind"] == "INICIAL"
        assert json.loads(get(server + "/api/stats?hit=10")[1])["eventos"] == 1
        detail = json.loads(get(server + "/api/ticker/abc")[1])
        assert detail["payload"]["data"]["ticker"] == "ABC"

    def test_rejects_bad_input(self, server):
        assert get(server + "/api/ticker/%3Cscript%3E")[0] == 400
        assert get(server + "/api/stats?hit=abc")[0] == 400
        assert get(server + "/nope")[0] == 404


class TestUtilities:
    def test_ttl_cache(self):
        cache, calls = TTLCache(), []
        f = lambda: calls.append(1) or len(calls)  # noqa: E731
        assert cache.get_or_set("k", 60, f) == 1 and cache.get_or_set("k", 60, f) == 1
        assert cache.get_or_set("k", 0, f) == 2

    def test_envfile(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RADAR_T1", raising=False)
        monkeypatch.setenv("RADAR_T2", "keep")
        p = tmp_path / ".env"
        p.write_text("# c\nRADAR_T1='v1'\nRADAR_T2=override\nbad line\n", encoding="utf-8")
        load_env(p)
        import os
        assert os.environ["RADAR_T1"] == "v1" and os.environ["RADAR_T2"] == "keep"

    def test_market_phases(self, cfg):
        s = cfg["schedule"]
        assert market_phase(datetime(2026, 9, 24, 8, 0, tzinfo=ET), s) == "premarket"
        assert market_phase(datetime(2026, 9, 24, 10, 0, tzinfo=ET), s) == "regular"
        assert market_phase(datetime(2026, 9, 24, 17, 0, tzinfo=ET), s) == "postmarket"
        assert market_phase(datetime(2026, 9, 24, 21, 0, tzinfo=ET), s) == "cerrado"
        assert market_phase(datetime(2026, 9, 26, 10, 0, tzinfo=ET), s) == "cerrado"
        assert market_phase(datetime(2026, 11, 26, 10, 0, tzinfo=ET), s) == "cerrado"

    def test_trading_days(self, cfg):
        hol = cfg["schedule"]["holidays"]
        assert previous_trading_day(date(2026, 9, 28), hol) == date(2026, 9, 25)
        assert add_trading_days(date(2026, 11, 25), 1, hol) == date(2026, 11, 27)


class TestOutcomeUpdater:
    def test_update_all(self, monkeypatch, cfg):
        db = Database(":memory:")
        aid = db.insert_alert({"ts": "2026-09-24T10:00:00-04:00", "ticker": "A", "kind": "INICIAL",
                               "notified": 1, "price": 2.0})
        db.insert_alert({"ts": "2026-06-01T10:00:00-04:00", "ticker": "OLD", "kind": "INICIAL",
                         "notified": 1, "price": 1.0})
        idx = pd.date_range("2026-09-24 09:30", periods=24 * 12 * 8, freq="5min", tz="America/New_York")
        bars = pd.DataFrame({"Open": 2.0, "High": 2.0, "Low": 1.8, "Close": 2.0, "Volume": 1}, index=idx)
        bars.loc[bars.index[40], "High"] = 3.0
        monkeypatch.setattr(yahoo, "bars_since", lambda t, start: bars)
        n = outcomes.update_all(db, datetime(2026, 10, 5, 12, 0, tzinfo=ET), cfg)
        row = db.query("SELECT * FROM alerts WHERE id=?", (aid,))[0]
        assert n == 1 and row["outcomes_complete"] == 1
        assert row["max_pct_1d"] == pytest.approx(50.0) and row["min_pct_5d"] == pytest.approx(-10.0)
        assert db.pending_outcomes() == []
