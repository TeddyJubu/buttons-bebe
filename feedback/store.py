"""store.py — tiny SQLite state: a poll cursor + a processed-ticket ledger.

Why not a rolling "recent" window? Because (M1) a fixed window silently drops
tickets that fall between polls. Instead we keep a high-water-mark cursor
(the max updated_datetime we've handled) and re-query "updated since cursor minus
a small overlap", then record every ticket we acted on so overlap never
double-writes.

Stdlib only. Safe to call repeatedly (idempotent schema).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cursor (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    updated_high TEXT
);
CREATE TABLE IF NOT EXISTS processed (
    ticket_id   INTEGER PRIMARY KEY,
    outcome     TEXT NOT NULL,          -- captured | skipped
    reason      TEXT,                   -- skip reason or 'ok'
    similarity  REAL,
    handled_at  TEXT NOT NULL
);
"""


@contextmanager
def _conn():
    conn = sqlite3.connect(str(config.STATE_DB))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_cursor() -> str:
    with _conn() as c:
        row = c.execute("SELECT updated_high FROM cursor WHERE id = 1").fetchone()
        return row["updated_high"] if row and row["updated_high"] else ""


def set_cursor(updated_high: str) -> None:
    if not updated_high:
        return
    with _conn() as c:
        c.execute(
            "INSERT INTO cursor (id, updated_high) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET updated_high = excluded.updated_high",
            (updated_high,),
        )


def already_processed(ticket_id: int) -> bool:
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM processed WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        return row is not None


def mark_processed(ticket_id: int, outcome: str, reason: str, similarity=None) -> None:
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO processed (ticket_id, outcome, reason, similarity, handled_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(ticket_id) DO UPDATE SET "
            "outcome=excluded.outcome, reason=excluded.reason, "
            "similarity=excluded.similarity, handled_at=excluded.handled_at",
            (ticket_id, outcome, reason, similarity, now),
        )


def stats() -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT outcome, reason, COUNT(*) n FROM processed GROUP BY outcome, reason"
        ).fetchall()
        return {"cursor": get_cursor(), "breakdown": [dict(r) for r in rows]}
