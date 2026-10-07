"""SQLite persistence: cycles, snapshots, alerts (with outcomes) and caches."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    phase TEXT NOT NULL,
    n_candidates INTEGER,
    duration_s REAL,
    errors TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES cycles(id),
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    phase TEXT,
    price REAL,
    change_pct REAL,
    premarket_change_pct REAL,
    market_cap REAL,
    float_shares REAL,
    volume REAL,
    avg_volume_20d REAL,
    rvol REAL,
    run_up_pct REAL,
    rs_status TEXT,
    rs_ratio INTEGER,
    rs_date TEXT,
    n_catalysts INTEGER,
    score_reverse_split REAL,
    score_float REAL,
    score_volume REAL,
    score_catalyst REAL,
    score_price REAL,
    bonus REAL,
    total INTEGER,
    level TEXT,
    active_signals TEXT,
    data_blocked INTEGER DEFAULT 0,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS ix_snap_cycle ON snapshots(cycle_id);
CREATE INDEX IF NOT EXISTS ix_snap_ticker_ts ON snapshots(ticker, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    kind TEXT NOT NULL,
    notified INTEGER NOT NULL,
    reason TEXT,
    level TEXT,
    total INTEGER,
    price REAL,
    change_pct REAL,
    rvol REAL,
    run_up_pct REAL,
    float_shares REAL,
    score_reverse_split REAL,
    score_float REAL,
    score_volume REAL,
    score_catalyst REAL,
    score_price REAL,
    bonus REAL,
    active_signals TEXT,
    message TEXT,
    payload TEXT,
    max_price_1h REAL, max_pct_1h REAL,
    max_price_4h REAL, max_pct_4h REAL,
    max_price_1d REAL, max_pct_1d REAL,
    max_price_5d REAL, max_pct_5d REAL,
    min_price_5d REAL, min_pct_5d REAL,
    outcomes_updated_at TEXT,
    outcomes_complete INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_alert_ticker_ts ON alerts(ticker, ts);

CREATE TABLE IF NOT EXISTS daily_stats (
    session_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    avg_volume_20d REAL,
    prev_close REAL,
    prev_high REAL,
    low_5d REAL,
    last_session TEXT,
    note TEXT,
    suspicious INTEGER DEFAULT 0,
    PRIMARY KEY (session_date, ticker)
);

CREATE TABLE IF NOT EXISTS reverse_split_filings (
    accession TEXT PRIMARY KEY,
    cik TEXT,
    tickers TEXT,
    company TEXT,
    form TEXT,
    filed_date TEXT,
    items TEXT,
    ratio INTEGER,
    ratio_text TEXT,
    effective_date TEXT,
    url TEXT
);
CREATE INDEX IF NOT EXISTS ix_rs_filed ON reverse_split_filings(filed_date);

CREATE TABLE IF NOT EXISTS sec_doc_cache (
    accession TEXT PRIMARY KEY,
    categories TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS broker_availability (
    ticker TEXT PRIMARY KEY,
    available INTEGER NOT NULL,
    evidence TEXT,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

MIGRATIONS = (
    ("daily_stats", "low_5d"),
    ("snapshots", "run_up_pct"),
    ("alerts", "run_up_pct"),
)

SCORE_COLS = ("reverse_split", "float", "volume", "catalyst", "price")
OUTCOME_COLS = (
    "max_price_1h", "max_pct_1h", "max_price_4h", "max_pct_4h",
    "max_price_1d", "max_pct_1d", "max_price_5d", "max_pct_5d",
    "min_price_5d", "min_pct_5d",
)


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the first release to existing databases."""
        for table, column in MIGRATIONS:
            cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} REAL")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------ kv
    def get_kv(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_kv(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, value))

    # ------------------------------------------------------------ cycles / snapshots
    def insert_cycle(self, ts: datetime, phase: str) -> int:
        with self.tx() as c:
            cur = c.execute("INSERT INTO cycles(ts, phase) VALUES(?, ?)", (ts.isoformat(), phase))
            return int(cur.lastrowid)

    def finish_cycle(self, cycle_id: int, n: int, duration: float, errors: list[str]) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE cycles SET n_candidates=?, duration_s=?, errors=? WHERE id=?",
                (n, round(duration, 1), json.dumps(errors[:50], ensure_ascii=False), cycle_id),
            )

    def insert_snapshots(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        sql = f"INSERT INTO snapshots({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})"
        with self.tx() as c:
            c.executemany(sql, [tuple(r[k] for k in cols) for r in rows])

    def latest_cycle_id(self) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(cycle_id) AS id FROM snapshots"
        ).fetchone()
        return row["id"] if row and row["id"] is not None else None

    def snapshots_for_cycle(self, cycle_id: int) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM snapshots WHERE cycle_id=? ORDER BY total DESC", (cycle_id,))

    def previous_level(self, ticker: str, before_ts: str, same_day: str) -> str | None:
        row = self._conn.execute(
            "SELECT level FROM snapshots WHERE ticker=? AND ts<? AND substr(ts,1,10)=? "
            "ORDER BY ts DESC LIMIT 1",
            (ticker, before_ts, same_day),
        ).fetchone()
        return row["level"] if row else None

    def tickers_seen_since(self, since_ts: str, min_total: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT ticker FROM snapshots WHERE ts>=? AND total>=?", (since_ts, min_total)
        ).fetchall()
        return [r["ticker"] for r in rows]

    # ------------------------------------------------------------ alerts
    def insert_alert(self, row: dict[str, Any]) -> int:
        cols = list(row.keys())
        sql = f"INSERT INTO alerts({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})"
        with self.tx() as c:
            return int(c.execute(sql, tuple(row[k] for k in cols)).lastrowid)

    def last_notified_alert(self, ticker: str) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT * FROM alerts WHERE ticker=? AND notified=1 ORDER BY ts DESC LIMIT 1", (ticker,)
        )
        return rows[0] if rows else None

    def has_watch_event(self, ticker: str, day: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM alerts WHERE ticker=? AND kind='VIGILAR' AND substr(ts,1,10)=?",
            (ticker, day),
        ).fetchone()
        return row is not None

    def alerts(self, limit: int = 200, notified_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE notified=1" if notified_only else ""
        return self.query(f"SELECT * FROM alerts {where} ORDER BY ts DESC LIMIT ?", (limit,))

    def pending_outcomes(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM alerts WHERE outcomes_complete=0 ORDER BY ts")

    def update_outcomes(self, alert_id: int, values: dict[str, Any], complete: bool, ts: str) -> None:
        sets = ", ".join(f"{k}=?" for k in values)
        params = (*values.values(), ts, int(complete), alert_id)
        with self.tx() as c:
            c.execute(
                f"UPDATE alerts SET {sets}{', ' if sets else ''}outcomes_updated_at=?, "
                "outcomes_complete=? WHERE id=?",
                params,
            )

    # ------------------------------------------------------------ daily stats
    def daily_stats(self, session_date: str) -> dict[str, dict[str, Any]]:
        rows = self.query("SELECT * FROM daily_stats WHERE session_date=?", (session_date,))
        return {r["ticker"]: r for r in rows}

    def upsert_daily_stats(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        sql = (
            f"INSERT OR REPLACE INTO daily_stats({', '.join(cols)}) "
            f"VALUES({', '.join('?' for _ in cols)})"
        )
        with self.tx() as c:
            c.executemany(sql, [tuple(r[k] for k in cols) for r in rows])

    # ------------------------------------------------------------ reverse split filings
    def known_rs_accessions(self) -> set[str]:
        return {r["accession"] for r in self.query("SELECT accession FROM reverse_split_filings")}

    def insert_rs_filing(self, row: dict[str, Any]) -> None:
        cols = list(row.keys())
        sql = (
            f"INSERT OR REPLACE INTO reverse_split_filings({', '.join(cols)}) "
            f"VALUES({', '.join('?' for _ in cols)})"
        )
        with self.tx() as c:
            c.execute(sql, tuple(row[k] for k in cols))

    def rs_filings_since(self, since: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM reverse_split_filings WHERE filed_date>=? ORDER BY filed_date DESC", (since,)
        )

    # ------------------------------------------------------------ broker availability
    def broker_availability(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        if not tickers:
            return {}
        marks = ", ".join("?" for _ in tickers)
        rows = self.query(f"SELECT * FROM broker_availability WHERE ticker IN ({marks})", tuple(tickers))
        return {r["ticker"]: r for r in rows}

    def set_broker_availability(self, ticker: str, available: bool, evidence: str, ts: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO broker_availability(ticker, available, evidence, checked_at) "
                "VALUES(?, ?, ?, ?)",
                (ticker, int(available), evidence, ts),
            )

    # ------------------------------------------------------------ sec doc cache
    def get_doc_categories(self, accession: str) -> list[list[str]] | None:
        row = self._conn.execute(
            "SELECT categories FROM sec_doc_cache WHERE accession=?", (accession,)
        ).fetchone()
        return json.loads(row["categories"]) if row else None

    def set_doc_categories(self, accession: str, categories: list, ts: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO sec_doc_cache(accession, categories, fetched_at) VALUES(?,?,?)",
                (accession, json.dumps(categories, ensure_ascii=False), ts),
            )
