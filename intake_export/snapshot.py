"""Bounded, explicit snapshot capture; never invoked by the intake application."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import time

from .mcp_read import ExportError


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode()


def private_json(path, data):
    raw = encoded(data)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    return sha256(raw).hexdigest(), len(raw)


def full_ticket(client, tid, pause):
    before = client.call("get_ticket", {"ticket_id": tid})
    pause()
    messages, cursor, cursors, seen = [], None, set(), set()
    for _ in range(100):
        page = client.call("get_ticket_messages", {"ticket_id": tid, "limit": 50, **({"cursor": cursor} if cursor else {})})
        pause()
        rows = page.get("data")
        if not isinstance(rows, list):
            raise ExportError("Message page is missing its records.")
        for message in rows:
            if not isinstance(message, dict) or message.get("id") is None:
                raise ExportError("Message is missing its source ID.")
            mid = str(message["id"])
            if mid in seen:
                raise ExportError("Repeated message ID across export pages; ticket may have changed.")
            seen.add(mid)
            messages.append(message)
        cursor = (page.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        if cursor in cursors:
            raise ExportError("Repeated message cursor; refusing an incomplete conversation.")
        cursors.add(cursor)
    else:
        raise ExportError("Conversation exceeds the export page limit.")
    after = client.call("get_ticket", {"ticket_id": tid})
    pause()
    if before.get("id") != tid or after.get("id") != tid:
        raise ExportError("Source ticket ID does not match the requested ticket.")
    if before.get("updated_datetime") != after.get("updated_datetime"):
        raise ExportError("Ticket changed during capture; retry in a new snapshot.")
    declared = after.get("messages_count")
    if declared is not None and declared != len(messages):
        raise ExportError("Source message count differs from the fully paginated conversation.")
    # Preserve the original detail response separately in the evidence file.
    ticket = {**after, "messages": sorted(messages, key=lambda m: (m.get("created_datetime") or "", str(m["id"])))}
    return ticket, {"ticket_id": tid, "detail": after, "fetched_message_count": len(messages),
                    "message_pages": len(cursors) + 1, "checked_at": stamp()}


def capture(client, directory, limit=50, delay=0.35, progress=lambda _: None):
    if not 1 <= limit <= 100:
        raise ExportError("Choose between 1 and 100 tickets for this test snapshot.")
    pause = lambda: time.sleep(delay)
    listed = client.call("list_inbox_tickets", {"limit": limit})
    pause()
    summaries = listed.get("data")
    if not isinstance(summaries, list) or not summaries:
        raise ExportError("No ticket records were returned.")
    ids = [t.get("id") for t in summaries]
    if any(type(tid) is not int or tid <= 0 for tid in ids) or len(set(ids)) != len(ids):
        raise ExportError("Ticket list has invalid or duplicate IDs.")
    manifest = {"format": "gorgias-mcp-test-snapshot-v1", "started_at": stamp(),
                "scope": "most recently updated tickets, bounded test sample",
                "requested_tickets": limit, "listed_tickets": len(ids),
                "account_export_complete": not bool((listed.get("meta") or {}).get("next_cursor")),
                "transport": "existing read-only Gorgias MCP", "provider_writes": 0,
                "attachment_downloads": 0, "files": [], "failed": []}
    totals = {"tickets": 0, "messages": 0, "notes": 0, "attachments": 0,
              "missing_full_body": 0, "available_stripped_body": 0, "unavailable_content": 0,
              "messages_with_headers": 0, "open": 0, "closed": 0}
    for position, tid in enumerate(ids, 1):
        try:
            ticket, evidence = full_ticket(client, tid, pause)
            # One ticket per file stays within the sandbox's bounded import size.
            payload = {"tickets": [ticket]}
            raw = encoded(payload)
            if len(raw) > 20 * 1024 * 1024:
                raise ExportError("A conversation exceeds the sandbox's 20 MiB file limit.")
            filename = f"ticket-{tid}.json"
            checksum, size = private_json(directory / filename, payload)
            private_json(directory / f"evidence-{tid}.json", evidence)
            manifest["files"].append({"file": filename, "sha256": checksum, "bytes": size,
                                      "ticket_id": tid, "messages": len(ticket["messages"])})
            totals["tickets"] += 1
            if ticket.get("status") in ("open", "closed"):
                totals[ticket["status"]] += 1
            for message in ticket["messages"]:
                totals["messages"] += 1
                totals["notes"] += message.get("public") is False
                totals["attachments"] += len(message.get("attachments") or [])
                full = any(isinstance(message.get(k), str) and message[k].strip() for k in ("body_text", "body_html"))
                stripped = any(isinstance(message.get(k), str) and message[k].strip() for k in ("stripped_text", "stripped_html"))
                totals["missing_full_body"] += not full
                totals["available_stripped_body"] += not full and stripped
                totals["unavailable_content"] += not full and not stripped
                totals["messages_with_headers"] += bool(message.get("headers"))
        except ExportError as exc:
            manifest["failed"].append({"ticket_id": tid, "reason": str(exc)})
        progress({"processed": position, "total": len(ids), "saved": totals["tickets"], "failed": len(manifest["failed"])})
    manifest.update({"finished_at": stamp(), "counts": totals,
                     "sample_capture_complete": not manifest["failed"] and totals["tickets"] == len(ids)})
    private_json(directory / "manifest.json", manifest)
    return manifest


def destination():
    base = Path(__file__).resolve().parents[1] / "intake" / "exports"
    if base.is_symlink():
        raise ExportError("Export storage cannot be a symbolic link.")
    base.mkdir(mode=0o700, exist_ok=True)
    os.chmod(base, 0o700)
    directory = base / datetime.now(timezone.utc).strftime("gorgias-%Y%m%dT%H%M%S-%fZ")
    directory.mkdir(mode=0o700)
    return directory
