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

The CLI and MCP entrypoints share the search implementation. Its indexed
folders and category weights come from `kb_lib`, so the MCP wrapper does not
maintain a second category taxonomy or scorer.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from search_kb import SearchOutcome, search
from kb_lib import CATEGORY_WEIGHT, CONTENT_FOLDERS, _get_model

HOST = os.environ.get("KB_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("KB_MCP_PORT", "8077"))
TRANSPORT = os.environ.get("KB_MCP_TRANSPORT", "stdio")

mcp = FastMCP("buttonsbebe-kb", host=HOST, port=PORT)


def _validate_category_configuration() -> None:
    """Fail closed if the indexed corpus and ranking map drift apart."""
    missing = sorted(set(CONTENT_FOLDERS) - set(CATEGORY_WEIGHT))
    if missing:
        raise RuntimeError(
            "missing category weights for indexed folders: " + ", ".join(missing)
        )


_validate_category_configuration()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def search_kb(query: str, k: int = 5) -> SearchOutcome:
    """Search the shared, weighted Buttons Bebe knowledge base.

    Returns status, independent notice_board and index health, and results.
    Healthy empty results mean no match. Unavailable notices may hide active
    overrides. Use passage source and tags to distinguish current policy from
    learned examples. Retrieval gaps alone do not create urgency. Sensitive
    requests still require elevated human review. Never send a reply."""
    return search(query, k=k)


if __name__ == "__main__":
    # For the always-on service, load the search model BEFORE serving so the
    # first request is fast and the tool is ready the moment Hermes connects.
    # In stdio mode we skip this so the initial handshake stays quick.
    if TRANSPORT != "stdio":
        _get_model()
    mcp.run(transport=TRANSPORT)
