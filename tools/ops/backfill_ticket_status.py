#!/usr/bin/env python3
"""Fill parsed_messages.ticket_status from live Gorgias GET /tickets.

Read-only Gorgias access. Fill-only on empty status rows. Does not enqueue jobs
or change tags, assignee, priority, spam flags, or message text.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# The shared normalizer lives in the webhook package; make it importable when
# this script is invoked directly (its tests add the same path).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webhook" / "src"))

import httpx
import sqlite3

EMPTY = {None, ""}


def normalize_status(value):
    from bb_webhook.webhook_handler import _normalize_ticket_status
    return _normalize_ticket_status(value)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ticket_ids(conn) -> list[int]:
    rows = conn.execute("SELECT DISTINCT ticket_id FROM parsed_messages ORDER BY ticket_id")
    return [int(row[0]) for row in rows]


def chunks(values, size=100):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def fetch_statuses(base_url: str, auth: tuple[str, str], ids: list[int]) -> dict[int, str]:
    found: dict[int, str] = {}
    headers = {"User-Agent": "ButtonsBebe-Dashboard/1.0", "Accept": "application/json"}
    with httpx.Client(timeout=20.0) as client:
        for batch in chunks(ids, 100):
            for attempt in range(4):
                response = client.get(
                    f"{base_url.rstrip('/')}/api/tickets",
                    auth=auth,
                    params={"ticket_ids": batch, "limit": 100},
                    headers=headers,
                )
                if response.status_code == 429 and attempt < 3:
                    retry = response.headers.get("Retry-After", "1")
                    try:
                        delay = min(max(float(retry), 0.5), 10.0)
                    except ValueError:
                        delay = 1.0
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list):
                    raise ValueError("Gorgias ticket list is malformed")
                for ticket in data:
                    if not isinstance(ticket, dict):
                        continue
                    ticket_id = ticket.get("id")
                    status = normalize_status(ticket.get("status"))
                    if isinstance(ticket_id, int) and status:
                        found[ticket_id] = status
                break
            else:
                raise RuntimeError("Gorgias rate limit persisted")
    return found


def plan(conn, statuses: dict[int, str]) -> list[dict]:
    updates = []
    for ticket_id, status in statuses.items():
        rows = list(conn.execute(
            "SELECT message_id, ticket_status FROM parsed_messages WHERE ticket_id = ?",
            (ticket_id,),
        ))
        for row in rows:
            if row["ticket_status"] not in EMPTY:
                continue
            updates.append({"ticket_id": ticket_id, "message_id": str(row["message_id"]), "status": status})
    return updates


def apply_updates(conn, updates: list[dict]) -> int:
    changed = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for item in updates:
            cursor = conn.execute(
                """
                UPDATE parsed_messages
                   SET ticket_status = ?
                 WHERE ticket_id = ? AND message_id = ?
                   AND (ticket_status IS NULL OR ticket_status = '')
                """,
                (item["status"], item["ticket_id"], item["message_id"]),
            )
            changed += cursor.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return changed


def summarize(updates: list[dict], fetched: int, known: int) -> dict:
    names: dict[str, int] = {}
    tickets = {item["ticket_id"] for item in updates}
    for item in updates:
        names[item["status"]] = names.get(item["status"], 0) + 1
    return {
        "known_tickets": known,
        "fetched_with_status": fetched,
        "rows": len(updates),
        "tickets": len(tickets),
        "status_names": dict(sorted(names.items(), key=lambda pair: (-pair[1], pair[0]))),
    }


def settings():
    from bb_webhook.config import get_settings
    return get_settings()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--statuses-json", type=Path, help="Offline status map {ticket_id: status}")
    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        raise SystemExit("--apply requires --backup")
    if args.apply:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from sqlite_backup import backup
        print(json.dumps({"backup": backup(args.source, args.backup)}))
    conn = connect(args.source)
    try:
        known = ticket_ids(conn)
        if args.statuses_json:
            raw = json.loads(args.statuses_json.read_text())
            statuses = {int(key): normalize_status(value) for key, value in raw.items()}
            statuses = {key: value for key, value in statuses.items() if value}
        else:
            cfg = settings()
            if not cfg.gorgias_api_email or not cfg.gorgias_api_key:
                raise SystemExit("Gorgias credentials are missing")
            statuses = fetch_statuses(cfg.gorgias_base_url, (cfg.gorgias_api_email, cfg.gorgias_api_key), known)
        updates = plan(conn, statuses)
        report = summarize(updates, len(statuses), len(known))
        report["mode"] = "apply" if args.apply else "dry-run"
        report["before_status_rows"] = conn.execute(
            "SELECT COUNT(*) FROM parsed_messages WHERE ticket_status IS NOT NULL AND ticket_status != ''"
        ).fetchone()[0]
        if args.apply:
            report["applied_rows"] = apply_updates(conn, updates)
            report["after_status_rows"] = conn.execute(
                "SELECT COUNT(*) FROM parsed_messages WHERE ticket_status IS NOT NULL AND ticket_status != ''"
            ).fetchone()[0]
        print(json.dumps(report, sort_keys=True))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
