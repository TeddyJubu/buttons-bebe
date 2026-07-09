"""AI pipeline worker (API contract §2).

A background thread polls the jobs table every 1s. Per job:
  fetch order/return context → risk classify → brain drafts → store Draft.
Each step is logged to audit_log. The pipeline NEVER fails a ticket because an
emulator is down (context degrades to None).
"""
from __future__ import annotations

import json
import logging
import threading
import time

from . import audit, context, risk
from .brains import DraftContext, get_brain
from .db import connect, now_iso

log = logging.getLogger("fable.pipeline")

_stop = threading.Event()
_thread: threading.Thread | None = None


def queue_depth(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')"
    ).fetchone()["n"]


def _load_ticket_bundle(conn, ticket_id):
    ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not ticket:
        return None, None, None, None
    customer = conn.execute(
        "SELECT * FROM customers WHERE id=?", (ticket["customer_id"],)
    ).fetchone()
    messages = conn.execute(
        "SELECT * FROM messages WHERE ticket_id=? ORDER BY id ASC", (ticket_id,)
    ).fetchall()
    last_customer = conn.execute(
        "SELECT * FROM messages WHERE ticket_id=? AND from_agent=0 ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return ticket, customer, messages, last_customer


def process_job(conn, job) -> None:
    ticket_id = job["ticket_id"]
    ticket, customer, messages, last_customer = _load_ticket_bundle(conn, ticket_id)
    if not ticket:
        return
    last_text = last_customer["body_text"] if last_customer else ""

    # 1. Context (order/returns). Degrades to None on any transport failure.
    ctx_data = context.fetch_context(customer["email"] if customer else "")
    if ctx_data is None:
        audit.record(conn, ticket_id, "pipeline:context",
                     "no context (emulators unreachable)", who="pipeline")
        conn.execute("UPDATE tickets SET order_context=NULL WHERE id=?", (ticket_id,))
        orders, returns = [], []
    else:
        orders = ctx_data.get("orders", [])
        returns = ctx_data.get("returns", [])
        audit.record(conn, ticket_id, "pipeline:context",
                     f"orders={len(orders)} returns={len(returns)}", who="pipeline")
        conn.execute("UPDATE tickets SET order_context=? WHERE id=?",
                     (json.dumps(ctx_data), ticket_id))
    conn.commit()

    # 2. Risk classify (deterministic).
    risk_level, risk_reason = risk.classify(last_text)
    conn.execute(
        "UPDATE tickets SET sensitive=?, sensitive_reason=?, updated_at=? WHERE id=?",
        (1 if risk_level == "sensitive" else 0, risk_reason, now_iso(), ticket_id),
    )
    audit.record(conn, ticket_id, "pipeline:risk",
                 f"{risk_level}" + (f" ({risk_reason})" if risk_reason else ""),
                 who="pipeline")
    conn.commit()

    # 3. Brain drafts.
    brain = get_brain()
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
        messages=[
            {
                "from_agent": bool(m["from_agent"]),
                "body_text": m["body_text"],
                "sender_name": m["sender_name"],
                "created_at": m["created_at"],
            }
            for m in messages
        ],
        last_customer_text=last_text,
        orders=orders,
        returns=returns,
        kb_snippets=[],
        risk=risk_level,
        risk_reason=risk_reason,
    )
    try:
        result = brain.draft(dctx)
    except NotImplementedError as e:
        audit.record(conn, ticket_id, "pipeline:draft",
                     f"brain '{brain.name}' not implemented: {e}", who="pipeline")
        conn.commit()
        return

    # 4. Store draft, superseding any older proposed draft on this ticket.
    conn.execute(
        "UPDATE drafts SET status='superseded' WHERE ticket_id=? AND status='proposed'",
        (ticket_id,),
    )
    conn.execute(
        "INSERT INTO drafts (ticket_id, body_text, risk, risk_reason, brain, kb_refs, "
        "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)",
        (ticket_id, result.body_text, risk_level, risk_reason, brain.name,
         json.dumps(result.kb_refs), now_iso()),
    )
    audit.record(conn, ticket_id, "pipeline:draft",
                 f"brain={brain.name} risk={risk_level}", who="pipeline")
    conn.commit()


def _run_once(conn) -> bool:
    """Claim and process a single queued job. Returns True if one was handled."""
    row = conn.execute(
        "SELECT * FROM jobs WHERE status='queued' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if not row:
        return False
    conn.execute(
        "UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
        (now_iso(), row["id"]),
    )
    conn.commit()
    try:
        process_job(conn, row)
        conn.execute("UPDATE jobs SET status='done', updated_at=? WHERE id=?",
                     (now_iso(), row["id"]))
        conn.commit()
    except Exception as e:  # never let one bad job stall the loop
        log.exception("job %s failed", row["id"])
        conn.execute("UPDATE jobs SET status='error', error=?, updated_at=? WHERE id=?",
                     (repr(e)[:500], now_iso(), row["id"]))
        conn.commit()
    return True


def _loop() -> None:
    conn = connect()
    log.info("pipeline worker started")
    try:
        while not _stop.is_set():
            worked = False
            try:
                worked = _run_once(conn)
            except Exception:
                log.exception("pipeline loop error")
            if not worked:
                _stop.wait(1.0)
    finally:
        conn.close()
        log.info("pipeline worker stopped")


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="fable-pipeline", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=3.0)
