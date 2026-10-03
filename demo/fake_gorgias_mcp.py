"""Fixture-backed, read-only Gorgias MCP service for the Cute Things demo.

The public tool surface intentionally mirrors ``tools/gorgias_mcp.py``:
``list_recent_tickets``, ``list_inbox_tickets``, ``get_ticket``,
``get_ticket_messages``, ``get_customer``, and ``search_customer``.  The
fixture data is synthetic and the module has no HTTP client, credentials, or
write operation.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

try:
    from .local_only import require_loopback
except ImportError:  # direct script execution
    from local_only import require_loopback


DEFAULT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gorgias.json"
HOST = require_loopback(
    os.environ.get("GORGIAS_MCP_HOST", "127.0.0.1"),
    "fake Gorgias MCP",
)
PORT = int(os.environ.get("GORGIAS_MCP_PORT", "8179"))
TRANSPORT = os.environ.get(
    "TRANSPORT", os.environ.get("GORGIAS_MCP_TRANSPORT", "stdio")
)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # Tests can use the fixture helpers without MCP installed.
    FastMCP = None  # type: ignore[assignment,misc]


class _FallbackMCP:
    """Small decorator shim so dependency-light tests see the real tool names."""

    def tool(self):
        return lambda function: function

    def run(self, **_kwargs: Any) -> None:
        raise RuntimeError("MCP is required to run the fake service")


mcp = FastMCP("cute-things-demo-gorgias", host=HOST, port=PORT) if FastMCP is not None else _FallbackMCP()


def fixture_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Return the selected fixture path without reading any environment secret."""

    return Path(path or os.environ.get("DEMO_GORGIAS_FIXTURES", DEFAULT_FIXTURES))


def load_fixtures(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load and validate the deterministic demo Gorgias document."""

    with fixture_path(path).open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("Gorgias fixtures must be a JSON object")
    if not isinstance(data.get("tickets"), list) or not isinstance(data.get("customers"), list):
        raise ValueError("Gorgias fixtures must contain list fields: tickets and customers")
    if not all(bool(item.get("synthetic")) for item in data["tickets"] + data["customers"]):
        raise ValueError("every Gorgias fixture record must be synthetic")
    return data


def _tickets() -> list[dict[str, Any]]:
    return load_fixtures()["tickets"]


def _customers() -> list[dict[str, Any]]:
    return load_fixtures()["customers"]


def _ticket(ticket_id: int | str) -> dict[str, Any] | None:
    wanted = str(ticket_id)
    return next((item for item in _tickets() if str(item.get("id")) == wanted), None)


def _customer(customer_id: int | str) -> dict[str, Any] | None:
    wanted = str(customer_id)
    return next((item for item in _customers() if str(item.get("id")) == wanted), None)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"error": message, **extra}


@mcp.tool()
def list_recent_tickets(limit: int = 10) -> dict[str, Any]:
    """List recent synthetic Gorgias tickets (read-only)."""

    safe_limit = max(0, min(int(limit), 30))
    keep = (
        "id",
        "subject",
        "status",
        "channel",
        "created_datetime",
        "updated_datetime",
    )
    tickets = [{key: item.get(key) for key in keep} for item in _tickets()[:safe_limit]]
    return {"count": len(tickets), "tickets": copy.deepcopy(tickets)}


INBOX_FIELDS = (
    "id", "subject", "status", "priority", "channel", "customer", "assignee_user",
    "assignee_team", "created_datetime", "updated_datetime", "last_message_datetime",
    "last_received_message_datetime", "excerpt", "tags", "spam", "trashed_datetime",
)
CURSOR_PREFIX = "demo-cursor:"


@mcp.tool()
def list_inbox_tickets(limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
    """Page synthetic tickets by last update with an opaque cursor (read-only)."""

    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    offset = 0
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor.startswith(CURSOR_PREFIX):
            raise ValueError("Invalid ticket cursor")
        position = cursor.removeprefix(CURSOR_PREFIX)
        if not position.isascii() or not position.isdigit():
            raise ValueError("Invalid ticket cursor")
        offset = int(position)
    ordered = sorted(_tickets(), key=lambda item: item.get("updated_datetime", ""), reverse=True)
    page = ordered[offset:offset + min(limit, 100)]
    end = offset + len(page)
    return {
        "data": [{key: copy.deepcopy(item.get(key)) for key in INBOX_FIELDS} for item in page],
        "meta": {"next_cursor": f"{CURSOR_PREFIX}{end}" if end < len(ordered) else None},
    }


@mcp.tool()
def get_ticket(ticket_id: int) -> dict[str, Any]:
    """Get one synthetic Gorgias ticket by id (read-only)."""

    ticket = _ticket(ticket_id)
    if ticket is None:
        return _error("ticket not found", ticket_id=ticket_id)
    result = copy.deepcopy(ticket)
    # The live endpoint returns the ticket object, not its private fixture-only
    # storage key.  Messages remain available through the dedicated tool.
    result.pop("synthetic", None)
    result.pop("messages", None)
    return result


@mcp.tool()
def get_ticket_messages(ticket_id: int, limit: int = 30) -> dict[str, Any]:
    """Get a synthetic ticket conversation (read-only)."""

    ticket = _ticket(ticket_id)
    if ticket is None:
        return _error("ticket not found", ticket_id=ticket_id)
    safe_limit = max(0, min(int(limit), 50))
    messages = copy.deepcopy(ticket.get("messages", [])[:safe_limit])
    return {"data": messages, "count": len(messages)}


@mcp.tool()
def get_customer(customer_id: int) -> dict[str, Any]:
    """Get a synthetic customer and synced Shopify order context (read-only)."""

    customer = _customer(customer_id)
    if customer is None:
        return _error("customer not found", customer_id=customer_id)
    result = copy.deepcopy(customer)
    result.pop("synthetic", None)
    return result


@mcp.tool()
def search_customer(email: str) -> dict[str, Any]:
    """Find synthetic customers by email address (read-only)."""

    query = str(email).strip().casefold()
    matches = [
        copy.deepcopy(customer)
        for customer in _customers()
        if str(customer.get("email", "")).strip().casefold() == query
    ]
    for customer in matches:
        customer.pop("synthetic", None)
    return {"data": matches, "count": len(matches)}


def main() -> None:
    """Run the local MCP service using the configured streamable transport."""

    mcp.run(transport=TRANSPORT)


if __name__ == "__main__":
    main()
