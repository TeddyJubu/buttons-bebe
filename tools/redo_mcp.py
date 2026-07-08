"""redo_mcp.py -- Redo Returns as a read-only Hermes tool (its own module).

Runs as an always-on HTTP MCP service (like the KB). Reads REDO_API_KEY and
REDO_STORE_ID from the agent's .env. Read-only: only GET requests, no writes.

Tools:
  - list_recent_returns(limit)         recent returns/RMAs
  - get_returns_for_order(order_name)  returns for a specific Shopify order
  - get_return(return_id)              one return by id

Transport is chosen by REDO_MCP_TRANSPORT (stdio default | streamable-http).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from mcp.server.fastmcp import FastMCP
from _common import load_env

HOST = os.environ.get("REDO_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("REDO_MCP_PORT", "8078"))
TRANSPORT = os.environ.get("REDO_MCP_TRANSPORT", "stdio")
UA = "ButtonsBebe-Hermes/1.0"

_env = load_env()
STORE = _env.get("REDO_STORE_ID", "")
KEY = _env.get("REDO_API_KEY", "")
BASE = f"https://api.getredo.com/v2.2/stores/{STORE}"

mcp = FastMCP("buttonsbebe-redo", host=HOST, port=PORT)


def _get(path: str, params: dict | None = None):
    if not (STORE and KEY):
        return {"error": "Redo not configured (REDO_STORE_ID / REDO_API_KEY missing)."}
    try:
        r = requests.get(
            BASE + path, params=params or {},
            headers={"Authorization": f"Bearer {KEY}", "User-Agent": UA}, timeout=20,
        )
        if not r.ok:
            return {"error": f"Redo API {r.status_code}", "detail": r.text[:200]}
        return r.json()
    except Exception as e:  # never raise into the MCP boundary
        return {"error": "request failed", "detail": repr(e)[:200]}


def _trim(ret):
    """Keep the fields a support agent needs; fall back to the whole object."""
    keep = [
        "id", "status", "state", "order_name", "shopify_order_name", "order_number",
        "created_at", "updated_at", "compensation_method", "refund_amount",
        "store_credit_amount", "total", "items", "products", "line_items",
        "tracking", "tracking_url", "tracking_number",
    ]
    if isinstance(ret, dict):
        out = {k: ret[k] for k in keep if k in ret}
        return out or ret
    return ret


@mcp.tool()
def list_recent_returns(limit: int = 10) -> dict:
    """List the most recent returns/RMAs from Redo (read-only). Use this to see
    recent return activity across the store."""
    d = _get("/returns", {"limit": limit})
    if isinstance(d, dict) and isinstance(d.get("returns"), list):
        return {"count": len(d["returns"]), "returns": [_trim(x) for x in d["returns"][:limit]]}
    return d


@mcp.tool()
def get_returns_for_order(order_name: str) -> dict:
    """Look up returns for a specific Shopify order by its name/number
    (e.g. '#12345' or '12345'). Read-only."""
    d = _get("/returns", {"shopify_order_name": order_name})
    if isinstance(d, dict) and isinstance(d.get("returns"), list):
        return {"order": order_name, "count": len(d["returns"]), "returns": [_trim(x) for x in d["returns"]]}
    return d


@mcp.tool()
def get_return(return_id: str) -> dict:
    """Get one return by its Redo return id (read-only)."""
    return _trim(_get(f"/returns/{return_id}"))


if __name__ == "__main__":
    mcp.run(transport=TRANSPORT)
