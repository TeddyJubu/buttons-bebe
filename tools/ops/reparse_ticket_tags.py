#!/usr/bin/env python3
"""Fill parsed_messages.ticket_tags from stored webhook raw payloads.

Read-only unless --apply is passed. Never touches status, assignee, priority,
spam flags, message text, or the job queue. Fill-only: empty/[] rows only.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

EMPTY = {None, "", "[]"}


def normalize_tags(value):
    from bb_webhook.webhook_handler import _normalize_ticket_tags
    return _normalize_ticket_tags(value)


def tags_from_raw(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    ticket = payload.get("ticket")
    if not isinstance(ticket, dict):
        data = payload.get("data")
        ticket = data.get("ticket") if isinstance(data, dict) else {}
    if not isinstance(ticket, dict):
        return None
    return normalize_tags(ticket.get("tags"))


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def latest_keys(conn) -> set[tuple[str, int]]:
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT message_id, ticket_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY ticket_id
                       ORDER BY received_at DESC, message_id DESC
                   ) AS n
            FROM parsed_messages
        )
        SELECT message_id, ticket_id FROM ranked WHERE n = 1
        """
    )
    return {(str(r["message_id"]), int(r["ticket_id"])) for r in rows}


def plan(conn) -> list[dict]:
    latest = latest_keys(conn)
    updates = []
    for row in conn.execute(
        "SELECT e.message_id, e.ticket_id, e.raw_payload, p.ticket_tags "
        "FROM webhook_events e "
        "JOIN parsed_messages p ON p.message_id = e.message_id AND p.ticket_id = e.ticket_id"
    ):
        current = row["ticket_tags"]
        if current not in EMPTY:
            continue
        tags = tags_from_raw(row["raw_payload"])
        if not tags:
            continue
        key = (str(row["message_id"]), int(row["ticket_id"]))
        updates.append(
            {
                "message_id": str(row["message_id"]),
                "ticket_id": int(row["ticket_id"]),
                "tags": tags,
                "latest": key in latest,
            }
        )
    return updates


def apply_updates(conn, updates: list[dict]) -> int:
    changed = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for item in updates:
            cursor = conn.execute(
                """
                UPDATE parsed_messages
                   SET ticket_tags = ?
                 WHERE message_id = ? AND ticket_id = ?
                   AND (ticket_tags IS NULL OR ticket_tags IN ('', '[]'))
                """,
                (json.dumps(item["tags"]), item["message_id"], item["ticket_id"]),
            )
            changed += cursor.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return changed


def summarize(updates: list[dict]) -> dict:
    tickets = {item["ticket_id"] for item in updates}
    latest_tickets = {item["ticket_id"] for item in updates if item["latest"]}
    names: dict[str, int] = {}
    for item in updates:
        for tag in item["tags"]:
            names[tag] = names.get(tag, 0) + 1
    return {
        "rows": len(updates),
        "tickets": len(tickets),
        "latest_rows": sum(1 for item in updates if item["latest"]),
        "latest_tickets": len(latest_tickets),
        "tag_names": dict(sorted(names.items(), key=lambda pair: (-pair[1], pair[0]))),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        raise SystemExit("--apply requires --backup")
    if args.apply:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from sqlite_backup import backup
        print(json.dumps({"backup": backup(args.source, args.backup)}))
    conn = connect(args.source)
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM parsed_messages "
            "WHERE ticket_tags IS NOT NULL AND ticket_tags NOT IN ('', '[]')"
        ).fetchone()[0]
        updates = plan(conn)
        report = summarize(updates)
        report["before_tagged_rows"] = before
        report["mode"] = "apply" if args.apply else "dry-run"
        if args.apply:
            report["applied_rows"] = apply_updates(conn, updates)
            report["after_tagged_rows"] = conn.execute(
                "SELECT COUNT(*) FROM parsed_messages "
                "WHERE ticket_tags IS NOT NULL AND ticket_tags NOT IN ('', '[]')"
            ).fetchone()[0]
        print(json.dumps(report, sort_keys=True))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
