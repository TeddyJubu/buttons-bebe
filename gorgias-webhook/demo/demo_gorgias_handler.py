#!/usr/bin/env python3
"""
demo_gorgias_handler.py — Mock Gorgias REST handler for the demo store.

Implements paths used by gorgias_api.py so pipeline.fetch_ticket_context and
Workflow A/B writes work against in-memory state.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Optional, Tuple

from demo_store import get_store

DEFAULT_AGENT_USER_ID = 777419526


def handle(method: str, path: str, body: Optional[dict] = None) -> Tuple[int, Any]:
    """Dispatch a mock Gorgias REST request. Returns (status, json_body)."""
    method = (method or "GET").upper()
    parsed = urllib.parse.urlparse(path if "://" not in path else path.split("://", 1)[1])
    route = parsed.path.rstrip("/") or "/"
    query = urllib.parse.parse_qs(parsed.query)

    store = get_store()

    # GET /api/tickets/:id
    m = re.match(r"^/api/tickets/(\d+)$", route)
    if method == "GET" and m:
        ticket = store.get_ticket(int(m.group(1)))
        if ticket is None:
            return 404, {"error": "Ticket not found"}
        return 200, ticket

    # GET /api/messages?ticket_id=
    if method == "GET" and route == "/api/messages":
        ticket_ids = query.get("ticket_id", [])
        if not ticket_ids:
            return 400, {"error": "ticket_id required"}
        tid = int(ticket_ids[0])
        limit = int((query.get("limit") or ["100"])[0])
        data = store.list_messages(tid, limit=limit)
        return 200, {"data": data, "meta": {}}

    # GET /api/customers/:id
    m = re.match(r"^/api/customers/(\d+)$", route)
    if method == "GET" and m:
        customer = store.get_customer(int(m.group(1)))
        if customer is None:
            return 404, {"error": "Customer not found"}
        return 200, customer

    # POST /api/tickets/:id/messages
    m = re.match(r"^/api/tickets/(\d+)/messages$", route)
    if method == "POST" and m:
        tid = int(m.group(1))
        payload = body or {}
        msg = store.post_message_from_payload(tid, payload)
        if msg is None:
            return 404, {"error": "Ticket not found"}
        return 201, msg

    # POST /api/tickets/:id/tags
    m = re.match(r"^/api/tickets/(\d+)/tags$", route)
    if method == "POST" and m:
        tid = int(m.group(1))
        names = (body or {}).get("names") or []
        ticket = store.add_tags(tid, names)
        if ticket is None:
            return 404, {"error": "Ticket not found"}
        return 200, ticket

    # PUT /api/tickets/:id
    m = re.match(r"^/api/tickets/(\d+)$", route)
    if method == "PUT" and m:
        tid = int(m.group(1))
        priority = (body or {}).get("priority")
        if priority:
            ticket = store.set_priority(tid, priority)
            if ticket is None:
                return 404, {"error": "Ticket not found"}
            return 200, ticket
        ticket = store.get_ticket(tid)
        if ticket is None:
            return 404, {"error": "Ticket not found"}
        return 200, ticket

    return 404, {"error": f"Unknown route {method} {route}"}


def handle_url(method: str, url: str, body: Optional[dict] = None) -> Tuple[int, Any]:
    """Parse a full URL and dispatch."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path
    if parsed.query:
        path = path + "?" + parsed.query
    return handle(method, path, body)
