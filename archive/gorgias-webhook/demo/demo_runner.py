#!/usr/bin/env python3
"""
demo_runner.py — Build webhook payloads and invoke the real pipeline + workflows.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from typing import Any, Dict, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)

# Patches must be applied before importing server (WORKFLOW_A_CONFIRM read at import)
import demo_patches
demo_patches.apply()

import gorgias_api
import pipeline
import server
from demo_store import get_store


def build_webhook_payload(
    ticket_id: int,
    *,
    from_agent: bool = False,
    event_type: str = "ticket-message-created",
) -> dict:
    """Build a Gorgias HTTP integration-shaped webhook payload."""
    return {
        "event": {"type": event_type},
        "ticket": {
            "id": str(ticket_id),
            "last_message": {"from_agent": from_agent},
        },
    }


def _run_workflow(ticket_id: int, from_agent: bool, event_type: str) -> dict:
    started = time.time()
    store = get_store()
    payload = build_webhook_payload(ticket_id, from_agent=from_agent, event_type=event_type)

    base_url, username, api_key = gorgias_api.load_credentials()
    ctx = pipeline.fetch_ticket_context(payload, base_url, username, api_key)

    route = server.route_for_event(ctx.event_type, ctx.from_agent)
    workflow = None
    error = None

    try:
        if route == "A":
            workflow = "A"
            server.run_workflow_a(ctx)
        elif route == "B":
            workflow = "B"
            server.run_workflow_b(ctx)
        else:
            workflow = None
    except Exception as exc:
        error = str(exc)
        traceback.print_exc()

    elapsed_ms = int((time.time() - started) * 1000)

    # Collect post-run state
    detail = store.get_ticket_detail(ticket_id)
    internal_notes = [
        m for m in (detail or {}).get("messages", [])
        if m.get("channel") == "internal-note"
    ]

    result = {
        "ticket_id": ticket_id,
        "workflow": workflow,
        "route": route,
        "from_agent": from_agent,
        "event_type": event_type,
        "elapsed_ms": elapsed_ms,
        "errors": list(ctx.errors or []),
        "pipeline_error": error,
        "summary": ctx.summary() if hasattr(ctx, "summary") else None,
        "internal_note_count": len(internal_notes),
        "last_internal_note": internal_notes[-1] if internal_notes else None,
        "telegram_notify_count": len(store.get_telegram("notify")),
        "telegram_priority_count": len(store.get_telegram("priority")),
        "priority": (detail or {}).get("ticket", {}).get("priority"),
        "tags": (detail or {}).get("ticket", {}).get("tags"),
    }
    store.record_run(ticket_id, result)
    return result


def run_customer_message(ticket_id: int, *, is_new_ticket: bool = False) -> dict:
    event = "ticket-created" if is_new_ticket else "ticket-message-created"
    return _run_workflow(ticket_id, from_agent=False, event_type=event)


def run_agent_reply(ticket_id: int) -> dict:
    return _run_workflow(ticket_id, from_agent=True, event_type="ticket-message-created")


def create_and_run(
    email: str,
    subject: str,
    message: str,
    *,
    name: Optional[str] = None,
) -> dict:
    store = get_store()
    ticket = store.create_ticket(email, subject, message, name=name)
    result = run_customer_message(ticket["id"], is_new_ticket=True)
    result["ticket"] = ticket
    return result


def add_customer_message_and_run(ticket_id: int, message: str) -> dict:
    store = get_store()
    msg = store.add_customer_message(ticket_id, message)
    if msg is None:
        return {"error": "Ticket not found", "ticket_id": ticket_id}
    result = run_customer_message(ticket_id, is_new_ticket=False)
    result["message"] = msg
    return result


def add_agent_reply_and_run(ticket_id: int, message: str) -> dict:
    store = get_store()
    msg = store.add_agent_public_reply(ticket_id, message)
    if msg is None:
        return {"error": "Ticket not found", "ticket_id": ticket_id}
    result = run_agent_reply(ticket_id)
    result["message"] = msg
    return result
