"""Recover customer draft jobs when Gorgias webhook delivery is interrupted.

Only the three read-only Gorgias MCP tools below are used. Recovered messages go
through the same durable intake and Hermes queue as signed webhook messages;
there is no provider write or automatic customer reply.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx

from bb_webhook.database import ingest_event
from bb_webhook.db import Database
from bb_webhook.message_times import normalize_timestamp, utc_microseconds, SOURCE_TIME_SQL
from logging_setup import get_logger, log_event

def _load_intake():
    try:
        from intake.message_content import intake_from_message as reader
    except ImportError:
        import sys
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from intake.message_content import intake_from_message as reader
    return reader

intake_from_message = _load_intake()

logger = get_logger(__name__)
MCP_URL = "http://127.0.0.1:8079/mcp"
PAGE_SIZE = 100
MAX_DETAILS = 5
MAX_ACTIVE_JOBS = 5
SCAN_SECONDS = 60
LOOKBACK_DAYS = 90  # The Inbox draft projection has the same window.
RAW_BYTE_CAP = 65_536
_SOURCE_FIELDS = ("body_text", "body_html", "text", "stripped_text", "stripped_html", "excerpt")
_BOUND_FIELDS = (
    "current_text", "display_text", "original_content", "preferred_content",
    "body_text", "body_html", "stripped_text", "stripped_html", "text", "excerpt",
)


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
        if tool not in {"list_inbox_tickets", "get_ticket", "get_ticket_messages"}:
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


def _candidate(ticket: dict, cutoff: int) -> bool:
    if type(ticket.get("id")) is not int or ticket["id"] <= 0:
        return False
    if ticket.get("status") != "open" or ticket.get("spam") or ticket.get("trashed_datetime"):
        return False
    received = utc_microseconds(ticket.get("last_received_message_datetime"))
    return received is not None and received >= cutoff


def _latest_public(messages: list[dict]) -> dict | None:
    public = [m for m in messages if isinstance(m, dict) and m.get("public") is not False
              and m.get("channel") != "internal-note" and utc_microseconds(m.get("created_datetime")) is not None]
    return max(public, key=lambda m: (utc_microseconds(m.get("created_datetime")), str(m.get("id") or "")),
               default=None)


def _detail_page_is_missing(message: dict | None) -> bool:
    return message is None


def _detail_page_matches_ticket(message: dict, ticket_id: int) -> bool:
    return message.get("ticket_id") == ticket_id


def _detail_page_lags_summary(message: dict, ticket: dict) -> bool:
    return (
        utc_microseconds(message.get("created_datetime"))
        < utc_microseconds(ticket["last_received_message_datetime"])
    )


def _event(ticket: dict, message: dict, tenant: str) -> tuple[dict, str] | None:
    """Build a bounded canonical intake from observed provider data."""
    if (message.get("from_agent") is not False
            or any(type(record.get("id")) is not int or not 0 < record["id"] < 2**63
                   for record in (ticket, message))):
        return None
    normalized = intake_from_message(message)
    body = normalized["current_text"].strip()[:20_000]
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
    created_at = normalize_timestamp(message.get("created_datetime"))
    if created_at is None:
        return None
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
    raw_message = {"id": message["id"], "from_agent": False, "created_datetime": created_at}
    for key in _SOURCE_FIELDS:
        value = message.get(key)
        if isinstance(value, str):
            raw_message[key] = value
    preferred = message.get("preferred_content")
    preferred_field = message.get("preferred_content_field")
    if isinstance(preferred, str) or (preferred is None and "preferred_content" in message):
        raw_message["preferred_content"] = preferred
    if isinstance(preferred_field, str) or (preferred_field is None and "preferred_content_field" in message):
        raw_message["preferred_content_field"] = preferred_field
    raw_message.update(normalized)
    raw = {"source": "gorgias_reconciliation", "trigger": "ticket-message-created",
           "ticket": {"id": ticket["id"], "subject": event["ticket_subject"],
                      "customer": {"email": customer.get("email"), "name": customer.get("name"),
                                   "id": customer.get("id")}},
           "message": raw_message}
    return event, _bound_raw(raw)


def _json_body_len(value: str) -> int:
    return len(json.dumps(value, ensure_ascii=True)) - 2


def _prefix_for_budget(value: str, budget: int) -> str:
    if budget <= 0 or not value:
        return ""
    if _json_body_len(value) <= budget:
        return value
    lo, hi, best = 0, len(value), ""
    while lo <= hi:
        mid = (lo + hi) // 2
        prefix = value[:mid]
        if _json_body_len(prefix) <= budget:
            best = prefix
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _allocate_text(originals: dict[str, str], budget: int) -> dict[str, str]:
    """Equal encoded-byte prefixes. Small fields keep their unused share."""
    assigned = {key: "" for key in originals}
    pending = [key for key in _BOUND_FIELDS if key in originals]
    leftover = budget
    while pending and leftover > 0:
        share = leftover // len(pending)
        if share <= 0:
            for key in pending:
                if leftover <= 0:
                    break
                assigned[key] = _prefix_for_budget(originals[key], leftover)
                leftover -= _json_body_len(assigned[key])
            break
        still = []
        for key in pending:
            size = _json_body_len(originals[key])
            if size <= share:
                assigned[key] = originals[key]
                leftover -= size
            else:
                still.append(key)
        if len(still) != len(pending):
            pending = still
            continue
        for key in pending:
            assigned[key] = _prefix_for_budget(originals[key], share)
            leftover -= _json_body_len(assigned[key])
        for key in pending:
            if leftover <= 0 or assigned[key] == originals[key]:
                continue
            room = _json_body_len(assigned[key]) + leftover
            longer = _prefix_for_budget(originals[key], room)
            leftover -= _json_body_len(longer) - _json_body_len(assigned[key])
            assigned[key] = longer
        break
    return assigned


def _bound_raw(raw: dict) -> str:
    """Fit source, contract, and preferred copies into the stored payload cap.

    The provider message is not modified. Prefixes are measured after
    ensure_ascii escaping, which is six bytes for non-ASCII and more for emoji.
    """
    message = raw["message"]
    truncated = bool(message.get("source_truncated"))
    # Provider metadata shares the same storage envelope as message evidence.
    # Bound it first so a long display name cannot consume the body budget.
    customer = raw["ticket"]["customer"]
    for record, key, limit in (
        (raw["ticket"], "subject", 500), (customer, "name", 200),
        (customer, "email", 254), (message, "created_datetime", 80),
    ):
        value = record.get(key)
        bounded = value[:limit] if isinstance(value, str) else None
        if bounded != value:
            truncated = True
        record[key] = bounded
    identity = customer.get("id")
    if isinstance(identity, str):
        customer["id"] = identity[:128]
        truncated |= len(identity) > 128
    elif type(identity) is not int or abs(identity) > 2**63 - 1:
        customer["id"] = None
        truncated |= identity is not None
    for key in ("display_source", "current_source", "original_field", "preferred_content_field"):
        if message.get(key) is not None and message.get(key) not in _SOURCE_FIELDS:
            message[key] = None
            truncated = True
    message["source_truncated"] = truncated
    originals: dict[str, str] = {}
    for key in _BOUND_FIELDS:
        value = message.get(key)
        if isinstance(value, str) and value:
            originals[key] = value
            message[key] = ""
    budget = RAW_BYTE_CAP - len(json.dumps(raw, ensure_ascii=True))
    assigned = _allocate_text(originals, max(0, budget))
    for key, value in originals.items():
        message[key] = assigned.get(key, "")
        if message[key] != value:
            truncated = True
    if truncated:
        message["source_truncated"] = True
    text = json.dumps(raw, ensure_ascii=True)
    while len(text) > RAW_BYTE_CAP:
        key = next((item for item in reversed(_BOUND_FIELDS)
                    if isinstance(message.get(item), str) and message[item]), None)
        if key is None:
            break
        current = message[key]
        overflow = len(text) - RAW_BYTE_CAP
        message[key] = _prefix_for_budget(current, _json_body_len(current) - overflow)
        if message[key] == current:
            message[key] = current[:-1]
        message["source_truncated"] = True
        text = json.dumps(raw, ensure_ascii=True)
    if len(text) > RAW_BYTE_CAP:
        raise ValueError("Retained message envelope exceeds the storage budget")
    return text


async def _mark_seen(db: Database, ticket: dict, outcome: str) -> None:
    await db.execute(
        """INSERT INTO gorgias_reconcile_seen (ticket_id,updated_at,checked_at,outcome)
           VALUES (?,?,?,?) ON CONFLICT(ticket_id) DO UPDATE SET
           updated_at=excluded.updated_at,checked_at=excluded.checked_at,outcome=excluded.outcome""",
        (ticket["id"], ticket.get("updated_datetime") or "",
         datetime.now(timezone.utc).isoformat(), outcome), operation="gorgias_reconcile_seen")


@dataclass
class PageBatch:
    remaining: deque[dict]
    next_cursor: str | None


@dataclass
class SweepPosition:
    cursor: str | None = None
    batch: PageBatch | None = None

    def advance(self):
        self.cursor = self.batch.next_cursor
        self.batch = None


async def reconcile_page(db_path: Path, tenant: str, position: SweepPosition,
                         *, client_factory=ReadOnlyMCP, max_details: int = MAX_DETAILS,
                         max_active_jobs: int = MAX_ACTIVE_JOBS,
                         now: float | None = None) -> tuple[str | None, int]:
    db = Database(db_path)
    active_rows = await db.fetch(
        "SELECT COUNT(*) AS n FROM job_queue WHERE is_customer_message=1 AND status IN ('pending','processing')",
        operation="gorgias_reconcile_capacity")
    slots = max(0, max_active_jobs - int(active_rows[0]["n"]))
    if not slots:
        return position.cursor, 0
    cutoff = int((now if now is not None else time.time()) * 1_000_000) - LOOKBACK_DAYS * 86400 * 1_000_000
    deferred = position.batch is not None
    async with client_factory() as client:
        if position.batch is None:
            page = await client.call("list_inbox_tickets", {"limit": PAGE_SIZE,
                                  **({"cursor": position.cursor} if position.cursor else {})})
            tickets = page.get("data")
            if not isinstance(tickets, list):
                raise ValueError("Gorgias ticket page unavailable")
            next_cursor = (page.get("meta") or {}).get("next_cursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise ValueError("Gorgias ticket cursor unavailable")
            if next_cursor == position.cursor:
                next_cursor = None
            candidates = deque(dict(t) for t in tickets[:PAGE_SIZE]
                               if isinstance(t, dict) and _candidate(t, cutoff))
            position.batch = PageBatch(candidates, next_cursor)
        if not position.batch.remaining:
            position.advance()
            return position.cursor, 0
        ids = tuple(t["id"] for t in position.batch.remaining)
        placeholders = ",".join("?" for _ in ids)
        seen_rows = await db.fetch(
            f"SELECT ticket_id,updated_at FROM gorgias_reconcile_seen WHERE ticket_id IN ({placeholders})",
            ids, operation="gorgias_reconcile_seen_read")
        seen = {row["ticket_id"]: row["updated_at"] for row in seen_rows}
        parsed_rows = await db.fetch(
            f"SELECT ticket_id,MAX({SOURCE_TIME_SQL}) AS newest, "
            f"MAX({SOURCE_TIME_SQL} IS NULL OR utc_microseconds(received_at) IS NULL) AS chronology_invalid FROM parsed_messages "
            f"WHERE is_customer_message=1 AND ticket_id IN ({placeholders}) GROUP BY ticket_id",
            ids, operation="gorgias_reconcile_parsed_read")
        parsed = {row["ticket_id"]: row["newest"] if not row["chronology_invalid"] else None for row in parsed_rows}
        enqueued = 0
        checked = 0
        detail_reads = 0
        detail_limit = min(MAX_DETAILS, max_details)
        while position.batch.remaining and checked < detail_limit and detail_reads < 2 * detail_limit and slots > 0:
            ticket = position.batch.remaining.popleft()
            if not _candidate(ticket, cutoff):
                continue
            if deferred:
                checked += 1
                detail_reads += 1
                try:
                    current = await client.call("get_ticket", {"ticket_id": ticket["id"]})
                    if not isinstance(current, dict) or current.get("id") != ticket["id"]:
                        raise ValueError("Gorgias ticket state unavailable")
                except Exception as exc:
                    log_event(logger, "WARNING", "Gorgias deferred ticket state unavailable",
                              ticket_id=ticket["id"], error=type(exc).__name__)
                    continue
                if not _candidate(current, cutoff):
                    continue
                ticket = current
            if (seen.get(ticket["id"]) == (ticket.get("updated_datetime") or "")
                    or (parsed.get(ticket["id"]) is not None
                        and parsed[ticket["id"]] >= utc_microseconds(ticket["last_received_message_datetime"]))):
                continue
            if not deferred:
                checked += 1
            detail_reads += 1
            try:
                detail = await client.call("get_ticket_messages", {"ticket_id": ticket["id"], "limit": 50})
                messages = detail.get("data")
                if not isinstance(messages, list):
                    raise ValueError("Gorgias messages unavailable")
            except Exception as exc:
                log_event(logger, "WARNING", "Gorgias draft recovery read failed",
                          ticket_id=ticket["id"], error=type(exc).__name__)
                continue
            latest = _latest_public(messages)
            if _detail_page_is_missing(latest):
                continue
            if not _detail_page_matches_ticket(latest, ticket["id"]):
                continue
            if latest.get("from_agent") is True:
                await _mark_seen(db, ticket, "agent_replied")
                continue
            if latest.get("from_agent") is not False:
                continue
            if _detail_page_lags_summary(latest, ticket):
                continue
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
        if not position.batch.remaining:
            position.advance()
        return position.cursor, enqueued


async def reconcile_loop(db_path: Path, tenant: str, *, client_factory=ReadOnlyMCP) -> None:
    """Keep webhook-free reads as a bounded, idempotent safety net."""
    position = SweepPosition()
    sweep = 0
    while True:
        refresh_head = sweep > 0 and sweep % 5 == 0
        try:
            turn = SweepPosition() if refresh_head else position
            _, enqueued = await reconcile_page(db_path, tenant, turn, client_factory=client_factory)
            if enqueued:
                log_event(logger, "INFO", "Gorgias draft recovery queued jobs", count=enqueued)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not refresh_head:
                position = SweepPosition()
            log_event(logger, "WARNING", "Gorgias draft recovery unavailable", error=type(exc).__name__)
        sweep += 1
        await asyncio.sleep(SCAN_SECONDS)
