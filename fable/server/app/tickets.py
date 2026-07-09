"""Ticket read/list/patch + serializers (API contract §1 core objects)."""
from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from . import audit
from .db import now_iso


# --- serializers -----------------------------------------------------------
def customer_brief(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


def customer_full(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "firstname": row["firstname"],
        "lastname": row["lastname"],
        "email": row["email"],
        "phone": row["phone"],
        "created_at": row["created_at"],
    }


def message_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "ticket_id": row["ticket_id"],
        "from_agent": bool(row["from_agent"]),
        "public": bool(row["public"]),
        "channel": row["channel"],
        "body_text": row["body_text"],
        "created_at": row["created_at"],
        "sender_name": row["sender_name"],
        "via": row["via"],
    }


def draft_to_dict(row: sqlite3.Row) -> dict:
    try:
        kb_refs = json.loads(row["kb_refs"] or "[]")
    except (ValueError, TypeError):
        kb_refs = []
    return {
        "id": row["id"],
        "ticket_id": row["ticket_id"],
        "body_text": row["body_text"],
        "risk": row["risk"],
        "risk_reason": row["risk_reason"],
        "brain": row["brain"],
        "kb_refs": kb_refs,
        "created_at": row["created_at"],
        "status": row["status"],
    }


def _ticket_tags(conn: sqlite3.Connection, ticket_id: int) -> List[str]:
    rows = conn.execute(
        "SELECT t.name FROM tags t JOIN ticket_tags tt ON tt.tag_id=t.id "
        "WHERE tt.ticket_id=? ORDER BY t.name",
        (ticket_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def _latest_message(conn: sqlite3.Connection, ticket_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM messages WHERE ticket_id=? ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()


def _has_proposed_draft(conn: sqlite3.Connection, ticket_id: int) -> bool:
    r = conn.execute(
        "SELECT 1 FROM drafts WHERE ticket_id=? AND status='proposed' LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return r is not None


def _current_draft(conn: sqlite3.Connection, ticket_id: int) -> Optional[sqlite3.Row]:
    """Latest proposed draft if any, else the most recent draft of any status."""
    r = conn.execute(
        "SELECT * FROM drafts WHERE ticket_id=? AND status='proposed' "
        "ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if r:
        return r
    return conn.execute(
        "SELECT * FROM drafts WHERE ticket_id=? ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()


def summary(conn: sqlite3.Connection, trow: sqlite3.Row) -> dict:
    cust = conn.execute("SELECT * FROM customers WHERE id=?", (trow["customer_id"],)).fetchone()
    last = _latest_message(conn, trow["id"])
    preview = ""
    if last:
        preview = (last["body_text"] or "").strip().replace("\n", " ")
        if len(preview) > 140:
            preview = preview[:137] + "..."
    return {
        "id": trow["id"],
        "subject": trow["subject"],
        "status": trow["status"],
        "channel": trow["channel"],
        "sensitive": bool(trow["sensitive"]),
        "sensitive_reason": trow["sensitive_reason"],
        "customer": customer_brief(cust) if cust else None,
        "preview": preview,
        "has_draft": _has_proposed_draft(conn, trow["id"]),
        "is_unread": bool(trow["is_unread"]),
        "tags": _ticket_tags(conn, trow["id"]),
        "last_message_at": trow["last_message_at"],
        "created_at": trow["created_at"],
    }


def full(conn: sqlite3.Connection, trow: sqlite3.Row) -> dict:
    data = summary(conn, trow)
    msg_rows = conn.execute(
        "SELECT * FROM messages WHERE ticket_id=? ORDER BY id ASC", (trow["id"],)
    ).fetchall()
    data["messages"] = [message_to_dict(m) for m in msg_rows]
    draft_row = _current_draft(conn, trow["id"])
    data["draft"] = draft_to_dict(draft_row) if draft_row else None
    oc = trow["order_context"]
    if oc:
        try:
            data["order_context"] = json.loads(oc)
        except (ValueError, TypeError):
            data["order_context"] = None
    else:
        data["order_context"] = None
    data["audit"] = audit.for_ticket(conn, trow["id"])
    return data


# --- queries ---------------------------------------------------------------
def get_row(conn: sqlite3.Connection, ticket_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()


def get_full(conn: sqlite3.Connection, ticket_id: int) -> Optional[dict]:
    row = get_row(conn, ticket_id)
    return full(conn, row) if row else None


def counts(conn: sqlite3.Connection) -> dict:
    def c(where: str, args=()) -> int:
        return conn.execute(f"SELECT COUNT(*) AS n FROM tickets WHERE {where}", args).fetchone()["n"]

    return {
        "open": c("status='open'"),
        "closed": c("status='closed'"),
        "snoozed": c("status='snoozed'"),
        "sensitive_open": c("status='open' AND sensitive=1"),
    }


def list_tickets(conn: sqlite3.Connection, status="all", channel="all",
                 sensitive=None, q=None, limit=50, cursor=None) -> dict:
    where = ["1=1"]
    args: list = []

    if status and status != "all":
        where.append("t.status=?")
        args.append(status)
    if channel and channel != "all":
        where.append("t.channel=?")
        args.append(channel)
    if sensitive is True:
        where.append("t.sensitive=1")
    if cursor:
        where.append("t.id < ?")
        args.append(int(cursor))
    if q:
        like = f"%{q}%"
        where.append(
            "(t.subject LIKE ? OR c.name LIKE ? OR c.email LIKE ? "
            "OR EXISTS (SELECT 1 FROM messages m WHERE m.ticket_id=t.id AND m.body_text LIKE ?))"
        )
        args.extend([like, like, like, like])

    limit = max(1, min(int(limit or 50), 200))
    sql = (
        "SELECT t.* FROM tickets t JOIN customers c ON c.id=t.customer_id "
        f"WHERE {' AND '.join(where)} ORDER BY t.id DESC LIMIT ?"
    )
    rows = conn.execute(sql, (*args, limit + 1)).fetchall()

    next_cursor = None
    if len(rows) > limit:
        next_cursor = rows[limit - 1]["id"]
        rows = rows[:limit]

    return {
        "tickets": [summary(conn, r) for r in rows],
        "next_cursor": next_cursor,
        "counts": counts(conn),
    }


# --- mutations -------------------------------------------------------------
def _set_tags(conn: sqlite3.Connection, ticket_id: int, tags: List[str]) -> None:
    conn.execute("DELETE FROM ticket_tags WHERE ticket_id=?", (ticket_id,))
    for name in tags:
        name = (name or "").strip()
        if not name:
            continue
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO ticket_tags (ticket_id, tag_id) VALUES (?, ?)",
            (ticket_id, tag_id),
        )


def patch(conn: sqlite3.Connection, ticket_id: int, body) -> Optional[dict]:
    row = get_row(conn, ticket_id)
    if not row:
        return None
    sets = []
    args: list = []
    changed = []

    if body.status is not None:
        sets.append("status=?")
        args.append(body.status)
        changed.append(f"status={body.status}")
    if body.assignee is not None:
        sets.append("assignee=?")
        args.append(body.assignee)
        changed.append(f"assignee={body.assignee}")
    if body.snooze_until is not None:
        sets.append("snooze_until=?")
        args.append(body.snooze_until)
        changed.append("snooze_until")

    if sets:
        sets.append("updated_at=?")
        args.append(now_iso())
        conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id=?", (*args, ticket_id))

    if body.tags is not None:
        _set_tags(conn, ticket_id, body.tags)
        changed.append("tags=" + ",".join(body.tags))

    audit.record(conn, ticket_id, "patch", "; ".join(changed) or "no-op")
    conn.commit()
    return get_full(conn, ticket_id)
