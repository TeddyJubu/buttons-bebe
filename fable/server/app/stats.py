"""Dashboard stats (API contract §1)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _parse(s):
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def compute(conn: sqlite3.Connection) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()

    tickets_today = conn.execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE substr(created_at,1,10)=?", (today,)
    ).fetchone()["n"]

    open_count = conn.execute(
        "SELECT COUNT(*) AS n FROM tickets WHERE status='open'"
    ).fetchone()["n"]

    # Average first response time: minutes between ticket creation and the first
    # public agent message on that ticket.
    rows = conn.execute(
        "SELECT t.created_at AS created, MIN(m.created_at) AS first_reply "
        "FROM tickets t JOIN messages m ON m.ticket_id=t.id "
        "WHERE m.from_agent=1 AND m.public=1 GROUP BY t.id"
    ).fetchall()
    deltas = []
    for r in rows:
        c = _parse(r["created"])
        f = _parse(r["first_reply"])
        if c and f and f >= c:
            deltas.append((f - c).total_seconds() / 60.0)
    avg_first_response = round(sum(deltas) / len(deltas), 1) if deltas else 0.0

    # Draft acceptance: sent drafts / all decided drafts (sent+noted+superseded).
    decided = conn.execute(
        "SELECT status, COUNT(*) AS n FROM drafts "
        "WHERE status IN ('sent','noted','superseded') GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["n"] for r in decided}
    total_decided = sum(counts.values())
    accepted = counts.get("sent", 0)
    drafts_accepted_pct = round(100.0 * accepted / total_decided, 1) if total_decided else 0.0

    by_channel_rows = conn.execute(
        "SELECT channel, COUNT(*) AS n FROM tickets GROUP BY channel"
    ).fetchall()
    by_channel = {r["channel"]: r["n"] for r in by_channel_rows}

    return {
        "tickets_today": tickets_today,
        "open": open_count,
        "avg_first_response_minutes": avg_first_response,
        "drafts_accepted_pct": drafts_accepted_pct,
        "by_channel": by_channel,
    }
