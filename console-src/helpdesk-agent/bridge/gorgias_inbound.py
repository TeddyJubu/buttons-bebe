"""Inbound Gorgias HTTP Integration door. Secret-gated; echo-safe."""

from __future__ import annotations

import hmac
import json
from typing import Any

from .config import gorgias_bridge_enabled
import os


def verify_secret(headers: dict[str, str] | None, query_secret: str | None = None) -> bool:
    """Header-only secret check. Query secrets are ignored (log leak risk)."""
    expected = os.environ.get("GORGIAS_BRIDGE_SECRET", "").strip()
    if not expected:
        return False
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    candidates: list[str] = []
    auth = headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        candidates.append(auth[7:].strip())
    bridge = headers.get("x-bridge-secret", "").strip()
    if bridge:
        candidates.append(bridge)
    # query_secret intentionally unused — keep signature for review_server callers.
    _ = query_secret
    return any(hmac.compare_digest(candidate, expected) for candidate in candidates if candidate)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    return text in {"true", "1", "yes"}


def parse_event(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload or {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    ticket = raw.get("ticket") if isinstance(raw.get("ticket"), dict) else data.get("ticket")
    message = raw.get("message") if isinstance(raw.get("message"), dict) else data.get("message")
    if not isinstance(ticket, dict):
        ticket = {}
    if not isinstance(message, dict):
        message = {}
    customer = ticket.get("customer") if isinstance(ticket.get("customer"), dict) else {}
    if isinstance(ticket.get("customer"), str):
        try:
            customer = json.loads(ticket["customer"])
        except (TypeError, ValueError, json.JSONDecodeError):
            customer = {"email": ticket.get("customer")}
    body = (
        message.get("body_text")
        or message.get("stripped_text")
        or message.get("text")
        or ""
    )
    sender = message.get("sender")
    author_email = None
    if isinstance(sender, dict):
        author_email = sender.get("email") or sender.get("address")
    elif isinstance(sender, str) and "@" in sender:
        author_email = sender
    customer_email = customer.get("email") if isinstance(customer, dict) else None
    customer_name = None
    if isinstance(customer, dict):
        customer_name = customer.get("name") or customer.get("firstname")
    return {
        "ticket_id": ticket.get("id"),
        "message_id": message.get("id"),
        "event_type": raw.get("trigger") or raw.get("event") or raw.get("type") or "unknown",
        "from_agent": _coerce_bool(message.get("from_agent")),
        "subject": ticket.get("subject") or "",
        "body": str(body or ""),
        "channel": message.get("channel") or ticket.get("channel") or "email",
        "created_at": (
            message.get("created_datetime")
            or message.get("created_at")
            or message.get("received_at")
            or ticket.get("created_datetime")
            or ""
        ),
        "customer_email": customer_email or author_email,
        "customer_name": customer_name,
        "author_email": author_email,
    }


def accept(payload: dict[str, Any] | None, *, invoke) -> dict[str, Any]:
    """Parse + gate + ingest. `invoke` is helpdesk.dispatch.invoke."""
    if not gorgias_bridge_enabled():
        return {"ok": False, "status": "bridge_disabled", "http": 503}
    event = parse_event(payload)
    if event["from_agent"]:
        return {"ok": True, "status": "ignored_agent_message", "http": 200}
    if event["ticket_id"] in (None, "") or event["message_id"] in (None, ""):
        return {"ok": False, "status": "missing_ticket_or_message_id", "http": 400}

    # Local import keeps helpdesk tickets out of bridge module load for unit tests
    from helpdesk import tickets as ticket_store

    mid = str(event["message_id"])
    if ticket_store.seen_message_id(mid, source="gorgias"):
        return {"ok": True, "status": "duplicate", "http": 200, "messageId": mid}

    email = event["customer_email"] or "unknown@example.com"
    name = event["customer_name"] or email
    from_header = f"{name} <{email}>" if name and name != email else email
    result = invoke(
        "helpdesk.ingest_email",
        {
            "from": from_header,
            "subject": event["subject"] or "(no subject)",
            "body": event["body"] or "",
            "receivedAt": event["created_at"] or "1970-01-01T00:00:00Z",
            "messageId": mid,
            "source": "gorgias",
            "_fromBridge": True,
            "external": {
                "system": "gorgias",
                "ticketId": str(event["ticket_id"]),
                "messageId": mid,
                "customerEmail": email,
            },
        },
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "status": "ingest_failed",
            "http": 500,
            "error": result.get("error"),
            "message": result.get("message"),
        }
    if result.get("spam"):
        return {"ok": True, "status": "spam", "http": 200, "ticketId": result.get("ticketId")}
    ticket_id = result.get("ticketId") or result.get("id")
    return {
        "ok": True,
        "status": "accepted",
        "http": 200,
        "ticketId": ticket_id,
        "source": "gorgias",
        "externalTicketId": str(event["ticket_id"]),
    }
