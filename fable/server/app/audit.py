"""Audit log helpers. Every mutation records a row."""
from __future__ import annotations

import sqlite3
from typing import Optional

from .db import now_iso


def record(conn: sqlite3.Connection, ticket_id: Optional[int], action: str,
           detail: str = "", who: str = "console") -> None:
    conn.execute(
        "INSERT INTO audit_log (ticket_id, who, action, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (ticket_id, who, action, detail, now_iso()),
    )


def list_recent(conn: sqlite3.Connection, limit: int = 100) -> list:
    rows = conn.execute(
        "SELECT ticket_id, who, action, detail, created_at FROM audit_log "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "ticket_id": r["ticket_id"],
            "who": r["who"],
            "action": r["action"],
            "detail": r["detail"],
            "at": r["created_at"],
        }
        for r in rows
    ]


def for_ticket(conn: sqlite3.Connection, ticket_id: int) -> list:
    rows = conn.execute(
        "SELECT action, detail, created_at FROM audit_log WHERE ticket_id=? "
        "ORDER BY id ASC",
        (ticket_id,),
    ).fetchall()
    return [{"action": r["action"], "detail": r["detail"], "at": r["created_at"]} for r in rows]
