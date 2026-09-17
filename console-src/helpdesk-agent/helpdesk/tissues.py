"""Tissue handlers. Input → output only. Same path for MCP / CLI / HTTP."""

from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import Any

# Sibling bridge package sits next to helpdesk/
_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from bridge import config as bridge_config  # noqa: E402
from bridge.router import reply_route  # noqa: E402

from . import tickets
from .composer import handle_draft_reply, handle_summarize_thread
from .intake import handle_ingest_chat, handle_ingest_email
from .mailbox import handle_pull_mailbox
from .macros import handle_apply_macro, handle_search_macros
from .names import (
    TOOL_APPLY_MACRO,
    TOOL_BRIDGE_STATUS,
    TOOL_DRAFT_REPLY,
    TOOL_ESCALATE_TICKET,
    TOOL_GET_CUSTOMER,
    TOOL_GET_ORDER,
    TOOL_GET_RETURNS,
    TOOL_GET_TICKET,
    TOOL_INGEST_CHAT,
    TOOL_INGEST_EMAIL,
    TOOL_PULL_MAILBOX,
    TOOL_LIST_PAST_ORDERS,
    TOOL_LIST_TICKETS,
    TOOL_SEARCH_MACROS,
    TOOL_SEND_REPLY,
    TOOL_SUMMARIZE_THREAD,
    TOOL_WRITE_GATE_STATUS,
)
from .env import load_shopify_env, mutations_enabled
from .errors import REFUSED_WRITES, HelpdeskError, bad_request
from .shop import rail_get_customer, rail_get_order, rail_get_returns, rail_list_past_orders


def _ticket_gid_source() -> str:
    forced = (load_shopify_env().get("HELPDESK_SOURCE") or "").strip().lower()
    if forced in {"live", "live-holes"}:
        return forced
    return "sample"


def _source_label() -> str:
    return "inbox" if os.environ.get("HELPDESK_PRODUCTION") == "1" else "sample"


def handle_list_tickets(args: dict[str, Any]) -> dict[str, Any]:
    view = str(args.get("view") or "open")
    limit = args.get("limit", 20)
    gid_source = _ticket_gid_source()
    return {"source": _source_label(), "tickets": tickets.list_tickets(view, limit, gid_source)}


def handle_get_ticket(args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = args.get("ticketId") or args.get("ticket_id")
    gid_source = _ticket_gid_source()
    return {
        "source": _source_label(),
        "ticket": tickets.get_ticket(str(ticket_id) if ticket_id is not None else "", gid_source),
    }


def handle_escalate_ticket(args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = args.get("ticketId") or args.get("ticket_id")
    reason = args.get("reason")
    gid_source = _ticket_gid_source()
    return {
        "source": _source_label(),
        "ticket": tickets.escalate_ticket(
            str(ticket_id) if ticket_id is not None else "",
            None if reason is None else str(reason),
            gid_source,
        ),
    }


def handle_write_gate_status(_args: dict[str, Any]) -> dict[str, Any]:
    enabled = mutations_enabled()
    refused = list(REFUSED_WRITES)
    return {
        "mutationsEnabled": enabled,
        "refused": refused,
        "tools": [f"helpdesk.{name}" for name in REFUSED_WRITES],
        "message": "Shopify writes are refused. SHOPIFY_MUTATIONS_ENABLED stays 0.",
    }


def handle_bridge_status(_args: dict[str, Any]) -> dict[str, Any]:
    return bridge_config.bridge_status()


def handle_send_reply(args: dict[str, Any]) -> dict[str, Any]:
    """Human-confirmed outbound reply. Locked off until send access is activated."""
    from .send_access import refuse_send, send_access_enabled

    ticket_id = args.get("ticketId") or args.get("ticket_id")
    if not send_access_enabled():
        refuse_send(ticket_id=str(ticket_id) if ticket_id else None)
    text = str(args.get("text") or "").strip()
    confirmed = args.get("confirmed") is True
    close = args.get("close") is True
    if not ticket_id:
        raise bad_request("ticketId is required", field="ticketId")
    if not text:
        raise bad_request("text is required", field="text")
    if not confirmed:
        raise HelpdeskError(
            "confirmation_required",
            "Send requires confirmed: true after the human confirms.",
            details={"ticketId": str(ticket_id)},
        )
    if not bridge_config.outbound_enabled():
        raise HelpdeskError(
            "outbound_disabled",
            "HELPDESK_OUTBOUND_ENABLED is off. Send stays local.",
            details={"ticketId": str(ticket_id)},
        )
    raw = tickets.find_ticket(str(ticket_id))
    if raw is None:
        from .errors import not_found

        raise not_found("ticket", str(ticket_id))
    if tickets.is_seed_ticket(str(ticket_id)):
        raise HelpdeskError(
            "no_real_recipient",
            "Seed / fixture tickets cannot send real email.",
            details={"ticketId": str(ticket_id)},
        )
    recipient = str(raw.get("fromEmail") or "").strip().lower()
    if not recipient or "@" not in recipient:
        raise HelpdeskError(
            "no_real_recipient",
            "Ticket has no customer email to send to.",
            details={"ticketId": str(ticket_id)},
        )
    allow = bridge_config.send_allowlist()
    if allow and recipient not in allow:
        raise HelpdeskError(
            "recipient_not_allowed",
            "Recipient is outside HELPDESK_SEND_ALLOWLIST.",
            details={"ticketId": str(ticket_id)},
        )

    projected = tickets.get_ticket(str(ticket_id), _ticket_gid_source())
    route = reply_route(projected)
    via = route
    delivery_status = "sent"
    external_message_id = None

    if route == "gorgias":
        ext = projected.get("external") or {}
        gorgias_ticket_id = ext.get("ticketId")
        if not gorgias_ticket_id:
            raise HelpdeskError(
                "missing_external_ticket",
                "Gorgias ticket id is missing on this ticket.",
                details={"ticketId": str(ticket_id)},
            )
        from bridge import gorgias_api  # lazy — only when switch routes here

        result = gorgias_api.send_public_reply(gorgias_ticket_id, text)
        if not result.get("ok"):
            raise HelpdeskError(
                "send_failed",
                str(result.get("error") or "Gorgias send failed"),
                details={"ticketId": str(ticket_id), "via": "gorgias"},
            )
        external_message_id = result.get("messageId")
        delivery_status = result.get("deliveryStatus") or "sent"
        if close:
            gorgias_api.close_ticket(gorgias_ticket_id)
    elif route == "email":
        from bridge import email_out

        from .fixtures_intake import MAILBOX_ADDRESS

        agentmail_mid = None
        ext = projected.get("external") or {}
        if ext.get("system") == "agentmail":
            agentmail_mid = ext.get("messageId")
        # Prefer first customer message external id / intake message id
        for message in raw.get("messages") or []:
            if isinstance(message, dict) and not message.get("fromAgent"):
                agentmail_mid = agentmail_mid or message.get("externalMessageId")
                break
        result = email_out.send_email_reply(
            to_email=recipient,
            subject=str(raw.get("subject") or ""),
            body=text,
            message_id=agentmail_mid,
            mailbox=MAILBOX_ADDRESS,
        )
        if not result.get("ok"):
            raise HelpdeskError(
                "send_failed",
                str(result.get("error") or "Email send failed"),
                details={"ticketId": str(ticket_id), "via": "email"},
            )
        external_message_id = result.get("messageId")
        delivery_status = result.get("deliveryStatus") or "sent"
        via = "email"
    else:
        raise HelpdeskError(
            "outbound_disabled",
            "Outbound is local-only on this host.",
            details={"ticketId": str(ticket_id)},
        )

    ticket = tickets.append_agent_message(
        str(ticket_id),
        text,
        via=via,
        external_message_id=external_message_id,
        delivery_status=delivery_status,
        close=close,
        gid_source=_ticket_gid_source(),
    )
    return {
        "ticket": ticket,
        "via": via,
        "deliveryStatus": delivery_status,
        "externalMessageId": external_message_id,
    }


def handle_get_customer(args: dict[str, Any]) -> dict[str, Any]:
    source, customer = rail_get_customer(args.get("shop"), args.get("customerId") or args.get("customer_id"))
    return {"source": source, "customer": customer}


def handle_get_order(args: dict[str, Any]) -> dict[str, Any]:
    source, order = rail_get_order(args.get("shop"), args.get("orderId") or args.get("order_id"))
    return {"source": source, "order": order}


def handle_get_returns(args: dict[str, Any]) -> dict[str, Any]:
    source, payload = rail_get_returns(args.get("shop"), args.get("orderId") or args.get("order_id"))
    return {"source": source, **payload}


def handle_list_past_orders(args: dict[str, Any]) -> dict[str, Any]:
    source, orders = rail_list_past_orders(args.get("shop"), args.get("customerId") or args.get("customer_id"))
    return {"source": source, "orders": orders}


HANDLERS = {
    TOOL_LIST_TICKETS: handle_list_tickets,
    TOOL_GET_TICKET: handle_get_ticket,
    TOOL_GET_CUSTOMER: handle_get_customer,
    TOOL_GET_ORDER: handle_get_order,
    TOOL_GET_RETURNS: handle_get_returns,
    TOOL_LIST_PAST_ORDERS: handle_list_past_orders,
    TOOL_DRAFT_REPLY: handle_draft_reply,
    TOOL_SUMMARIZE_THREAD: handle_summarize_thread,
    TOOL_SEARCH_MACROS: handle_search_macros,
    TOOL_APPLY_MACRO: handle_apply_macro,
    TOOL_INGEST_EMAIL: handle_ingest_email,
    TOOL_INGEST_CHAT: handle_ingest_chat,
    TOOL_PULL_MAILBOX: handle_pull_mailbox,
    TOOL_ESCALATE_TICKET: handle_escalate_ticket,
    TOOL_WRITE_GATE_STATUS: handle_write_gate_status,
    TOOL_BRIDGE_STATUS: handle_bridge_status,
    TOOL_SEND_REPLY: handle_send_reply,
}
