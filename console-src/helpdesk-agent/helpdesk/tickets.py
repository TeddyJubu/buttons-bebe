"""First-party ticket tissue. Not Gorgias. Sample threads plus intake.

Ticket status is ours: open / closed / snoozed.
Never Return.status OPEN and never Customer.displayName.
Spam never becomes a ticket and never appears in list_tickets.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import sqlite3
from contextlib import contextmanager
from functools import wraps
from . import state_store
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import bad_request, not_found
from .speakers import project_customer_name, project_message
from .fixtures_live_holes import (
    C_FULFILLED,
    C_MULTI,
    C_UNFULFILLED,
    O_1001,
    O_1002,
    O_1003,
)
from .fixtures_demo_tickets import DEMO_SEED_TICKETS
from .fixtures_sample import ADA, CASEY, JORDAN, ORDER_ADA, ORDER_CASEY_A, ORDER_CASEY_B

# Keep in sync with the single JS source: console-src/inbox/js/view-model.js
# exports VIEW_IDS (report 10, action 7). Order differs (UI menu order there).
VIEWS = ("open", "closed", "all", "snoozed", "mine", "unassigned")
TICKET_STATUSES = ("open", "closed", "snoozed")
REQUEST_TYPES = ("marketing_unsubscribe", "privacy_request", "bug")
PRIVACY_SUBTYPES = ("access", "delete", "export")
SEVERITIES = ("low", "medium", "high", "critical")
_BUG_TYPE_RE = re.compile(r"\b(?:bug|crash(?:es|ed|ing)?)\b", re.I)
_BROKEN_RE = re.compile(r"\bbroken\b", re.I)
_TECH_RE = re.compile(r"\b(?:ios|iphone|ipad|android|app|device)\b", re.I)
_IOS_RE = re.compile(r"\b(?:ios|iphone|ipad)\b", re.I)
_ANDROID_RE = re.compile(r"\bandroid\b", re.I)
_CRITICAL_RE = re.compile(r"\bcritical\b", re.I)
_CRASH_RE = re.compile(r"\bcrash(?:es|ed|ing)?\b", re.I)
_LOW_RE = re.compile(r"\b(?:low|minor)\b", re.I)
_PRIVACY_MARKERS = (
    "privacy",
    "gdpr",
    "delete my data",
    "data request",
    "privacy request",
    "delete my personal data",
    "data deletion",
    "data export",
    "export my data",
    "right to be forgotten",
    "erase my data",
)

ALIASES = {
    "1001": "t-ada-track",
    "1002": "t-casey-visor",
    "1003": "t-jordan-ship",
}

SAMPLE_GIDS = {
    "t-ada-track": (ADA, ORDER_ADA),
    "t-casey-visor": (CASEY, ORDER_CASEY_A),
    "t-casey-throw": (CASEY, ORDER_CASEY_B),
    "t-jordan-ship": (JORDAN, None),
    "t-ada-closed": (ADA, ORDER_ADA),
}

LIVE_GIDS = {
    "t-ada-track": (C_UNFULFILLED, O_1001),
    "t-casey-visor": (C_FULFILLED, O_1002),
    "t-casey-throw": (C_MULTI, O_1003),
    "t-jordan-ship": (C_MULTI, None),
    "t-ada-closed": (C_UNFULFILLED, O_1001),
}

STORE_NAME = "Demo Shop"

SEED_TICKETS = (
    {
        "id": "t-ada-track",
        "customerName": "Ada Demo",
        "subject": "Tracking on order #1001 has not moved",
        "snippet": "Where is my order #1001? The tracking has not updated.",
        "status": "open",
        "assignee": "me",
        "updatedAt": "2026-08-28T15:10:00Z",
        "messages": [
            {
                "id": "m1",
                "from": "customer",
                "fromAgent": False,
                "name": "Ada Demo",
                "fromName": "Ada Demo",
                "body": "Where is my order #1001? The tracking has not updated.",
                "at": "2026-08-28T14:02:00Z",
            },
            {
                "id": "m2",
                "from": "agent",
                "fromAgent": True,
                "name": STORE_NAME,
                "fromName": STORE_NAME,
                "body": "Looking at the shipment now — I will write back with the carrier update.",
                "at": "2026-08-28T14:40:00Z",
            },
        ],
        "statusEvents": [
            {"at": "2026-08-28T14:41:00Z", "status": "open", "note": "assigned"},
        ],
    },
    {
        "id": "t-casey-visor",
        "customerName": "Casey Sandbox",
        "subject": "When will order #1002 ship?",
        "snippet": "Please tell me when order #1002 will leave. I need the visor this week.",
        "status": "open",
        "assignee": None,
        "updatedAt": "2026-08-28T16:20:00Z",
        "messages": [
            {
                "id": "m3",
                "from": "customer",
                "fromAgent": False,
                "name": "Casey Sandbox",
                "fromName": "Casey Sandbox",
                "body": "Please tell me when order #1002 will leave. I need the visor this week.",
                "at": "2026-08-28T16:20:00Z",
            }
        ],
        "statusEvents": [],
    },
    {
        "id": "t-casey-throw",
        "customerName": "Casey Sandbox",
        "subject": "Question about the throw on #1003",
        "snippet": "Did the merino throw on #1003 go out? I want to confirm the shipment.",
        "status": "open",
        "assignee": None,
        "updatedAt": "2026-08-27T11:05:00Z",
        "messages": [
            {
                "id": "m4",
                "from": "customer",
                "fromAgent": False,
                "name": "Casey Sandbox",
                "fromName": "Casey Sandbox",
                "body": "Did the merino throw on #1003 go out? I want to confirm the shipment.",
                "at": "2026-08-27T11:05:00Z",
            }
        ],
        "statusEvents": [],
    },
    {
        "id": "t-jordan-ship",
        "customerName": "Jordan Preview",
        "subject": "Do you ship the demo catalog to Canada?",
        "snippet": "Do you ship the demo catalog to Canada, or is it local-only?",
        "status": "snoozed",
        "assignee": None,
        "updatedAt": "2026-08-26T09:00:00Z",
        "messages": [
            {
                "id": "m5",
                "from": "customer",
                "fromAgent": False,
                "name": "Jordan Preview",
                "fromName": "Jordan Preview",
                "body": "Do you ship the demo catalog to Canada, or is it local-only?",
                "at": "2026-08-26T09:00:00Z",
            }
        ],
        "statusEvents": [
            {"at": "2026-08-26T09:05:00Z", "status": "snoozed", "note": "waiting"},
        ],
    },
    {
        "id": "t-ada-closed",
        "customerName": "Ada Demo",
        "subject": "Received the rattle — thank you",
        "snippet": "The rattle from #1001 arrived. Thank you — you can close this.",
        "status": "closed",
        "assignee": "me",
        "updatedAt": "2026-08-25T18:12:00Z",
        "messages": [
            {
                "id": "m6",
                "from": "customer",
                "fromAgent": False,
                "name": "Ada Demo",
                "fromName": "Ada Demo",
                "body": "The rattle from #1001 arrived. Thank you — you can close this.",
                "at": "2026-08-25T17:50:00Z",
            },
            {
                "id": "m7",
                "from": "agent",
                "fromAgent": True,
                "name": STORE_NAME,
                "fromName": STORE_NAME,
                "body": "Glad it reached you, Ada.",
                "at": "2026-08-25T18:10:00Z",
            },
        ],
        "statusEvents": [
            {"at": "2026-08-25T18:12:00Z", "status": "closed", "note": "answered"},
        ],
    },
    {
        "id": "t-priya-unsub",
        "customerName": "Priya Lane",
        "subject": "Please unsubscribe me from marketing emails",
        "snippet": "Please take me off the marketing list. I still want order updates.",
        "status": "open",
        "assignee": "me",
        "updatedAt": "2026-08-28T15:40:00Z",
        "requestType": "marketing_unsubscribe",
        "unsubscribeHandled": False,
        "messages": [
            {
                "id": "m8-unsub",
                "from": "customer",
                "fromAgent": False,
                "name": "Priya Lane",
                "fromName": "Priya Lane",
                "body": "Please take me off the marketing list. I still want order updates.",
                "at": "2026-08-28T15:40:00Z",
            }
        ],
        "statusEvents": [
            {"at": "2026-08-28T15:41:00Z", "status": "open", "note": "created"},
        ],
    },
    {
        "id": "t-lee-privacy",
        "customerName": "Lee Chen",
        "subject": "GDPR request — please delete my data",
        "snippet": "Please delete my stored personal data. I do not need a Shopify account change from this inbox.",
        "status": "open",
        "assignee": "me",
        "updatedAt": "2026-08-28T15:50:00Z",
        "requestType": "privacy_request",
        "privacySubtype": "delete",
        "privacyHandled": False,
        "messages": [
            {
                "id": "m9-privacy",
                "from": "customer",
                "fromAgent": False,
                "name": "Lee Chen",
                "fromName": "Lee Chen",
                "body": "Please delete my stored personal data. I do not need a Shopify account change from this inbox.",
                "at": "2026-08-28T15:50:00Z",
            }
        ],
        "statusEvents": [
            {"at": "2026-08-28T15:51:00Z", "status": "open", "note": "created"},
        ],
    },
    {
        "id": "t-remy-bug",
        "customerName": "Remy Cole",
        "subject": "App crash on iOS — checkout bug",
        "snippet": "The shop app crashes on iOS when I open checkout. I can keep using Android.",
        "status": "open",
        "assignee": "me",
        "updatedAt": "2026-08-28T16:00:00Z",
        "requestType": "bug",
        "severity": "high",
        "device": "iOS",
        "bugHandled": False,
        "messages": [
            {
                "id": "m10-bug",
                "from": "customer",
                "fromAgent": False,
                "name": "Remy Cole",
                "fromName": "Remy Cole",
                "body": "The shop app crashes on iOS when I open checkout. I can keep using Android.",
                "at": "2026-08-28T16:00:00Z",
            }
        ],
        "statusEvents": [
            {"at": "2026-08-28T16:01:00Z", "status": "open", "note": "created"},
        ],
    },
) + tuple(DEMO_SEED_TICKETS)

_store: list[dict] = []
_intake: list[dict] = []
_by_dedupe: dict[tuple, dict] = {}
_seen_messages: set[str] = set()
_next_seq = 1
_store_lock = threading.RLock()
_transaction_local = threading.local()

INTAKE_SOURCES = frozenset({"agentmail", "gorgias", "chat", "seed"})


def _seen_file() -> Path | None:
    raw = os.environ.get("HELPDESK_SEEN_FILE", "").strip()
    return Path(raw) if raw else None


def _store_file() -> Path | None:
    raw = os.environ.get("HELPDESK_STORE_FILE", "").strip()
    return Path(raw) if raw else None


def _load_persisted_seen() -> None:
    path = _seen_file()
    if not path or not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise state_store.StoreUnavailable("Legacy inbox state is unreadable; migration refused") from exc
    if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
        raise state_store.StoreUnavailable("Legacy seen state is invalid; migration refused")
    if isinstance(data, list):
        for item in data:
            if item:
                _seen_messages.add(str(item))


def _persist_seen(message_id: str) -> None:
    if os.environ.get("HELPDESK_DB_FILE"):
        return  # Committed with tickets in transaction().
    path = _seen_file()
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(sorted(_seen_messages)))


def _atomic_write_text(path, text: str) -> None:
    """Write text to path via tmp + os.replace so readers never see a torn file.

    Same pattern as export_projection.py / export_shop_rail.py; a crash or
    concurrent reader mid-write must not corrupt the legacy JSON state. The
    tmp name is unique per call (two writers sharing a legacy JSON path must
    not clobber each other's tmp) and adopts the existing file's mode so a
    0600 state file can't get relaxed to the umask default by the replace.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            os.chmod(tmp_name, os.stat(path).st_mode & 0o777)
        except FileNotFoundError:
            pass  # first write: keep mkstemp's private 0600
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise


def _dedupe_key_to_list(key: tuple) -> list:
    return [list(part) if isinstance(part, tuple) else part for part in key]


def _dedupe_key_from_list(raw: list | None) -> tuple | None:
    if not isinstance(raw, list) or not raw:
        return None
    return tuple(tuple(part) if isinstance(part, list) else part for part in raw)


def _intake_tickets() -> list[dict]:
    return [ticket for ticket in _store if str(ticket.get("id", "")).startswith("t-in-")]


def _persist_store() -> None:
    if os.environ.get("HELPDESK_DB_FILE"):
        return  # Committed with seen IDs in transaction().
    path = _store_file()
    if not path:
        return
    with _store_lock:
        payload = {
            "nextSeq": _next_seq,
            "tickets": [copy.deepcopy(ticket) for ticket in _intake_tickets()],
            "dedupe": [
                {
                    "key": _dedupe_key_to_list(key),
                    "ticketId": ticket.get("id"),
                }
                for key, ticket in _by_dedupe.items()
                if str(ticket.get("id", "")).startswith("t-in-")
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True))


def _load_persisted_store() -> None:
    global _next_seq
    path = _store_file()
    if not path or not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise state_store.StoreUnavailable("Legacy inbox state is unreadable; migration refused") from exc
    state_store.validate(data)
    tickets = data["tickets"]
    by_id: dict[str, dict] = {}
    for raw in tickets:
        if not isinstance(raw, dict):
            continue
        ticket_id = str(raw.get("id") or "")
        if not ticket_id.startswith("t-in-"):
            raise state_store.StoreUnavailable("Unexpected legacy ticket ID; migration refused")
        ticket = copy.deepcopy(raw)
        ticket.setdefault("joined", True)
        ticket.setdefault("source", "agentmail")
        _store.insert(0, ticket)
        by_id[ticket_id] = ticket
        try:
            seq = int(ticket_id.rsplit("-", 1)[-1])
            _next_seq = max(_next_seq, seq + 1)
        except (TypeError, ValueError):
            pass
    for entry in data.get("dedupe") or []:
        if not isinstance(entry, dict):
            continue
        key = _dedupe_key_from_list(entry.get("key"))
        ticket = by_id.get(str(entry.get("ticketId") or ""))
        if key and ticket is not None:
            _by_dedupe[key] = ticket
    try:
        next_seq = int(data.get("nextSeq") or _next_seq)
        _next_seq = max(_next_seq, next_seq)
    except (TypeError, ValueError):
        pass


def seed_catalog_loaded() -> bool:
    return len(_store) >= len(SEED_TICKETS)


def is_seed_ticket(ticket_id: str | None) -> bool:
    """True for fixture / demo seeds. Intake tickets are t-in-*."""
    canonical = _resolve_id(str(ticket_id or ""))
    if not canonical:
        return True
    if canonical.startswith("t-in-"):
        return False
    return any(row["id"] == canonical for row in SEED_TICKETS) or canonical in ALIASES


def reset() -> None:
    global _store, _intake, _by_dedupe, _seen_messages, _next_seq
    _store = [] if os.environ.get("HELPDESK_PRODUCTION") == "1" else [copy.deepcopy(row) for row in SEED_TICKETS]
    for ticket in _store:
        ticket.setdefault("source", "seed")
    _intake = []
    _by_dedupe = {}
    _seen_messages = set()
    _next_seq = 1
    db_path = os.environ.get("HELPDESK_DB_FILE")
    if db_path:
        db = getattr(_transaction_local, "db", None)
        own = db is None
        if own:
            db = state_store.connect(Path(db_path))
        try:
            state = state_store.read(db)
            if state is None and getattr(_transaction_local, "readonly", False):
                raise state_store.StoreUnavailable("Inbox state has not been initialized")
            if state is not None:
                _store = copy.deepcopy(state["tickets"])
                _seen_messages = set(state.get("seen", []))
                _next_seq = state.get("nextSeq", 1)
                by_id = {row["id"]: row for row in _store}
                _by_dedupe = {_dedupe_key_from_list(row["key"]): by_id[row["ticketId"]] for row in state.get("dedupe", [])}
                return
        finally:
            if own:
                db.close()
    _load_persisted_seen()
    _load_persisted_store()


def remember_intake(record: dict) -> None:
    _intake.append(dict(record))
    message_id = record.get("messageId")
    if message_id:
        source = str(record.get("source") or "agentmail").strip().lower() or "agentmail"
        mid = f"{source}:{message_id}"
        _seen_messages.add(mid)
        # Keep bare id for older seen files / AgentMail pull path.
        _seen_messages.add(str(message_id))
        _persist_seen(mid)


def seen_message_id(message_id: str | None, *, source: str | None = None) -> bool:
    if not message_id:
        return False
    mid = str(message_id)
    if source:
        if f"{source}:{mid}" in _seen_messages:
            return True
        # Legacy flat ids only count as seen for the same pull path (agentmail).
        if source == "agentmail" and mid in _seen_messages and f"gorgias:{mid}" not in _seen_messages:
            return True
        return False
    return mid in _seen_messages or any(item.endswith(f":{mid}") for item in _seen_messages)


def normalize_external(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    system = str(raw.get("system") or "").strip().lower()
    if system not in {"gorgias", "agentmail"}:
        return None
    out: dict[str, Any] = {"system": system}
    for key, dest in (
        ("ticketId", "ticketId"),
        ("messageId", "messageId"),
        ("customerEmail", "customerEmail"),
    ):
        value = raw.get(key) if key in raw else raw.get(
            {"ticketId": "ticket_id", "messageId": "message_id", "customerEmail": "customer_email"}.get(key, key)
        )
        if value is not None and str(value).strip():
            out[dest] = str(value).strip()
    return out if out.get("ticketId") or out.get("messageId") else out


def find_ticket(ticket_id: str) -> dict | None:
    canonical = _resolve_id(str(ticket_id or ""))
    for ticket in _store:
        if ticket["id"] == canonical:
            return ticket
    return None


reset()


def intake_records() -> list[dict]:
    return [dict(row) for row in _intake]


def _snippet(body: str) -> str:
    return " ".join(str(body or "").split())[:140]


def infer_privacy_subtype(subject: str = "", body: str = "") -> str | None:
    """Optional Access / Delete / Export peek. Never a Shopify write."""
    hay = f"{subject or ''} {body or ''}".lower()
    if any(marker in hay for marker in ("delete my data", "data deletion", "erase my data", "right to be forgotten")):
        return "delete"
    if any(marker in hay for marker in ("export my data", "data export")):
        return "export"
    if any(marker in hay for marker in ("data request", "access my data", "access request")):
        return "access"
    return None


def infer_request_type(subject: str = "", body: str = "") -> str | None:
    """First-party type from intake subject/body. Never a Shopify write."""
    subject_hay = f"{subject or ''}".lower()
    hay = f"{subject or ''} {body or ''}".lower()
    if "unsubscribe-farm" in hay or "unsubscribe farm" in hay:
        return None
    if any(marker in hay for marker in _PRIVACY_MARKERS):
        return "privacy_request"
    if "unsubscribe" in subject_hay:
        return "marketing_unsubscribe"
    if _is_bug_copy(hay):
        return "bug"
    return None


def _is_bug_copy(hay: str) -> bool:
    if _BUG_TYPE_RE.search(hay):
        return True
    return bool(_BROKEN_RE.search(hay) and _TECH_RE.search(hay))


def infer_severity(subject: str = "", body: str = "") -> str | None:
    """Optional Low / Medium / High / Critical peek. Never a Shopify write."""
    hay = f"{subject or ''} {body or ''}"
    if _CRITICAL_RE.search(hay):
        return "critical"
    if _CRASH_RE.search(hay):
        return "high"
    if _LOW_RE.search(hay):
        return "low"
    return "medium"


def infer_device(subject: str = "", body: str = "") -> str | None:
    """Optional iOS / Android peek from intake keywords. Never a product mutation."""
    hay = f"{subject or ''} {body or ''}"
    if _IOS_RE.search(hay):
        return "iOS"
    if _ANDROID_RE.search(hay):
        return "Android"
    return None


def _normalize_request_type(
    value: str | None, subject: str = "", body: str = ""
) -> str | None:
    typed = str(value or "").strip() or infer_request_type(subject, body)
    if typed in REQUEST_TYPES:
        return typed
    return None


def _normalize_severity(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    return raw if raw in SEVERITIES else None


def _normalize_device(value: str | None) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:40] if text else None


def add_ticket(
    *,
    customer_name: str,
    subject: str,
    body: str,
    received_at: str,
    customer_id: str | None,
    order_id: str | None,
    channel: str,
    from_email: str | None,
    dedupe_key: tuple,
    request_type: str | None = None,
    source: str = "agentmail",
    external: dict[str, Any] | None = None,
) -> dict:
    existing = _by_dedupe.get(dedupe_key)
    if existing:
        return _row(existing, gid_source="joined")
    global _next_seq
    ticket_id = f"t-in-{_next_seq}"
    _next_seq += 1
    typed = _normalize_request_type(request_type, subject, body)
    subtype = infer_privacy_subtype(subject, body) if typed == "privacy_request" else None
    severity = infer_severity(subject, body) if typed == "bug" else None
    device = infer_device(subject, body) if typed == "bug" else None
    source_name = str(source or "agentmail").strip().lower() or "agentmail"
    if source_name not in INTAKE_SOURCES:
        source_name = "agentmail"
    ext = normalize_external(external)
    ticket = {
        "id": ticket_id,
        "customerName": customer_name,
        "subject": subject,
        "snippet": _snippet(body),
        "status": "open",
        "assignee": None,
        "updatedAt": received_at,
        "joined": True,
        "customerId": customer_id,
        "orderId": order_id,
        "channel": channel,
        "fromEmail": from_email,
        "source": source_name,
        "external": ext,
        "requestType": typed,
        "privacySubtype": subtype,
        "privacyHandled": False,
        "unsubscribeHandled": False,
        "bugHandled": False,
        "severity": severity,
        "device": device,
        "messages": [
            {
                "id": f"m-{ticket_id}-1",
                "from": "customer",
                "fromAgent": False,
                "name": customer_name,
                "fromName": customer_name,
                "body": body,
                "at": received_at,
            }
        ],
        "statusEvents": [
            {"at": received_at, "status": "open", "note": "created"},
        ],
    }
    if from_email:
        ticket["messages"][0]["fromEmail"] = from_email
    if ext and ext.get("messageId"):
        ticket["messages"][0]["externalMessageId"] = ext["messageId"]
    _store.insert(0, ticket)
    _by_dedupe[dedupe_key] = ticket
    _persist_store()
    return _row(ticket, gid_source="joined")


def append_agent_message(
    ticket_id: str,
    body: str,
    *,
    via: str | None = None,
    external_message_id: str | None = None,
    delivery_status: str | None = None,
    close: bool = False,
    gid_source: str = "sample",
) -> dict:
    """Append a human-sent agent reply. Persists intake tickets."""
    text = str(body or "").strip()
    if not text:
        raise bad_request("text is required", field="text")
    ticket = find_ticket(ticket_id)
    if ticket is None:
        raise not_found("ticket", str(ticket_id))
    now = _now_iso()
    message: dict[str, Any] = {
        "id": f"m-{ticket['id']}-out-{len(ticket.get('messages') or []) + 1}",
        "from": "agent",
        "fromAgent": True,
        "name": STORE_NAME,
        "fromName": STORE_NAME,
        "body": text,
        "at": now,
    }
    if via:
        message["via"] = str(via)
    if external_message_id:
        message["externalMessageId"] = str(external_message_id)
    if delivery_status:
        message["deliveryStatus"] = str(delivery_status)
    ticket.setdefault("messages", []).append(message)
    ticket["snippet"] = _snippet(text)
    ticket["updatedAt"] = now
    if close:
        ticket["status"] = "closed"
        ticket.setdefault("statusEvents", []).append(
            {"at": now, "status": "closed", "note": "sent"}
        )
    if str(ticket.get("id", "")).startswith("t-in-"):
        _persist_store()
    return get_ticket(ticket["id"], gid_source)


def _resolve_id(ticket_id: str) -> str:
    return ALIASES.get(str(ticket_id), str(ticket_id))


def _gids_for(ticket: dict, gid_source: str = "sample") -> tuple[str | None, str | None]:
    if ticket.get("joined"):
        return ticket.get("customerId"), ticket.get("orderId")
    table = LIVE_GIDS if gid_source in {"live", "live-holes"} else SAMPLE_GIDS
    return table.get(ticket["id"], (None, None))


def ticket_in_view(ticket: dict, view: str) -> bool:
    status = ticket["status"]
    if view == "all":
        return True
    if view == "open":
        return status == "open"
    if view == "closed":
        return status == "closed"
    if view == "snoozed":
        return status == "snoozed"
    if view == "mine":
        return ticket.get("assignee") == "me" and status == "open"
    if view == "unassigned":
        return ticket.get("assignee") is None and status == "open"
    return False


def _row(ticket: dict, gid_source: str = "sample") -> dict:
    customer_id, order_id = _gids_for(ticket, gid_source)
    typed = ticket.get("requestType") or None
    severity = _normalize_severity(ticket.get("severity")) if typed == "bug" else None
    device = _normalize_device(ticket.get("device")) if typed == "bug" else None
    return {
        "id": ticket["id"],
        "customerName": project_customer_name(ticket),
        "subject": ticket["subject"],
        "snippet": ticket["snippet"],
        "status": ticket["status"],
        "updatedAt": ticket["updatedAt"],
        "customerId": customer_id,
        "orderId": order_id,
        "requestType": typed,
        "severity": severity,
        "device": device,
    }


def list_tickets(view: str = "open", limit: int = 20, gid_source: str = "sample") -> list[dict]:
    if view not in VIEWS:
        raise bad_request("view must be open, closed, all, snoozed, mine, or unassigned", field="view")
    try:
        cap = int(limit)
    except (TypeError, ValueError) as exc:
        raise bad_request("limit must be an integer", field="limit") from exc
    if cap < 1 or cap > 100:
        raise bad_request("limit must be 1..100", field="limit")
    rows = [t for t in _store if ticket_in_view(t, view)]
    return [_row(t, gid_source) for t in rows[:cap]]


def get_ticket(ticket_id: str, gid_source: str = "sample") -> dict:
    if not ticket_id:
        raise bad_request("ticketId is required", field="ticketId")
    canonical = _resolve_id(ticket_id)
    for ticket in _store:
        if ticket["id"] == canonical:
            row = _row(ticket, gid_source)
            row["messages"] = [project_message(ticket, message) for message in ticket["messages"]]
            row["statusEvents"] = [dict(event) for event in ticket["statusEvents"]]
            row["escalated"] = bool(ticket.get("escalated"))
            reason = ticket.get("escalationReason")
            if reason:
                row["escalationReason"] = str(reason)
            if ticket.get("fromEmail"):
                row["fromEmail"] = ticket.get("fromEmail")
            source = str(ticket.get("source") or ("seed" if not ticket.get("joined") else "agentmail"))
            row["source"] = source
            ext = normalize_external(ticket.get("external"))
            if ext:
                row["external"] = ext
            subtype = ticket.get("privacySubtype") if row.get("requestType") == "privacy_request" else None
            row["privacySubtype"] = subtype if subtype in PRIVACY_SUBTYPES else None
            row["privacyHandled"] = bool(ticket.get("privacyHandled"))
            row["unsubscribeHandled"] = bool(ticket.get("unsubscribeHandled"))
            row["bugHandled"] = bool(ticket.get("bugHandled"))
            return row
    raise not_found("ticket", str(ticket_id))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def escalate_ticket(ticket_id: str, reason: str | None = None, gid_source: str = "sample") -> dict:
    """First-party helpdesk escalate. Never a Shopify mutation."""
    if not ticket_id:
        raise bad_request("ticketId is required", field="ticketId")
    canonical = _resolve_id(ticket_id)
    note_reason = " ".join(str(reason or "").split())
    for ticket in _store:
        if ticket["id"] != canonical:
            continue
        now = _now_iso()
        already = bool(ticket.get("escalated"))
        ticket["escalated"] = True
        if note_reason:
            ticket["escalationReason"] = note_reason[:240]
        ticket["updatedAt"] = now
        if not already:
            note = "escalated"
            if note_reason:
                note = f"escalated: {note_reason}"[:140]
            ticket.setdefault("statusEvents", []).append(
                {"at": now, "status": ticket["status"], "note": note}
            )
        _persist_store()
        return get_ticket(canonical, gid_source)
    raise not_found("ticket", str(ticket_id))


def mark_privacy_handled(ticket_id: str, gid_source: str = "sample") -> dict:
    """First-party helpdesk flag. Never a Shopify Customer Privacy write."""
    if not ticket_id:
        raise bad_request("ticketId is required", field="ticketId")
    canonical = _resolve_id(ticket_id)
    for ticket in _store:
        if ticket["id"] != canonical:
            continue
        if ticket.get("requestType") != "privacy_request":
            raise bad_request("ticket is not a privacy request", field="ticketId")
        now = _now_iso()
        already = bool(ticket.get("privacyHandled"))
        ticket["privacyHandled"] = True
        ticket["updatedAt"] = now
        if not already:
            ticket.setdefault("statusEvents", []).append(
                {"at": now, "status": ticket["status"], "note": "privacy handled"}
            )
        _persist_store()
        return get_ticket(canonical, gid_source)
    raise not_found("ticket", str(ticket_id))


def mark_unsubscribed(ticket_id: str, gid_source: str = "sample") -> dict:
    """First-party helpdesk flag. Never a Shopify marketing consent write."""
    if not ticket_id:
        raise bad_request("ticketId is required", field="ticketId")
    canonical = _resolve_id(ticket_id)
    for ticket in _store:
        if ticket["id"] != canonical:
            continue
        if ticket.get("requestType") != "marketing_unsubscribe":
            raise bad_request("ticket is not a marketing unsubscribe request", field="ticketId")
        now = _now_iso()
        already = bool(ticket.get("unsubscribeHandled"))
        ticket["unsubscribeHandled"] = True
        ticket["updatedAt"] = now
        if not already:
            ticket.setdefault("statusEvents", []).append(
                {"at": now, "status": ticket["status"], "note": "unsubscribed"}
            )
        _persist_store()
        return get_ticket(canonical, gid_source)
    raise not_found("ticket", str(ticket_id))


def mark_bug_handled(ticket_id: str, gid_source: str = "sample") -> dict:
    """First-party helpdesk flag. Never a Shopify product write."""
    if not ticket_id:
        raise bad_request("ticketId is required", field="ticketId")
    canonical = _resolve_id(ticket_id)
    for ticket in _store:
        if ticket["id"] != canonical:
            continue
        if ticket.get("requestType") != "bug":
            raise bad_request("ticket is not a bug report", field="ticketId")
        now = _now_iso()
        already = bool(ticket.get("bugHandled"))
        ticket["bugHandled"] = True
        ticket["updatedAt"] = now
        if not already:
            ticket.setdefault("statusEvents", []).append(
                {"at": now, "status": ticket["status"], "note": "bug handled"}
            )
        _persist_store()
        return get_ticket(canonical, gid_source)
    raise not_found("ticket", str(ticket_id))


@contextmanager
def transaction(*, write: bool = True):
    """Reload and commit one complete operation, including intake deduplication.

    Nested ticket helpers reuse the dispatch transaction. Failed operations
    roll back both the database and the process-local cache.
    """
    db_path = os.environ.get("HELPDESK_DB_FILE")
    with _store_lock:
        if not db_path or getattr(_transaction_local, "db", None) is not None:
            if write and getattr(_transaction_local, "readonly", False):
                raise state_store.StoreUnavailable("Mutation refused in a read-only operation")
            yield
            return
        db = state_store.connect(Path(db_path), readonly=not write)
        _transaction_local.db = db
        _transaction_local.readonly = not write
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            reset()
            yield
            if write:
                state_store.write(db, {
                    "tickets": copy.deepcopy(_store),
                    "seen": sorted(_seen_messages),
                    "nextSeq": _next_seq,
                    "dedupe": [{"key": _dedupe_key_to_list(k), "ticketId": v["id"]} for k, v in _by_dedupe.items()],
                })
            db.commit()
        except Exception:
            db.rollback()
            reset()
            raise
        finally:
            _transaction_local.db = None
            _transaction_local.readonly = False
            db.close()


def _transactional(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with transaction():
            return function(*args, **kwargs)
    return wrapped


for _name in ("remember_intake", "add_ticket", "append_agent_message", "escalate_ticket",
              "mark_privacy_handled", "mark_unsubscribed", "mark_bug_handled"):
    globals()[_name] = _transactional(globals()[_name])
