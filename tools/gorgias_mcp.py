"""gorgias_mcp.py -- Gorgias helpdesk as a read-only Hermes tool (its own module).

Always-on HTTP MCP service. Basic Auth (email + API key). Read-only: GET only,
no writes (posting internal notes is intentionally NOT exposed here).

Tools:
  - list_recent_tickets(limit)
  - get_ticket(ticket_id)
  - get_ticket_messages(ticket_id, limit)
  - get_customer(customer_id)          includes synced Shopify order context
  - search_customer(email)

Transport chosen by GORGIAS_MCP_TRANSPORT (stdio default | streamable-http).
Note: sets an explicit User-Agent -- Gorgias's WAF 403s the default urllib UA.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import StrictInt
from _common import _clean, load_env
from gorgias_content import curate_messages, curate_ticket

HOST = os.environ.get("GORGIAS_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("GORGIAS_MCP_PORT", "8079"))
TRANSPORT = os.environ.get("GORGIAS_MCP_TRANSPORT", "stdio")
UA = "ButtonsBebe-Hermes/1.0"


def _bare_subdomain(sub):
    s = _clean(sub).replace("https://", "").replace("http://", "").strip("/").split("/")[0]
    return s[: -len(".gorgias.com")] if s.endswith(".gorgias.com") else s


_env = load_env()
SUB = _bare_subdomain(_env.get("GORGIAS_SUBDOMAIN", ""))
EMAIL = _env.get("GORGIAS_API_EMAIL", "")
KEY = _env.get("GORGIAS_API_KEY", "")
BASE = f"https://{SUB}.gorgias.com/api"
AUTH = (EMAIL, KEY) if EMAIL and KEY else None

mcp = FastMCP("buttonsbebe-gorgias", host=HOST, port=PORT)


def _positive_int(value, name):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError(f"{name} must be a positive integer below 2**63")


def _get(path, params=None):
    if not AUTH:
        return {"error": "Gorgias not configured (email / api key missing)."}
    try:
        r = requests.get(BASE + path, params=params or {}, auth=AUTH,
                         headers={"User-Agent": UA}, timeout=20)
        if not r.ok:
            return {"error": f"Gorgias API {r.status_code}", "detail": r.text[:200]}
        return r.json()
    except Exception as e:
        return {"error": "request failed", "detail": repr(e)[:200]}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def list_recent_tickets(limit: StrictInt = 10) -> dict:
    """List recent Gorgias tickets (read-only)."""
    _positive_int(limit, "limit")
    d = _get("/tickets", {"limit": min(limit, 30), "order_by": "created_datetime:desc"})
    if isinstance(d, dict) and isinstance(d.get("data"), list):
        keep = ("id", "subject", "status", "channel", "created_datetime", "updated_datetime")
        return {"count": len(d["data"]), "tickets": [{k: t.get(k) for k in keep} for t in d["data"]]}
    return d


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def get_ticket(ticket_id: StrictInt) -> dict:
    """Get one Gorgias ticket by id (read-only)."""
    _positive_int(ticket_id, "ticket_id")
    return curate_ticket(_get(f"/tickets/{ticket_id}"))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def get_ticket_messages(ticket_id: StrictInt, limit: StrictInt = 30, cursor: str | None = None) -> dict:
    """Get one newest-first conversation page. Follow meta.next_cursor for older pages.

    Use preferred_content; full bodies may be archived/null. A page is not a full thread.
    """
    _positive_int(ticket_id, "ticket_id")
    _positive_int(limit, "limit")
    params = {"ticket_id": ticket_id, "limit": min(limit, 50), "order_by": "created_datetime:desc"}
    if cursor is not None:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 2048:
            raise ValueError("cursor must be a nonempty string of at most 2048 characters")
        params["cursor"] = cursor
    return curate_messages(_get("/messages", params))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def get_customer(customer_id: StrictInt) -> dict:
    """Get a Gorgias customer by id, including synced Shopify order context (read-only)."""
    _positive_int(customer_id, "customer_id")
    return _get(f"/customers/{customer_id}")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def search_customer(email: str) -> dict:
    """Find a Gorgias customer by email address (read-only)."""
    return _get("/customers", {"email": email})


if __name__ == "__main__":
    mcp.run(transport=TRANSPORT)
