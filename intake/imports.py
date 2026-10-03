"""Offline Gorgias snapshot parser. Deliberately contains no API client."""
import json
import re

from .policy import Invalid
from .records import choice, external_id, plain_html, text, timestamp

MAX_BYTES = 20 * 1024 * 1024


def account_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,79}", value):
        raise Invalid("Source account: use 1–80 lowercase letters, digits, dots or hyphens.")
    return value


def rows(value, label):
    if isinstance(value, dict):
        meta = value.get("meta") or {}
        if not isinstance(meta, dict):
            raise Invalid(f"{label}: invalid pagination metadata.")
        if meta.get("next_cursor") or value.get("next_cursor"):
            raise Invalid(f"{label}: incomplete page; combine all exported pages first.")
        value = value.get("data")
    if not isinstance(value, list):
        raise Invalid(f"{label}: expected an explicit list, including empty lists.")
    return value


def object_or_empty(value, field):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Invalid(f"{field}: expected an object.")
    return value


def parse_message(raw, path):
    if not isinstance(raw, dict):
        raise Invalid(f"{path}: expected a message object.")
    mid = external_id(raw.get("id"), f"{path}.id")
    if type(raw.get("public")) is not bool or type(raw.get("from_agent")) is not bool:
        raise Invalid(f"{path}: public and from_agent must be explicit booleans.")
    body = raw.get("body_text")
    if body is None:
        body = plain_html(raw.get("body_html") or "")
    else:
        body = text(body, f"{path}.body_text", 2_000_000)
    sender = object_or_empty(raw.get("sender"), f"{path}.sender")
    headers = object_or_empty(raw.get("headers"), f"{path}.headers")
    attachments = raw.get("attachments") or []
    if not isinstance(attachments, list) or any(not isinstance(a, dict) for a in attachments):
        raise Invalid(f"{path}.attachments: expected a list of metadata objects.")
    return {
        "external_id": mid,
        "kind": "note" if not raw["public"] else "outgoing" if raw["from_agent"] else "incoming",
        "author_name": text(sender.get("name") or "Unknown author", f"{path}.sender.name"),
        "author_email": text(sender.get("email") or "", f"{path}.sender.email", 320),
        "body": body,
        "created_at": timestamp(raw.get("created_datetime"), f"{path}.created_datetime"),
        "channel": text(raw.get("channel") or "unknown", f"{path}.channel", 80),
        "headers": headers, "attachments": attachments, "raw": raw,
    }


def parse_export(payload):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_BYTES:
        raise Invalid("Choose a JSON export no larger than 20 MiB.")
    try:
        def reject_constant(value):
            raise ValueError("Non-finite JSON value")
        def unique_object(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("Repeated JSON field")
                result[key] = item
            return result
        value = json.loads(payload.decode("utf-8-sig"), parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Invalid("Invalid JSON export. CSV summaries are not supported; full messages are required.") from exc
    if isinstance(value, dict) and "tickets" in value:
        # Keep pagination metadata when unwrapping the convenience envelope.
        value = {"data": value["tickets"], "meta": value.get("meta"),
                 "next_cursor": value.get("next_cursor")}
    tickets = rows(value, "tickets")
    if not tickets or len(tickets) > 2000:
        raise Invalid("Import between 1 and 2,000 tickets per file.")
    normalized, warnings, seen_tickets, seen_messages = [], [], set(), set()
    for index, raw in enumerate(tickets):
        path = f"tickets[{index}]"
        if not isinstance(raw, dict):
            raise Invalid(f"{path}: expected a ticket object.")
        tid = external_id(raw.get("id"), f"{path}.id")
        if tid in seen_tickets:
            raise Invalid(f"{path}: repeated ticket ID within this file.")
        seen_tickets.add(tid)
        message_rows = rows(raw.get("messages"), f"{path}.messages")
        count = raw.get("messages_count")
        if count is not None and (type(count) is not int or count != len(message_rows)):
            raise Invalid(f"{path}: messages_count does not match the exported messages.")
        if count is None:
            warnings.append(f"{path}: no messages_count; completeness must be reconciled with the source.")
        customer = object_or_empty(raw.get("customer"), f"{path}.customer")
        if customer.get("id") is not None:
            external_id(customer["id"], f"{path}.customer.id")
        assignee = object_or_empty(raw.get("assignee_user"), f"{path}.assignee_user")
        priority = raw.get("priority") or "normal"
        priority = "critical" if priority == "urgent" else priority
        status = choice(raw.get("status"), ("open", "closed"), f"{path}.status")
        tags = raw.get("tags") or []
        if not isinstance(tags, list):
            raise Invalid(f"{path}.tags: expected a list.")
        messages = []
        for mi, item in enumerate(message_rows):
            message = parse_message(item, f"{path}.messages[{mi}]")
            if item.get("ticket_id") is not None and external_id(item["ticket_id"], f"{path}.messages[{mi}].ticket_id") != tid:
                raise Invalid(f"{path}.messages[{mi}]: message belongs to a different source ticket.")
            if message["external_id"] in seen_messages:
                raise Invalid(f"{path}.messages[{mi}]: repeated message ID within this file.")
            seen_messages.add(message["external_id"])
            messages.append(message)
        created = timestamp(raw.get("created_datetime"), f"{path}.created_datetime")
        updated = timestamp(raw.get("updated_datetime") or raw.get("created_datetime"), f"{path}.updated_datetime")
        if any(m["attachments"] for m in messages):
            warnings.append(f"{path}: attachment metadata only; files will not be downloaded.")
        normalized.append({
            "external_id": tid, "subject": text(raw.get("subject") or "Untitled ticket", f"{path}.subject", 2000),
            "status": status, "priority": choice(priority, ("low", "normal", "high", "critical"), f"{path}.priority"),
            "assignee": text(assignee.get("email") or assignee.get("name") or "", f"{path}.assignee", 500),
            "channel": text(raw.get("channel") or raw.get("via") or "unknown", f"{path}.channel", 80),
            "created_at": created, "updated_at": updated, "customer": customer,
            "customer_name": text(customer.get("name") or "Unknown contact", f"{path}.customer.name"),
            "customer_email": text(customer.get("email") or "", f"{path}.customer.email", 320),
            "tags": tags, "messages": messages,
            "raw": {k: v for k, v in raw.items() if k != "messages"},
        })
    return normalized, warnings
