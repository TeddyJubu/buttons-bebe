"""Gorgias-compatible read layer + internal-note write (API contract §3).

Maps Fable objects to Gorgias field names (created_datetime, from_agent, public,
body_text, ...) inside the standard Gorgias list envelope. Basic auth is
accepted-but-ignored so existing tools work by changing only their base URL.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from . import audit
from .db import now_iso


def envelope(data: list, *, next_cursor=None, prev_cursor=None, total=None) -> dict:
    return {
        "data": data,
        "object": "list",
        "meta": {
            "next_cursor": next_cursor,
            "prev_cursor": prev_cursor,
            "total_resources": total if total is not None else len(data),
        },
    }


def _customer_obj(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "firstname": row["firstname"],
        "lastname": row["lastname"],
        "external_id": None,
    }


def _tags(conn, ticket_id):
    rows = conn.execute(
        "SELECT t.id, t.name FROM tags t JOIN ticket_tags tt ON tt.tag_id=t.id "
        "WHERE tt.ticket_id=?",
        (ticket_id,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "decoration": None} for r in rows]


def message_obj(conn, m: sqlite3.Row) -> dict:
    ticket = conn.execute("SELECT customer_id, subject FROM tickets WHERE id=?",
                          (m["ticket_id"],)).fetchone()
    cust = None
    if ticket:
        cust = conn.execute("SELECT * FROM customers WHERE id=?",
                            (ticket["customer_id"],)).fetchone()
    public = bool(m["public"])
    from_agent = bool(m["from_agent"])
    sender = None
    receiver = None
    if from_agent:
        sender = {"name": m["sender_name"] or "Buttons Bebe Care Team"}
        if public and cust:
            receiver = {"id": cust["id"], "email": cust["email"], "name": cust["name"]}
    else:
        if cust:
            sender = {"id": cust["id"], "email": cust["email"], "name": cust["name"]}
    return {
        "id": m["id"],
        "ticket_id": m["ticket_id"],
        "public": public,
        "channel": m["channel"],
        "via": m["via"],
        "from_agent": from_agent,
        "subject": ticket["subject"] if ticket else None,
        "body_text": m["body_text"],
        "body_html": f"<p>{(m['body_text'] or '').replace(chr(10), '<br>')}</p>",
        "stripped_text": m["body_text"],
        "sender": sender,
        "receiver": receiver,
        "created_datetime": m["created_at"],
        "sent_datetime": m["created_at"] if from_agent else None,
    }


def ticket_obj(conn, t: sqlite3.Row, include_messages: bool = False) -> dict:
    cust = conn.execute("SELECT * FROM customers WHERE id=?", (t["customer_id"],)).fetchone()
    obj = {
        "id": t["id"],
        "status": t["status"] if t["status"] != "snoozed" else "open",
        "channel": t["channel"],
        "via": "api",
        "priority": "high" if t["sensitive"] else "normal",
        "from_agent": False,
        "subject": t["subject"],
        "language": "en",
        "is_unread": bool(t["is_unread"]),
        "spam": False,
        "external_id": None,
        "customer": _customer_obj(cust),
        "assignee_user": {"name": t["assignee"]} if t["assignee"] else None,
        "assignee_team": None,
        "tags": _tags(conn, t["id"]),
        "created_datetime": t["created_at"],
        "opened_datetime": t["created_at"],
        "updated_datetime": t["updated_at"],
        "last_message_datetime": t["last_message_at"],
        "last_received_message_datetime": t["last_message_at"],
        "closed_datetime": t["updated_at"] if t["status"] == "closed" else None,
        "snooze_datetime": t["snooze_until"],
    }
    if include_messages:
        rows = conn.execute(
            "SELECT * FROM messages WHERE ticket_id=? ORDER BY id ASC", (t["id"],)
        ).fetchall()
        obj["messages"] = [message_obj(conn, m) for m in rows]
    return obj


# --- read endpoints --------------------------------------------------------
def list_tickets(conn, limit=30, cursor=None) -> dict:
    limit = max(1, min(int(limit or 30), 100))
    args = []
    where = "1=1"
    if cursor:
        where += " AND id < ?"
        args.append(int(cursor))
    rows = conn.execute(
        f"SELECT * FROM tickets WHERE {where} ORDER BY id DESC LIMIT ?",
        (*args, limit + 1),
    ).fetchall()
    next_cursor = None
    if len(rows) > limit:
        next_cursor = rows[limit - 1]["id"]
        rows = rows[:limit]
    total = conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"]
    return envelope([ticket_obj(conn, r) for r in rows], next_cursor=next_cursor, total=total)


def get_ticket(conn, ticket_id) -> Optional[dict]:
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    return ticket_obj(conn, row, include_messages=True) if row else None


def get_ticket_messages(conn, ticket_id, limit=30) -> dict:
    limit = max(1, min(int(limit or 30), 100))
    rows = conn.execute(
        "SELECT * FROM messages WHERE ticket_id=? ORDER BY id ASC LIMIT ?",
        (ticket_id, limit),
    ).fetchall()
    return envelope([message_obj(conn, m) for m in rows])


def get_customer(conn, customer_id) -> Optional[dict]:
    row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    return _customer_obj(row)


def search_customers(conn, email) -> dict:
    rows = conn.execute(
        "SELECT * FROM customers WHERE lower(email)=lower(?) ORDER BY id ASC", (email,)
    ).fetchall()
    return envelope([_customer_obj(r) for r in rows])


# --- write: internal note (the VPS writer path) ----------------------------
def post_message(conn, ticket_id, body) -> Optional[dict]:
    ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not ticket:
        return None
    channel = (body.channel or "internal").lower()
    is_internal = channel in ("internal", "internal-note", "internal_note")
    public = False if is_internal else bool(body.public)
    from_agent = True if body.from_agent is None else bool(body.from_agent)
    stored_channel = "internal-note" if is_internal else ticket["channel"]
    sender_name = (body.sender or {}).get("name") if body.sender else "Buttons Bebe Care Team"

    cur = conn.execute(
        "INSERT INTO messages (ticket_id, from_agent, public, channel, body_text, "
        "sender_name, via, created_at) VALUES (?, ?, ?, ?, ?, ?, 'api', ?)",
        (ticket_id, 1 if from_agent else 0, 1 if public else 0, stored_channel,
         body.body_text, sender_name, now_iso()),
    )
    ts = now_iso()
    conn.execute("UPDATE tickets SET last_message_at=?, updated_at=? WHERE id=?",
                 (ts, ts, ticket_id))
    audit.record(conn, ticket_id, "gorgias-compat:message",
                 f"channel={stored_channel} public={public}", who="api")
    conn.commit()
    m = conn.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
    return message_obj(conn, m)
