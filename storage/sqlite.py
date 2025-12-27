from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable


class SqliteStore:
    """
    Simple SQLite persistence layer.
    - Thread-safe via a single connection + lock (fine for this asyncio app).
    - Stores: markets, orders, fills, tape events, position snapshots, pnl snapshots.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")

    def init_db(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS markets (
                  market_id TEXT PRIMARY KEY,
                  question TEXT,
                  event_id TEXT,
                  active INTEGER,
                  end_ts REAL,
                  volume_24h_usd REAL,
                  liquidity_usd REAL,
                  updated_ts REAL
                );

                CREATE TABLE IF NOT EXISTS orders (
                  order_id TEXT PRIMARY KEY,
                  market_id TEXT,
                  side TEXT,
                  price REAL,
                  size REAL,
                  created_ts REAL,
                  status TEXT,
                  filled_size REAL,
                  meta_json TEXT
                );

                CREATE TABLE IF NOT EXISTS fills (
                  fill_id TEXT PRIMARY KEY,
                  order_id TEXT,
                  market_id TEXT,
                  side TEXT,
                  price REAL,
                  size REAL,
                  ts REAL,
                  meta_json TEXT
                );

                CREATE TABLE IF NOT EXISTS tape (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  market_id TEXT,
                  kind TEXT,
                  payload_json TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_tape_ts ON tape(ts);
                CREATE INDEX IF NOT EXISTS idx_tape_market ON tape(market_id, ts);

                CREATE TABLE IF NOT EXISTS position_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  market_id TEXT,
                  event_id TEXT,
                  position REAL,
                  avg_price REAL,
                  mark_price REAL,
                  unrealized_pnl REAL,
                  realized_pnl REAL
                );

                CREATE TABLE IF NOT EXISTS pnl_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  total_unrealized REAL,
                  total_realized REAL,
                  total_pnl REAL
                );
                """
            )
            self._conn.commit()

    def upsert_markets(self, markets: Iterable[dict[str, Any]]) -> None:
        now = time.time()
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO markets(market_id, question, event_id, active, end_ts, volume_24h_usd, liquidity_usd, updated_ts)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(market_id) DO UPDATE SET
                  question=excluded.question,
                  event_id=excluded.event_id,
                  active=excluded.active,
                  end_ts=excluded.end_ts,
                  volume_24h_usd=excluded.volume_24h_usd,
                  liquidity_usd=excluded.liquidity_usd,
                  updated_ts=excluded.updated_ts
                """,
                [
                    (
                        m["market_id"],
                        m.get("question"),
                        m.get("event_id"),
                        1 if m.get("active", True) else 0,
                        m.get("end_ts"),
                        float(m.get("volume_24h_usd", 0.0)),
                        float(m.get("liquidity_usd", 0.0)),
                        now,
                    )
                    for m in markets
                ],
            )
            self._conn.commit()

    def insert_order(self, order: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO orders(order_id, market_id, side, price, size, created_ts, status, filled_size, meta_json)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    order["order_id"],
                    order["market_id"],
                    order["side"],
                    order["price"],
                    order["size"],
                    order["created_ts"],
                    order["status"],
                    order.get("filled_size", 0.0),
                    json.dumps(order.get("meta", {}), separators=(",", ":")),
                ),
            )
            self._conn.commit()

    def update_order_status(self, order_id: str, status: str, filled_size: float | None = None) -> None:
        with self._lock:
            if filled_size is None:
                self._conn.execute("UPDATE orders SET status=? WHERE order_id=?", (status, order_id))
            else:
                self._conn.execute(
                    "UPDATE orders SET status=?, filled_size=? WHERE order_id=?",
                    (status, float(filled_size), order_id),
                )
            self._conn.commit()

    def insert_fill(self, fill: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO fills(fill_id, order_id, market_id, side, price, size, ts, meta_json)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    fill["fill_id"],
                    fill["order_id"],
                    fill["market_id"],
                    fill["side"],
                    fill["price"],
                    fill["size"],
                    fill["ts"],
                    json.dumps(fill.get("meta", {}), separators=(",", ":")),
                ),
            )
            self._conn.commit()

    def insert_tape(self, ts: float, market_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tape(ts, market_id, kind, payload_json) VALUES(?,?,?,?)",
                (float(ts), market_id, kind, json.dumps(payload, separators=(",", ":"))),
            )
            self._conn.commit()

    def insert_position_snapshot(self, snap: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO position_snapshots(ts, market_id, event_id, position, avg_price, mark_price, unrealized_pnl, realized_pnl)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    snap["ts"],
                    snap["market_id"],
                    snap["event_id"],
                    snap["position"],
                    snap["avg_price"],
                    snap["mark_price"],
                    snap["unrealized_pnl"],
                    snap["realized_pnl"],
                ),
            )
            self._conn.commit()

    def insert_pnl_snapshot(self, snap: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO pnl_snapshots(ts, total_unrealized, total_realized, total_pnl) VALUES(?,?,?,?)",
                (snap["ts"], snap["total_unrealized"], snap["total_realized"], snap["total_pnl"]),
            )
            self._conn.commit()

    def fetch_latest_positions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT market_id, event_id, position, avg_price, mark_price, unrealized_pnl, realized_pnl, MAX(ts) as ts
                FROM position_snapshots
                GROUP BY market_id
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def fetch_latest_pnl(self) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts, total_unrealized, total_realized, total_pnl FROM pnl_snapshots ORDER BY ts DESC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"ts": row[0], "total_unrealized": row[1], "total_realized": row[2], "total_pnl": row[3]}

    def iter_tape(self, start_ts: float | None, end_ts: float | None):
        q = "SELECT ts, market_id, kind, payload_json FROM tape WHERE 1=1"
        params: list[Any] = []
        if start_ts is not None:
            q += " AND ts >= ?"
            params.append(float(start_ts))
        if end_ts is not None:
            q += " AND ts <= ?"
            params.append(float(end_ts))
        q += " ORDER BY ts ASC, id ASC"
        with self._lock:
            cur = self._conn.execute(q, tuple(params))
            for ts, market_id, kind, payload_json in cur:
                yield float(ts), str(market_id), str(kind), json.loads(payload_json)

