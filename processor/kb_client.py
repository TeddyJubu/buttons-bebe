"""KB search client — calls the KB MCP server to search the knowledge base.

The KB MCP server runs as a systemd service on localhost:8077.
This client calls it via HTTP to search for policies, FAQs, and
exemplar tickets relevant to a customer's message.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from config import get_settings
from logging_setup import get_logger, log_event

logger = get_logger(__name__)

_TIMEOUT = 10.0


def search_kb(query: str, k: int = 5) -> list[dict[str, Any]]:
    """Search the Buttons Bebe knowledge base.

    Calls the KB MCP server's search_kb tool via the MCP HTTP transport.

    Args:
        query: The customer's question or keywords
        k: Number of results to return (default 5)

    Returns:
        List of KB passages, each with:
        - text: The passage content
        - score: Relevance score
        - file: Source file path
        - title: Section title
        - category: policies | faq | tickets | intents
        - sensitive: bool — if True, draft a SENSITIVE reply (tagged, safe language)
        - heading: Section heading
    """
    settings = get_settings()
    url = settings.kb_mcp_url

    # MCP tool call via streamable-http transport
    # The MCP server accepts JSON-RPC style requests
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "search_kb",
            "arguments": {"query": query, "k": k},
        },
        "id": 1,
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, json=payload,
                               headers={"Content-Type": "application/json"})

            if resp.status_code != 200:
                log_event(logger, "WARNING", "KB search returned non-200",
                          status=resp.status_code,
                          query=query[:100])
                return []

            data = resp.json()

            # MCP response format: {"result": {"content": [{"type": "text", "text": "..."}]}}
            result = data.get("result", {})
            content = result.get("content", [])

            if not content:
                # Try direct result format (some MCP implementations)
                if isinstance(result, list):
                    return result
                return []

            # Extract text from content items
            for item in content:
                if item.get("type") == "text":
                    try:
                        parsed = json.loads(item["text"])
                        if isinstance(parsed, list):
                            return parsed
                        if isinstance(parsed, dict) and "result" in parsed:
                            return json.loads(parsed["result"]) if isinstance(parsed["result"], str) else parsed["result"]
                    except (json.JSONDecodeError, KeyError):
                        # Return raw text if not JSON
                        return [{"text": item["text"], "score": 0, "sensitive": False}]

            return []

    except httpx.RequestError as exc:
        log_event(logger, "ERROR", f"KB search request failed: {exc}",
                  query=query[:100])
        return []
    except Exception as exc:
        log_event(logger, "ERROR", f"KB search unexpected error: {exc}",
                  query=query[:100])
        return []