"""Local read-only dashboard (stdlib HTTP server, bound to localhost)."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from radar import analysis
from radar.db import Database

STATIC = Path(__file__).parent / "static"
TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")

SUMMARY_COLS = (
    "ticker", "name", "ts", "phase", "price", "change_pct", "premarket_change_pct",
    "market_cap", "float_shares", "volume", "avg_volume_20d", "rvol", "run_up_pct", "rs_status",
    "rs_ratio", "rs_date", "n_catalysts", "score_reverse_split", "score_float",
    "score_volume", "score_catalyst", "score_price", "bonus", "total", "level",
    "active_signals", "data_blocked",
)
ALERT_COLS = (
    "id", "ts", "ticker", "kind", "notified", "reason", "level", "total", "price",
    "change_pct", "rvol", "run_up_pct", "float_shares", "active_signals",
    "max_pct_1h", "max_pct_4h", "max_pct_1d", "max_pct_5d", "min_pct_5d",
    "outcomes_complete",
)


def latest(db: Database) -> dict[str, Any]:
    cycle_id = db.latest_cycle_id()
    if cycle_id is None:
        return {"cycle": None, "rows": []}
    cycle = db.query("SELECT * FROM cycles WHERE id=?", (cycle_id,))
    rows = [{k: r[k] for k in SUMMARY_COLS} for r in db.snapshots_for_cycle(cycle_id)]
    return {"cycle": cycle[0] if cycle else None, "rows": rows}


def ticker_detail(db: Database, ticker: str) -> dict[str, Any]:
    snaps = db.query(
        "SELECT payload FROM snapshots WHERE ticker=? ORDER BY ts DESC LIMIT 1", (ticker,)
    )
    history = db.query(
        "SELECT ts, price, total, level FROM snapshots WHERE ticker=? ORDER BY ts DESC LIMIT 100",
        (ticker,),
    )
    alerts = db.query(
        f"SELECT {', '.join(ALERT_COLS)} FROM alerts WHERE ticker=? ORDER BY ts DESC LIMIT 50", (ticker,)
    )
    return {
        "payload": json.loads(snaps[0]["payload"]) if snaps else None,
        "history": history,
        "alerts": alerts,
    }


def make_handler(db: Database):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            return

        def _json(self, body: Any, status: int = 200) -> None:
            data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _file(self, name: str, ctype: str) -> None:
            data = (STATIC / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            path = url.path
            try:
                if path in ("/", "/index.html"):
                    return self._file("index.html", "text/html; charset=utf-8")
                if path == "/app.js":
                    return self._file("app.js", "application/javascript; charset=utf-8")
                if path == "/api/latest":
                    return self._json(latest(db))
                if path == "/api/alerts":
                    qs = parse_qs(url.query)
                    limit = min(int(qs.get("limit", ["300"])[0]), 5000)
                    rows = db.query(
                        f"SELECT {', '.join(ALERT_COLS)} FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
                    )
                    return self._json(rows)
                if path == "/api/stats":
                    qs = parse_qs(url.query)
                    hit = float(qs.get("hit", [str(analysis.DEFAULT_HIT_PCT)])[0])
                    return self._json(analysis.report(db.alerts(limit=100000), hit_pct=hit))
                if path.startswith("/api/ticker/"):
                    ticker = path.rsplit("/", 1)[1].upper()
                    if not TICKER_RE.match(ticker):
                        return self._json({"error": "ticker invalido"}, 400)
                    return self._json(ticker_detail(db, ticker))
                self._json({"error": "no encontrado"}, 404)
            except (ValueError, KeyError) as exc:
                self._json({"error": str(exc)}, 400)

    return Handler


def serve(cfg: Mapping[str, Any]) -> None:
    db = Database(cfg["db_path"])
    host, port = cfg["dashboard"]["host"], int(cfg["dashboard"]["port"])
    server = ThreadingHTTPServer((host, port), make_handler(db))
    print(f"Panel en http://{host}:{port}  (Ctrl+C para detener)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
