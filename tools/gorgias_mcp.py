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
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from mcp.server.fastmcp import FastMCP
from _common import load_env

HOST = os.environ.get("GORGIAS_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("GORGIAS_MCP_PORT", "8079"))
TRANSPORT = os.environ.get("GORGIAS_MCP_TRANSPORT", "stdio")
UA = "ButtonsBebe-Hermes/1.0"


def _clean(v):
    return re.sub(r'^[\s"\']+|[\s"\'\\]+$', "", v).replace("\r", "")


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


@mcp.tool()
def list_recent_tickets(limit: int = 10) -> dict:
    """List recent Gorgias tickets (read-only)."""
    d = _get("/tickets", {"limit": min(limit, 30)})
    if isinstance(d, dict) and isinstance(d.get("data"), list):
        keep = ("id", "subject", "status", "channel", "created_datetime", "updated_datetime")
        return {"count": len(d["data"]), "tickets": [{k: t.get(k) for k in keep} for t in d["data"]]}
    return d


@mcp.tool()
def get_ticket(ticket_id: int) -> dict:
    """Get one Gorgias ticket by id (read-only)."""
    return _get(f"/tickets/{ticket_id}")


@mcp.tool()
def get_ticket_messages(ticket_id: int, limit: int = 30) -> dict:
    """Get the messages/conversation of a Gorgias ticket (read-only)."""
    return _get(f"/tickets/{ticket_id}/messages", {"limit": min(limit, 50)})


@mcp.tool()
def get_customer(customer_id: int) -> dict:
    """Get a Gorgias customer by id, including synced Shopify order context (read-only)."""
    return _get(f"/customers/{customer_id}")


@mcp.tool()
def search_customer(email: str) -> dict:
    """Find a Gorgias customer by email address (read-only)."""
    return _get("/customers", {"email": email})


if __name__ == "__main__":
    mcp.run(transport=TRANSPORT)
