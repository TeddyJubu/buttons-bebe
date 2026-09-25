"""Three local QA MCP contracts; no customer-system network clients imported."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import StrictInt
from qa_safety import GROUPS, audit, filter_policy_results, validate_fixture


def create_server(group: str, port: int, fixture_path: Path, audit_path: Path, allowlist: Path, kb_mode: str):
    if group not in GROUPS or not 1024 <= port <= 65535:
        raise ValueError("Invalid QA endpoint")
    server = FastMCP(group, host="127.0.0.1", port=port, log_level="ERROR", stateless_http=True, json_response=True)

    def state(tool):
        if fixture_path.is_symlink() or fixture_path.stat().st_size > 100000:
            raise ValueError("Invalid bounded fixture file")
        value = validate_fixture(json.loads(fixture_path.read_text()))
        audit(audit_path, group, tool, scenario_id=value["scenario_id"])
        return value

    def unknown():
        return {"error": "qa_fixture_not_found", "qa_fixture": True}

    if group == "buttonsbebe_gorgias":
        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def list_recent_tickets(limit: StrictInt = 10) -> dict:
            value = state("list_recent_tickets")
            return {"count":1,"tickets":[value["ticket"]],"qa_fixture":True}

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def list_inbox_tickets(limit: StrictInt = 100, cursor: str | None = None) -> dict:
            value = state("list_inbox_tickets")
            if not 1 <= limit < 2**63:
                raise ValueError("QA requires positive bounded limits")
            if cursor is not None:
                if not cursor or len(cursor) > 2048:
                    raise ValueError("Invalid bounded QA cursor")
                return unknown()  # Fixtures contain exactly one page.
            return {"data": [value["ticket"]], "meta": {"next_cursor": None}, "qa_fixture": True}

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def get_ticket(ticket_id: StrictInt) -> dict:
            value = state("get_ticket")
            return value["ticket"] if ticket_id == value["ticket"]["id"] else unknown()

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def get_ticket_messages(ticket_id: StrictInt, limit: StrictInt = 30, cursor: str | None = None) -> dict:
            value = state("get_ticket_messages")
            if ticket_id <= 0 or not 1 <= limit < 2**63:
                raise ValueError("QA requires positive bounded identifiers and limits")
            if cursor is not None:
                if not cursor or len(cursor) > 2048:
                    raise ValueError("Invalid bounded QA cursor")
                return unknown()  # Fixture has exactly one page; never reach other tickets.
            return {"data": value["messages"][:min(limit,50)], "meta":{"next_cursor":None}, "qa_fixture":True} if ticket_id == value["ticket"]["id"] else unknown()

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def get_customer(customer_id: StrictInt) -> dict:
            value = state("get_customer")
            return value["customer"] if customer_id == value["customer"]["id"] else unknown()

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def search_customer(email: str) -> dict:
            value = state("search_customer")
            return {"data":[value["customer"]], "qa_fixture":True} if email == value["customer"]["email"] else unknown()

    elif group == "buttonsbebe_redo":
        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def list_recent_returns(limit: int = 10) -> dict:
            state("list_recent_returns")
            return {"returns":[],"qa_fixture":True}

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def get_returns_for_order(order_name: str) -> dict:
            value = state("get_returns_for_order")
            return {"returns":[],"qa_fixture":True} if any(o["name"] == order_name.lstrip("#") for o in value["orders"]) else unknown()

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def get_return(return_id: str) -> dict:
            state("get_return")
            return unknown()

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        def get_order(order_name: str) -> dict:
            value = state("get_order")
            return next((o for o in value["orders"] if o["name"] == order_name.lstrip("#") or o["id"] == order_name), unknown())

    else:
        @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
        async def search_kb(query: str, k: int = 5) -> list[dict]:
            value = state("search_kb")
            if not 1 <= k <= 25 or not query.strip() or len(query) > 1000:
                raise ValueError("Invalid bounded QA search")
            if kb_mode == "fixture":
                return [{"file":"policies/qa-fixture.md","category":"policies","status":"confirmed","text":"QA fixture: human review is required. Never claim a refund or order change was completed. Ask for missing information.","qa_fixture":True}]
            try:
                # This fixed read-only endpoint is the only optional real-service
                # connection anywhere in the QA MCP process.
                async with asyncio.timeout(30):
                    async with streamablehttp_client("http://127.0.0.1:8077/mcp", timeout=10, sse_read_timeout=25) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            result = await session.call_tool("search_kb", {"query":query,"k":25})
                if result.isError:
                    raise ValueError("KB search failed")
                structured = result.structuredContent
                if isinstance(structured, dict) and isinstance(structured.get("result"), list):
                    rows = structured["result"]
                else:
                    blocks = [block.text for block in result.content if getattr(block,"type",None) == "text"]
                    if len(blocks) != 1 or len(blocks[0]) > 2_000_000:
                        raise ValueError("Unexpected KB response")
                    rows = json.loads(blocks[0])
                safe, filtered = filter_policy_results(rows, set(json.loads(allowlist.read_text())))
                audit(audit_path, group, "kb_projection", scenario_id=value["scenario_id"], filtered=filtered,
                      returned=min(len(safe),k), files=[row["file"] for row in safe[:k]])
                return safe[:k]
            except Exception:
                audit(audit_path, group, "kb_projection", scenario_id=value["scenario_id"], fatal=True)
                raise ValueError("QA policy projection refused an invalid response") from None
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=GROUPS, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--kb-mode", choices=("fixture","policies-only"), required=True)
    args = parser.parse_args()
    create_server(args.group,args.port,args.fixture,args.audit,args.allowlist,args.kb_mode).run(transport="streamable-http")


if __name__ == "__main__":
    main()
