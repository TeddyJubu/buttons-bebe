"""Recover customer draft jobs when Gorgias webhook delivery is interrupted.

Only the two read-only Gorgias MCP tools below are used. Recovered messages go
through the same durable intake and Hermes queue as signed webhook messages;
there is no provider write or automatic customer reply.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from typing import Any

import httpx

from bb_webhook.database import ingest_event
from bb_webhook.db import Database
from bb_webhook.message_content import message_text
from logging_setup import get_logger, log_event

logger = get_logger(__name__)
MCP_URL = "http://127.0.0.1:8079/mcp"
PAGE_SIZE = 100
MAX_DETAILS = 5
MAX_ACTIVE_JOBS = 5
SCAN_SECONDS = 60
LOOKBACK_DAYS = 90  # The Inbox draft projection has the same window.


def epoch(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0


class ReadOnlyMCP:
    """One short-lived, loopback-only MCP session with bounded responses."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=25, trust_env=False, follow_redirects=False)
        self.session: str | None = None
        self.sequence = 0

    async def __aenter__(self):
        try:
            await self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                          "clientInfo": {"name": "draft-reconciler", "version": "1"}})
            await self.rpc("notifications/initialized", {}, notification=True)
            return self
        except BaseException:
            await self.client.aclose()
            raise

    async def __aexit__(self, *_):
        if self.session:
            try:
                await self.client.delete(MCP_URL, headers={"Mcp-Session-Id": self.session}, timeout=3)
            except (httpx.HTTPError, RuntimeError):
                pass
        await self.client.aclose()

    async def rpc(self, method: str, params: dict, *, notification: bool = False) -> dict:
        self.sequence += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = self.sequence
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2024-11-05"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        async with self.client.stream("POST", MCP_URL, json=payload, headers=headers) as response:
            response.raise_for_status()
            self.session = response.headers.get("Mcp-Session-Id", self.session)
            if notification:
                return {}
            if "text/event-stream" in response.headers.get("Content-Type", ""):
                chunks: list[str] = []
                size = 0
                result = None
                async for line in response.aiter_lines():
                    size += len(line)
                    if size > 8_000_000:
                        raise ValueError("MCP response too large")
                    if line.startswith("data:"):
                        chunks.append(line[5:].strip())
                    elif not line and chunks:
                        event = json.loads("\n".join(chunks))
                        chunks.clear()
                        if event.get("id") == self.sequence:
                            result = event
                            break
                if result is None:
                    raise ValueError("MCP result missing")
            else:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 8_000_000:
                        raise ValueError("MCP response too large")
                result = json.loads(body)
        if not isinstance(result, dict) or result.get("error"):
            raise ValueError("MCP request failed")
        return result.get("result") or {}

    async def call(self, tool: str, arguments: dict) -> dict:
        if tool not in {"list_inbox_tickets", "get_ticket_messages"}:
            raise ValueError("Non-read Gorgias tool refused")
        result = await self.rpc("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            raise ValueError("Gorgias read unavailable")
        data = result.get("structuredContent")
        if not isinstance(data, dict):
            data = next((json.loads(c["text"]) for c in result.get("content", [])
                         if c.get("type") == "text"), None)
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError("Gorgias read unavailable")
        return data


def _candidate(ticket: dict, cutoff: float) -> bool:
    if type(ticket.get("id")) is not int or ticket["id"] <= 0:
        return False
    if ticket.get("status") != "open" or ticket.get("spam") or ticket.get("trashed_datetime"):
        return False
    return epoch(ticket.get("last_received_message_datetime")) >= cutoff


def _latest_public(messages: list[dict]) -> dict | None:
    public = [m for m in messages if isinstance(m, dict) and m.get("public") is not False
              and m.get("channel") != "internal-note" and epoch(m.get("created_datetime"))]
    return max(public, key=lambda m: (epoch(m.get("created_datetime")), str(m.get("id") or "")),
               default=None)


def _event(ticket: dict, message: dict, tenant: str) -> tuple[dict, str] | None:
    """Build a bounded canonical intake from observed provider data."""
    if message.get("from_agent") is not False or type(message.get("id")) is not int:
        return None
    body = message_text(message).strip()[:20_000]
    if not body:
        return None
    customer = ticket.get("customer") if isinstance(ticket.get("customer"), dict) else {}
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    author_email = sender.get("email") if isinstance(sender.get("email"), str) else None
    user = ticket.get("assignee_user") if isinstance(ticket.get("assignee_user"), dict) else {}
    team = ticket.get("assignee_team") if isinstance(ticket.get("assignee_team"), dict) else {}
    raw_tags = ticket.get("tags") if isinstance(ticket.get("tags"), list) else []
    tags = [x.get("name", "") if isinstance(x, dict) else str(x) for x in raw_tags]
    intents = [{"name": str(x.get("name"))[:80]} for x in message.get("intents") or []
               if isinstance(x, dict) and x.get("name")][:10]
    created_at = message["created_datetime"]
    event = {"tenant_id": tenant, "ticket_id": ticket["id"], "message_id": message["id"],
             "event_type": "ticket-message-created", "author_type": "customer",
             "author_email": author_email, "channel": message.get("channel") or ticket.get("channel"),
             "created_at": created_at, "message_text": body,
             "ticket_subject": str(ticket.get("subject") or "")[:500],
             "ticket_status": ticket.get("status"),
             "ticket_assignee": user.get("email") or user.get("name") or team.get("name"),
             "ticket_tags": tags[:12], "ticket_priority": ticket.get("priority"),
             "ticket_spam": int(bool(ticket.get("spam"))),
             "ticket_trashed": int(bool(ticket.get("trashed_datetime"))),
             "ticket_snoozed": int(bool(ticket.get("snooze_datetime"))),
             "customer_email": customer.get("email"), "intents": intents,
             "is_customer_message": True}
    source_field = message.get("preferred_content_field")
    if source_field not in {"stripped_text", "stripped_html", "body_text", "body_html"}:
        source_field = "body_text"
    original = message.get("preferred_content") or message.get(source_field) or ""
    raw = {"source": "gorgias_reconciliation", "trigger": "ticket-message-created",
           "ticket": {"id": ticket["id"], "subject": event["ticket_subject"],
                      "customer": {"email": customer.get("email"), "name": customer.get("name"),
                                   "id": customer.get("id")}},
           "message": {"id": message["id"], "from_agent": False, "created_datetime": created_at,
                       source_field: str(original)[:12_000]}}
    raw_text = json.dumps(raw, ensure_ascii=True)
    if len(raw_text.encode()) > 65_536:
        raw["message"][source_field] = str(original)[:6_000]
        raw_text = json.dumps(raw, ensure_ascii=True)
    return event, raw_text


async def _mark_seen(db: Database, ticket: dict, outcome: str) -> None:
    await db.execute(
        """INSERT INTO gorgias_reconcile_seen (ticket_id,updated_at,checked_at,outcome)
           VALUES (?,?,?,?) ON CONFLICT(ticket_id) DO UPDATE SET
           updated_at=excluded.updated_at,checked_at=excluded.checked_at,outcome=excluded.outcome""",
        (ticket["id"], ticket.get("updated_datetime") or "",
         datetime.now(timezone.utc).isoformat(), outcome), operation="gorgias_reconcile_seen")


async def reconcile_page(db_path: Path, tenant: str, cursor: str | None = None,
                         *, client_factory=ReadOnlyMCP, max_details: int = MAX_DETAILS,
                         max_active_jobs: int = MAX_ACTIVE_JOBS,
                         now: float | None = None) -> tuple[str | None, int]:
    """Inspect one Gorgias page; return next cursor and count of new jobs."""
    db = Database(db_path)
    active_rows = await db.fetch(
        "SELECT COUNT(*) AS n FROM job_queue WHERE is_customer_message=1 AND status IN ('pending','processing')",
        operation="gorgias_reconcile_capacity")
    slots = max(0, max_active_jobs - int(active_rows[0]["n"]))
    if not slots:
        return cursor, 0
    cutoff = (now or time.time()) - timedelta(days=LOOKBACK_DAYS).total_seconds()
    async with client_factory() as client:
        page = await client.call("list_inbox_tickets", {"limit": PAGE_SIZE,
                              **({"cursor": cursor} if cursor else {})})
        tickets = page.get("data")
        if not isinstance(tickets, list):
            raise ValueError("Gorgias ticket page unavailable")
        next_cursor = (page.get("meta") or {}).get("next_cursor")
        candidates = [t for t in tickets if isinstance(t, dict) and _candidate(t, cutoff)]
        if not candidates:
            return next_cursor if next_cursor != cursor else None, 0
        ids = tuple(t["id"] for t in candidates)
        placeholders = ",".join("?" for _ in ids)
        seen_rows = await db.fetch(
            f"SELECT ticket_id,updated_at FROM gorgias_reconcile_seen WHERE ticket_id IN ({placeholders})",
            ids, operation="gorgias_reconcile_seen_read")
        seen = {row["ticket_id"]: row["updated_at"] for row in seen_rows}
        parsed_rows = await db.fetch(
            f"SELECT ticket_id,MAX(created_at) AS newest FROM parsed_messages "
            f"WHERE is_customer_message=1 AND ticket_id IN ({placeholders}) GROUP BY ticket_id",
            ids, operation="gorgias_reconcile_parsed_read")
        parsed = {row["ticket_id"]: epoch(row["newest"]) for row in parsed_rows}
        unchecked = [t for t in candidates
                     if seen.get(t["id"]) != (t.get("updated_datetime") or "")
                     and parsed.get(t["id"], 0) < epoch(t["last_received_message_datetime"])]
        if not unchecked:
            return next_cursor if next_cursor != cursor else None, 0
        enqueued = 0
        checked = 0
        for ticket in unchecked:
            if checked >= max_details or slots <= 0:
                break
            checked += 1
            try:
                detail = await client.call("get_ticket_messages", {"ticket_id": ticket["id"], "limit": 50})
                messages = detail.get("data")
                if not isinstance(messages, list):
                    raise ValueError("Gorgias messages unavailable")
                latest = _latest_public(messages)
                if latest is None:
                    continue  # Incomplete page: retry after Gorgias catches up.
                if latest.get("ticket_id") != ticket["id"]:
                    continue  # Never attribute another ticket's message to this customer.
                if latest.get("from_agent") is True:
                    await _mark_seen(db, ticket, "agent_replied")
                    continue
                if latest.get("from_agent") is not False:
                    continue
                if epoch(latest.get("created_datetime")) < epoch(ticket["last_received_message_datetime"]):
                    continue  # The ticket summary is ahead of the message page.
                normalized = _event(ticket, latest, tenant)
                if normalized is None:
                    await _mark_seen(db, ticket, "content_unavailable")
                    continue
                event, raw = normalized
                job_id = await ingest_event(event, raw, db_path)
                await _mark_seen(db, ticket, "queued" if job_id else "duplicate")
                if job_id is not None:
                    enqueued += 1
                    slots -= 1
                    log_event(logger, "INFO", "Recovered customer message for Hermes",
                              ticket_id=ticket["id"], message_id=latest["id"], job_id=job_id)
            except (httpx.HTTPError, ValueError) as exc:
                log_event(logger, "WARNING", "Gorgias draft recovery read failed",
                          ticket_id=ticket["id"], error=type(exc).__name__)
        has_more_on_page = len(unchecked) > checked
        return (cursor if has_more_on_page else (next_cursor if next_cursor != cursor else None)), enqueued


async def reconcile_loop(db_path: Path, tenant: str) -> None:
    """Keep webhook-free reads as a bounded, idempotent safety net."""
    cursor = None
    sweep = 0
    while True:
        try:
            # Check the newest tickets regularly while older pages are scanned.
            if sweep % 5 == 0:
                cursor = None
            cursor, enqueued = await reconcile_page(db_path, tenant, cursor)
            if enqueued:
                log_event(logger, "INFO", "Gorgias draft recovery queued jobs", count=enqueued)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            cursor = None
            log_event(logger, "WARNING", "Gorgias draft recovery unavailable",
                      error=type(exc).__name__)
        sweep += 1
        await asyncio.sleep(SCAN_SECONDS)
