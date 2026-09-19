"""Minimal MCP stdio server. Tools call the same dispatch as the CLI."""

from __future__ import annotations

import json
import sys
from typing import Any

from .dispatch import WRITE_TOOLS, invoke, list_tools
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

SCHEMAS = {
    TOOL_LIST_TICKETS: {
        "view": {"type": "string", "description": "open | closed | all | snoozed | mine | unassigned | spam | trash"},
        "limit": {"type": "integer"},
    },
    TOOL_GET_TICKET: {"ticketId": {"type": "string"}},
    TOOL_GET_CUSTOMER: {
        "shop": {"type": "string"},
        "customerId": {"type": "string", "description": "gid://shopify/Customer/…"},
    },
    TOOL_GET_ORDER: {
        "shop": {"type": "string"},
        "orderId": {"type": "string", "description": "gid://shopify/Order/…"},
    },
    TOOL_GET_RETURNS: {
        "shop": {"type": "string"},
        "orderId": {"type": "string", "description": "gid://shopify/Order/…"},
    },
    TOOL_LIST_PAST_ORDERS: {
        "shop": {"type": "string"},
        "customerId": {"type": "string", "description": "gid://shopify/Customer/…"},
    },
    TOOL_DRAFT_REPLY: {
        "ticketId": {"type": "string"},
        "shop": {"type": "string"},
        "thread": {"type": "object", "description": "already-loaded ticket thread"},
        "customer": {"type": "object"},
        "order": {"type": "object"},
        "returns": {"type": "object"},
        "pastOrders": {"type": "array"},
        "customerId": {"type": "string"},
        "orderId": {"type": "string"},
    },
    TOOL_SUMMARIZE_THREAD: {
        "ticketId": {"type": "string"},
        "shop": {"type": "string"},
        "thread": {"type": "object", "description": "already-loaded ticket thread"},
    },
    TOOL_SEARCH_MACROS: {
        "query": {"type": "string", "description": "name, tag, or body substring"},
    },
    TOOL_APPLY_MACRO: {
        "macroId": {"type": "string"},
        "mode": {"type": "string", "description": "replace | append — never a send"},
        "currentBody": {"type": "string", "description": "textarea text to append onto"},
    },
    TOOL_INGEST_EMAIL: {
        "from": {"type": "string", "description": "From name and email"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "receivedAt": {"type": "string"},
    },
    TOOL_INGEST_CHAT: {
        "fromName": {"type": "string"},
        "body": {"type": "string"},
        "receivedAt": {"type": "string"},
    },
    TOOL_PULL_MAILBOX: {
        "limit": {"type": "integer", "description": "max unread/new inbound messages to pull"},
    },
    TOOL_ESCALATE_TICKET: {
        "ticketId": {"type": "string"},
        "reason": {"type": "string", "description": "optional first-party note; never a Shopify mutation"},
    },
    TOOL_WRITE_GATE_STATUS: {},
    TOOL_BRIDGE_STATUS: {},
    TOOL_SEND_REPLY: {
        "ticketId": {"type": "string"},
        "text": {"type": "string"},
        "confirmed": {"type": "boolean", "description": "must be true after human confirms"},
        "close": {"type": "boolean"},
    },
}


def _description(name: str) -> str:
    if name == TOOL_ESCALATE_TICKET:
        return (
            "First-party helpdesk escalate. Sets escalated/pending on the ticket. "
            "Never a Shopify Admin mutation. Human still owns Send."
        )
    if name == TOOL_WRITE_GATE_STATUS:
        return (
            "Payment write-gate. Out: mutationsEnabled and refused "
            "(send, refund, cancel). Cute Things stays read-only. "
            "WRITE_TOOLS refuse those tools even if the env flag is on."
        )
    if name == TOOL_BRIDGE_STATUS:
        return (
            "Detachable Gorgias bridge status. Out: gorgiasEnabled, gorgiasConfigured, "
            "outboundEnabled, emailConfigured. Never returns secrets."
        )
    if name == TOOL_SEND_REPLY:
        return (
            "HUMAN-ONLY. Sends a customer reply via Gorgias or email after confirmation. "
            "MCP and CLI refuse this tool. Use the inbox Send button."
        )
    return f"Helpdesk tissue {name}"


def tool_descriptors() -> list[dict[str, Any]]:
    live = [
        {
            "name": name,
            "description": _description(name),
            "inputSchema": {
                "type": "object",
                "properties": SCHEMAS[name],
                "additionalProperties": False,
            },
        }
        for name in list_tools()
    ]
    refused = [
        {
            "name": name,
            "description": (
                "REFUSED. Shopify Admin write is gated. "
                "SHOPIFY_MUTATIONS_ENABLED stays 0. WRITE_TOOLS refuse send, refund, and cancel. "
                "Call helpdesk.write_gate_status for the gate payload."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
        }
        for name in sorted(WRITE_TOOLS)
    ]
    return live + refused


def handle_rpc(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    rpc_id = message.get("id")
    if method == "notifications/initialized" or rpc_id is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "helpdesk", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": tool_descriptors()}
    elif method == "tools/call":
        params = message.get("params") or {}
        payload = invoke(str(params.get("name") or ""), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": not payload.get("ok")}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def run_stdio() -> None:
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            continue
        reply = handle_rpc(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
