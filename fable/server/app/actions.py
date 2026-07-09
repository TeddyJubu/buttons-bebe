"""Ticket actions — send / note / rewrite (API contract §1).

Mirror the VPS console verbs. Send is customer-facing (per-channel transport);
note is internal-only; rewrite asks the brain to revise the current draft.
Every action is audited. Transport failure on Send => 502 and the draft stays
proposed (nothing left the system).
"""
from __future__ import annotations

import json
import logging
import sqlite3

import httpx
from fastapi import HTTPException

from . import audit, config, tickets
from .brains import DraftContext, get_brain
from .db import now_iso

log = logging.getLogger("fable.actions")

_TIMEOUT = 5.0


def _ticket(conn, ticket_id):
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="ticket not found")
    return row


def _customer(conn, customer_id):
    return conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()


def _current_proposed_draft(conn, ticket_id):
    return conn.execute(
        "SELECT * FROM drafts WHERE ticket_id=? AND status='proposed' ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()


def _store_message(conn, ticket_id, *, channel, body_text, public, from_agent,
                   sender_name, via):
    cur = conn.execute(
        "INSERT INTO messages (ticket_id, from_agent, public, channel, body_text, "
        "sender_name, via, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ticket_id, 1 if from_agent else 0, 1 if public else 0, channel, body_text,
         sender_name, via, now_iso()),
    )
    ts = now_iso()
    conn.execute(
        "UPDATE tickets SET last_message_at=?, updated_at=? WHERE id=?",
        (ts, ts, ticket_id),
    )
    return conn.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()


# --- transports ------------------------------------------------------------
def _send_email_transport(to, subject, body_text) -> None:
    """Raise HTTPException(502) if the mailbox transport is unreachable/errors."""
    url = f"{config.MAILBOX_BASE}/send"
    try:
        # trust_env=False: the mailbox is a localhost service — never route this
        # through an environment proxy (keeps everything on localhost).
        r = httpx.post(url, json={"to": to, "subject": subject, "body_text": body_text},
                       timeout=_TIMEOUT, trust_env=False)
    except Exception as e:
        log.warning("mailbox send failed: %r", e)
        raise HTTPException(status_code=502, detail="email transport unavailable")
    if r.status_code // 100 != 2:
        log.warning("mailbox send non-2xx: %s %s", r.status_code, r.text[:120])
        raise HTTPException(status_code=502, detail="email transport error")


# --- actions ---------------------------------------------------------------
def send(conn: sqlite3.Connection, ticket_id: int, text: str) -> dict:
    ticket = _ticket(conn, ticket_id)
    if ticket["status"] == "closed":
        raise HTTPException(status_code=409, detail="ticket is closed")

    channel = ticket["channel"]
    customer = _customer(conn, ticket["customer_id"])

    if channel == "email":
        to = customer["email"] if customer else None
        # Attempt transport FIRST — on failure nothing is stored, draft stays proposed.
        _send_email_transport(to, ticket["subject"], text)
    elif channel == "whatsapp":
        conn.execute(
            "INSERT INTO whatsapp_outbox (ticket_id, phone, body_text, created_at) "
            "VALUES (?, ?, ?, ?)",
            (ticket_id, customer["phone"] if customer else None, text, now_iso()),
        )
    # chat: no external transport — the stored message is served via long-poll.

    msg = _store_message(
        conn, ticket_id, channel=channel, body_text=text, public=True,
        from_agent=True, sender_name="Buttons Bebe Care Team", via="console",
    )
    # Mark the proposed draft as sent; ticket stays open (human closes manually).
    draft = _current_proposed_draft(conn, ticket_id)
    if draft:
        conn.execute("UPDATE drafts SET status='sent' WHERE id=?", (draft["id"],))
    conn.execute("UPDATE tickets SET is_unread=0, updated_at=? WHERE id=?",
                 (now_iso(), ticket_id))
    audit.record(conn, ticket_id, "send", f"channel={channel} chars={len(text)}")
    conn.commit()
    return {"ok": True, "message": tickets.message_to_dict(msg)}


def note(conn: sqlite3.Connection, ticket_id: int, text: str) -> dict:
    _ticket(conn, ticket_id)
    msg = _store_message(
        conn, ticket_id, channel="internal-note", body_text=text, public=False,
        from_agent=True, sender_name="Buttons Bebe Care Team", via="console",
    )
    # Saving a note consumes the current proposed draft (console "Save as note").
    draft = _current_proposed_draft(conn, ticket_id)
    if draft:
        conn.execute("UPDATE drafts SET status='noted' WHERE id=?", (draft["id"],))
    audit.record(conn, ticket_id, "note", f"chars={len(text)}")
    conn.commit()
    return {"ok": True, "message": tickets.message_to_dict(msg)}


def rewrite(conn: sqlite3.Connection, ticket_id: int, instruction: str) -> dict:
    ticket = _ticket(conn, ticket_id)
    customer = _customer(conn, ticket["customer_id"])
    current = _current_proposed_draft(conn, ticket_id)
    if not current:
        raise HTTPException(status_code=409, detail="no draft to rewrite")

    last_customer = conn.execute(
        "SELECT * FROM messages WHERE ticket_id=? AND from_agent=0 ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    oc = ticket["order_context"]
    ctx_data = {}
    if oc:
        try:
            ctx_data = json.loads(oc)
        except (ValueError, TypeError):
            ctx_data = {}

    dctx = DraftContext(
        ticket_id=ticket_id,
        subject=ticket["subject"] or "",
        channel=ticket["channel"],
        customer={
            "id": customer["id"] if customer else None,
            "name": customer["name"] if customer else None,
            "firstname": customer["firstname"] if customer else None,
            "lastname": customer["lastname"] if customer else None,
            "email": customer["email"] if customer else None,
            "phone": customer["phone"] if customer else None,
        },
        messages=[],
        last_customer_text=last_customer["body_text"] if last_customer else "",
        orders=ctx_data.get("orders", []),
        returns=ctx_data.get("returns", []),
        kb_snippets=[],
        risk=current["risk"],
        risk_reason=current["risk_reason"],
    )
    brain = get_brain()
    result = brain.rewrite(dctx, current["body_text"], instruction)

    conn.execute("UPDATE drafts SET status='superseded' WHERE id=?", (current["id"],))
    cur = conn.execute(
        "INSERT INTO drafts (ticket_id, body_text, risk, risk_reason, brain, kb_refs, "
        "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)",
        (ticket_id, result.body_text, current["risk"], current["risk_reason"],
         brain.name, json.dumps(result.kb_refs), now_iso()),
    )
    audit.record(conn, ticket_id, "rewrite", f"instruction={instruction[:80]}")
    conn.commit()
    new_draft = conn.execute("SELECT * FROM drafts WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"draft": tickets.draft_to_dict(new_draft)}
