"""AgentMail → ingest_email bridge. Pull only. Never send, reply, or create."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .errors import bad_request
from .fixtures_intake import (
    FIXTURE_MESSAGE_IDS,
    MAILBOX_ADDRESS,
    MAILBOX_DISPLAY,
    MAILBOX_FIXTURES,
)
from .intake import handle_ingest_email
from . import tickets

_KEY_NAME = "AGENTMAIL_API_KEY"
_UNREAD_LABELS = frozenset({"unread", "new", "inbox"})
_SENT_LABELS = frozenset({"sent", "draft", "trash", "outbox"})


def _clean(value: str) -> str:
    return value.strip().strip("\"'").replace("\r", "")


def _load_key_into_environ() -> bool:
    """Ensure AgentMail() can read the key from the process env. Never log it."""
    if os.environ.get(_KEY_NAME, "").strip():
        return True
    root = Path(__file__).resolve().parents[3]
    env_path = root / ".env"
    if not env_path.is_file():
        return False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, raw = text.split("=", 1)
        if key.strip() == _KEY_NAME:
            value = _clean(raw)
            if value:
                os.environ[_KEY_NAME] = value
                return True
    return False


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, dict)):
        try:
            return list(value)
        except TypeError:
            return [value]
    return [value]


def format_from(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [format_from(item) for item in value]
        return ", ".join(part for part in parts if part)
    nested = _attr(value, "from_address", "fromAddress")
    if nested is not None and nested is not value:
        nested_text = format_from(nested)
        if nested_text:
            return nested_text
    name = _attr(
        value,
        "name",
        "display_name",
        "displayName",
        "from_name",
        "fromName",
    )
    email = _attr(
        value,
        "email",
        "address",
        "email_address",
        "emailAddress",
        "from_email",
        "fromEmail",
    )
    name = str(name or "").strip()
    email = str(email or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return email or name


def message_body(message: Any) -> str:
    raw = (
        _attr(message, "extracted_text", "extractedText")
        or _attr(message, "text")
        or _attr(message, "extracted_html", "extractedHtml")
        or _attr(message, "html")
        or ""
    )
    return str(raw)


def message_received_at(message: Any) -> str:
    raw = (
        _attr(message, "received_at", "receivedAt")
        or _attr(message, "created_at", "createdAt")
        or _attr(message, "timestamp")
        or ""
    )
    return str(raw)


def message_id_of(message: Any) -> str:
    return str(_attr(message, "message_id", "messageId", "id") or "").strip()


def _labels(message: Any) -> set[str]:
    return {str(label).strip().lower() for label in _as_list(_attr(message, "labels")) if label}


def _from_addresses(value: Any) -> set[str]:
    blob = format_from(value).lower()
    found = set()
    if MAILBOX_ADDRESS.lower() in blob:
        found.add(MAILBOX_ADDRESS.lower())
    return found


def is_inbound(message: Any) -> bool:
    labels = _labels(message)
    if labels & _SENT_LABELS:
        return False
    if _from_addresses(_attr(message, "from_", "from")):
        return False
    return True


def is_unread_or_new(message: Any, *, any_unread: bool) -> bool:
    labels = _labels(message)
    if not any_unread:
        return True
    return bool(labels & _UNREAD_LABELS) or not labels


def fixture_messages(limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in MAILBOX_FIXTURES[:limit]:
        mapped = _normalize(item)
        if mapped:
            rows.append(mapped)
    return rows


def _resolve_inbox_id(client: Any) -> str | None:
    listed = client.inboxes.list(limit=100)
    inboxes = _as_list(_attr(listed, "inboxes", "items") or listed)
    want = MAILBOX_ADDRESS.lower()
    for inbox in inboxes:
        inbox_id = str(_attr(inbox, "inbox_id", "inboxId") or "").strip()
        email = str(_attr(inbox, "email") or "").strip().lower()
        display = str(_attr(inbox, "display_name", "displayName") or "").strip()
        if email == want or inbox_id.lower() == want:
            return inbox_id or MAILBOX_ADDRESS
        if display == MAILBOX_DISPLAY and want in (email, inbox_id.lower()):
            return inbox_id or MAILBOX_ADDRESS
    return None


def _normalize(message: Any) -> dict[str, Any] | None:
    mid = message_id_of(message)
    if not mid:
        return None
    formatted = format_from(_attr(message, "from_", "from"))
    extra_name = str(_attr(message, "from_name", "fromName") or "").strip()
    extra_email = str(_attr(message, "from_email", "fromEmail") or "").strip()
    if extra_name and extra_email:
        formatted = f"{extra_name} <{extra_email}>"
    elif extra_name and "<" not in formatted:
        formatted = f"{extra_name} <{formatted}>" if formatted else extra_name
    elif extra_email and not formatted:
        formatted = extra_email
    return {
        "message_id": mid,
        "from": formatted,
        "subject": str(_attr(message, "subject") or ""),
        "body": message_body(message),
        "received_at": message_received_at(message),
        "labels": sorted(_labels(message)),
    }


def _live_messages(limit: int) -> list[dict[str, Any]] | None:
    try:
        if not _load_key_into_environ():
            return None
        from agentmail import AgentMail

        client = AgentMail()
        inbox_id = _resolve_inbox_id(client)
        if not inbox_id:
            return None
        listed = client.inboxes.messages.list(inbox_id=inbox_id, limit=limit)
        items = _as_list(_attr(listed, "messages", "items") or listed)
        any_unread = any(_labels(item) & _UNREAD_LABELS for item in items)
        out: list[dict[str, Any]] = []
        for item in items:
            if not is_inbound(item) or not is_unread_or_new(item, any_unread=any_unread):
                continue
            mid = message_id_of(item)
            if not mid:
                continue
            full = client.inboxes.messages.get(inbox_id=inbox_id, message_id=mid)
            mapped = _normalize(full) or _normalize(item)
            if mapped:
                out.append(mapped)
            if len(out) >= limit:
                break
        return out
    except Exception:
        return None


def load_messages(limit: int) -> tuple[list[dict[str, Any]], str]:
    live = _live_messages(limit)
    if live:
        return live, "live"
    return fixture_messages(limit), "fixture"


def _limit(args: dict[str, Any]) -> int:
    raw = args.get("limit", 20)
    try:
        cap = int(raw)
    except (TypeError, ValueError) as exc:
        raise bad_request("limit must be an integer", field="limit") from exc
    if cap < 1 or cap > 100:
        raise bad_request("limit must be 1..100", field="limit")
    return cap


def _ticket_row(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload["id"],
        "customerName": payload["customerName"],
        "subject": payload["subject"],
        "snippet": payload["snippet"],
        "status": payload["status"],
        "updatedAt": payload["updatedAt"],
        "customerId": payload.get("customerId"),
        "orderId": payload.get("orderId"),
        "requestType": payload.get("requestType") or None,
    }


def handle_pull_mailbox(args: dict[str, Any]) -> dict[str, Any]:
    limit = _limit(args)
    force = bool(args.get("force"))
    messages, source = load_messages(limit)
    ingested: list[dict[str, Any]] = []
    spam: list[dict[str, str]] = []
    skipped = 0
    for message in messages:
        mid = message.get("message_id")
        if tickets.seen_message_id(mid, source="agentmail"):
            skipped += 1
            continue
        if (
            not force
            and mid
            and str(mid) in FIXTURE_MESSAGE_IDS
            and tickets.seed_catalog_loaded()
        ):
            skipped += 1
            continue
        from_value = message.get("from") or ""
        subject = message.get("subject") or ""
        body = message.get("body") or ""
        received_at = message.get("received_at") or ""
        if not from_value or not subject or not body or not received_at:
            skipped += 1
            continue
        result = handle_ingest_email(
            {
                "from": from_value,
                "subject": subject,
                "body": body,
                "receivedAt": received_at,
                "messageId": mid,
            }
        )
        if result.get("spam"):
            spam.append({"from": from_value, "subject": subject})
            continue
        ingested.append(_ticket_row(result))
    return {
        "ingested": ingested,
        "spam": spam,
        "skipped": skipped,
        "source": source,
        "mailbox": MAILBOX_ADDRESS,
        "mailboxDisplay": MAILBOX_DISPLAY,
    }
