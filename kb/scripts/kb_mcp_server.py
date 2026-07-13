"""kb_mcp_server.py -- exposes the KB to Hermes as the read-only `search_kb` tool.

It can run two ways (chosen by the KB_MCP_TRANSPORT environment variable):

  - "streamable-http"  -> an always-on background service (recommended).
    Runs as a systemd service, keeps the search model loaded in memory, and
    listens on localhost. Hermes connects to it by URL, so the tool is ready
    instantly every session. This is how it runs in production.

  - "stdio" (default)  -> Hermes spawns this script per session.
    Simpler, but has a cold start each time, so the tool can occasionally be
    slow to appear. Kept as a fallback.

Environment variables:
  KB_MCP_TRANSPORT   stdio (default) | streamable-http | sse
  KB_MCP_HOST        default 127.0.0.1   (localhost only -- not exposed)
  KB_MCP_PORT        default 8077

The server offers exactly ONE tool -- search_kb -- and nothing else.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from search_kb import search
from kb_lib import _get_model

HOST = os.environ.get("KB_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("KB_MCP_PORT", "8077"))
TRANSPORT = os.environ.get("KB_MCP_TRANSPORT", "stdio")

mcp = FastMCP("buttonsbebe-kb", host=HOST, port=PORT)


@mcp.tool()
def search_kb(query: str, k: int = 5) -> list[dict]:
    """Search the Buttons Bebe knowledge base (policies, macros, solved tickets).
    Returns the top matching passages, each with a relevance score and a risk
    label ("sensitive": true means return a safely prefixed draft for elevated
    human review; never send it and never suppress the draft)."""
    return search(query, k=k)


if __name__ == "__main__":
    # For the always-on service, load the search model BEFORE serving so the
    # first request is fast and the tool is ready the moment Hermes connects.
    # In stdio mode we skip this so the initial handshake stays quick.
    if TRANSPORT != "stdio":
        _get_model()
    mcp.run(transport=TRANSPORT)
