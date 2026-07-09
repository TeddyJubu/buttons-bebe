"""Channel intake — email / chat / whatsapp become tickets in one inbox.

find-or-create customer → find-or-create open ticket (same customer+channel
within 7 days) → store the customer message → enqueue an AI pipeline job.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import audit
from .db import now_iso

_REUSE_WINDOW = timedelta(days=7)


def _split_name(name):
    name = (name or "").strip()
    if not name:
        return None, None
    parts = name.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


# --- customer --------------------------------------------------------------
def _find_customer_by_email(conn, email):
    if not email:
        return None
    return conn.execute(
        "SELECT * FROM customers WHERE lower(email)=lower(?) ORDER BY id ASC LIMIT 1",
        (email,),
    ).fetchone()


def _find_customer_by_phone(conn, phone):
    if not phone:
        return None
    return conn.execute(
        "SELECT * FROM customers WHERE phone=? ORDER BY id ASC LIMIT 1", (phone,)
    ).fetchone()


def _create_customer(conn, email=None, name=None, phone=None):
    fn, ln = _split_name(name)
    if not name and email:
        name = email.split("@")[0]
    cur = conn.execute(
        "INSERT INTO customers (email, name, firstname, lastname, phone, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (email, name, fn, ln, phone, now_iso()),
    )
    return conn.execute("SELECT * FROM customers WHERE id=?", (cur.lastrowid,)).fetchone()


def _enrich_customer(conn, row, email=None, name=None, phone=None):
    """Backfill any newly-known contact info without clobbering existing values."""
    updates, args = [], []
    if email and not row["email"]:
        updates.append("email=?"); args.append(email)
    if phone and not row["phone"]:
        updates.append("phone=?"); args.append(phone)
    if name and not row["name"]:
        fn, ln = _split_name(name)
        updates.append("name=?"); args.append(name)
        updates.append("firstname=?"); args.append(fn)
        updates.append("lastname=?"); args.append(ln)
    if updates:
        conn.execute(f"UPDATE customers SET {', '.join(updates)} WHERE id=?", (*args, row["id"]))
        return conn.execute("SELECT * FROM customers WHERE id=?", (row["id"],)).fetchone()
    return row


# --- ticket ----------------------------------------------------------------
def _find_open_ticket(conn, customer_id, channel):
    row = conn.execute(
        "SELECT * FROM tickets WHERE customer_id=? AND channel=? AND status='open' "
        "ORDER BY id DESC LIMIT 1",
        (customer_id, channel),
    ).fetchone()
    if not row:
        return None
    last = _parse_iso(row["last_message_at"]) or _parse_iso(row["created_at"])
    if last is None:
        return row
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - last <= _REUSE_WINDOW:
        return row
    return None


def _create_ticket(conn, customer_id, channel, subject):
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO tickets (subject, status, channel, customer_id, is_unread, "
        "created_at, updated_at, last_message_at) VALUES (?, 'open', ?, ?, 1, ?, ?, ?)",
        (subject, channel, customer_id, ts, ts, ts),
    )
    return conn.execute("SELECT * FROM tickets WHERE id=?", (cur.lastrowid,)).fetchone()


def _derive_subject(channel, body_text):
    text = (body_text or "").strip().replace("\n", " ")
    snippet = text[:60] + ("..." if len(text) > 60 else "")
    prefix = {"chat": "Chat", "whatsapp": "WhatsApp"}.get(channel, "Message")
    return f"{prefix}: {snippet}" if snippet else f"New {prefix.lower()} conversation"


def _store_message(conn, ticket_id, channel, body_text, sender_name):
    cur = conn.execute(
        "INSERT INTO messages (ticket_id, from_agent, public, channel, body_text, "
        "sender_name, via, created_at) VALUES (?, 0, 1, ?, ?, ?, 'customer', ?)",
        (ticket_id, channel, body_text, sender_name, now_iso()),
    )
    ts = now_iso()
    conn.execute(
        "UPDATE tickets SET last_message_at=?, updated_at=?, is_unread=1 WHERE id=?",
        (ts, ts, ticket_id),
    )
    return cur.lastrowid


def _enqueue(conn, ticket_id, message_id):
    ts = now_iso()
    conn.execute(
        "INSERT INTO jobs (ticket_id, message_id, kind, status, created_at, updated_at) "
        "VALUES (?, ?, 'draft', 'queued', ?, ?)",
        (ticket_id, message_id, ts, ts),
    )


# --- public entrypoints ----------------------------------------------------
def intake_email(conn: sqlite3.Connection, data) -> dict:
    email = (data.from_email or "").strip()
    row = _find_customer_by_email(conn, email)
    if row:
        cust = _enrich_customer(conn, row, email=email, name=data.from_name)
    else:
        cust = _create_customer(conn, email=email, name=data.from_name)

    ticket = _find_open_ticket(conn, cust["id"], "email")
    if not ticket:
        subject = (data.subject or "").strip() or _derive_subject("email", data.body_text)
        ticket = _create_ticket(conn, cust["id"], "email", subject)

    sender = data.from_name or cust["name"] or email
    msg_id = _store_message(conn, ticket["id"], "email", data.body_text, sender)
    _enqueue(conn, ticket["id"], msg_id)
    audit.record(conn, ticket["id"], "intake", "channel=email", who="customer")
    conn.commit()
    return {"ticket_id": ticket["id"], "message_id": msg_id}


def intake_chat(conn: sqlite3.Connection, data) -> dict:
    session_id = data.session_id
    session = conn.execute(
        "SELECT * FROM chat_sessions WHERE session_id=?", (session_id,)
    ).fetchone()

    if session and session["ticket_id"]:
        ticket = conn.execute(
            "SELECT * FROM tickets WHERE id=?", (session["ticket_id"],)
        ).fetchone()
        cust = conn.execute(
            "SELECT * FROM customers WHERE id=?", (session["customer_id"],)
        ).fetchone()
        # If session's ticket got closed, reuse-window logic still creates a fresh one.
        if ticket and ticket["status"] != "closed":
            reuse = _find_open_ticket(conn, cust["id"], "chat")
            ticket = reuse or ticket
        else:
            ticket = _find_open_ticket(conn, cust["id"], "chat")
            if not ticket:
                ticket = _create_ticket(conn, cust["id"], "chat",
                                        _derive_subject("chat", data.body_text))
            conn.execute(
                "UPDATE chat_sessions SET ticket_id=? WHERE session_id=?",
                (ticket["id"], session_id),
            )
    else:
        row = _find_customer_by_email(conn, data.email) if data.email else None
        if row:
            cust = _enrich_customer(conn, row, email=data.email, name=data.name)
        else:
            cust = _create_customer(conn, email=data.email, name=data.name)
        ticket = _find_open_ticket(conn, cust["id"], "chat")
        if not ticket:
            ticket = _create_ticket(conn, cust["id"], "chat",
                                    _derive_subject("chat", data.body_text))
        conn.execute(
            "INSERT OR REPLACE INTO chat_sessions (session_id, customer_id, ticket_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, cust["id"], ticket["id"], now_iso()),
        )

    sender = data.name or cust["name"] or "Chat visitor"
    msg_id = _store_message(conn, ticket["id"], "chat", data.body_text, sender)
    _enqueue(conn, ticket["id"], msg_id)
    audit.record(conn, ticket["id"], "intake", "channel=chat", who="customer")
    conn.commit()
    return {"ticket_id": ticket["id"], "message_id": msg_id}


def intake_whatsapp(conn: sqlite3.Connection, data) -> dict:
    phone = (data.phone or "").strip()
    row = _find_customer_by_phone(conn, phone)
    if row:
        cust = _enrich_customer(conn, row, phone=phone, name=data.name)
    else:
        cust = _create_customer(conn, phone=phone, name=data.name)

    ticket = _find_open_ticket(conn, cust["id"], "whatsapp")
    if not ticket:
        ticket = _create_ticket(conn, cust["id"], "whatsapp",
                                _derive_subject("whatsapp", data.body_text))

    sender = data.name or cust["name"] or phone
    msg_id = _store_message(conn, ticket["id"], "whatsapp", data.body_text, sender)
    _enqueue(conn, ticket["id"], msg_id)
    audit.record(conn, ticket["id"], "intake", "channel=whatsapp", who="customer")
    conn.commit()
    return {"ticket_id": ticket["id"], "message_id": msg_id}
