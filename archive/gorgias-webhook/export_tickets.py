#!/usr/bin/env python3
"""
export_tickets.py — Export Gorgias tickets + conversations to CSV for KB use.

Fetches all tickets from the last N months (default 12), pulls every message
in each thread, and writes two CSV files:

  exports/messages_{label}.csv  — one row per message (primary KB format)
  exports/tickets_{label}.csv   — one row per ticket (summary + full thread)

Supports resume via a checkpoint file so large exports can survive restarts.

Usage:
  python3 export_tickets.py                          # last 12 months
  python3 export_tickets.py --months 6               # last 6 months
  python3 export_tickets.py --max-tickets 10         # smoke test
  python3 export_tickets.py --resume                 # continue interrupted run
  python3 export_tickets.py --label 12mo_2026-06-26  # custom output label

No external dependencies beyond gorgias_api.py (stdlib + cryptography for
encrypted config keys).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import gorgias_api

logger = logging.getLogger("export-tickets")

EXPORTS_DIR = os.path.join(SCRIPT_DIR, "exports")
STATE_DIR = os.path.join(EXPORTS_DIR, ".state")

MESSAGE_COLUMNS = [
    "ticket_id",
    "ticket_subject",
    "ticket_status",
    "ticket_priority",
    "ticket_channel",
    "ticket_tags",
    "ticket_created_at",
    "ticket_updated_at",
    "customer_id",
    "customer_name",
    "customer_email",
    "message_id",
    "message_seq",
    "message_created_at",
    "from_agent",
    "speaker",
    "channel",
    "is_public",
    "sender_name",
    "sender_email",
    "body_text",
]

TICKET_COLUMNS = [
    "ticket_id",
    "subject",
    "status",
    "priority",
    "channel",
    "tags",
    "created_at",
    "updated_at",
    "closed_at",
    "customer_id",
    "customer_name",
    "customer_email",
    "message_count",
    "customer_message_count",
    "agent_message_count",
    "first_customer_message",
    "last_agent_reply",
    "conversation_text",
]


def _load_credentials():
    """Reuse pipeline credential loader (handles encrypted API keys)."""
    from pipeline import _load_credentials as load
    return load()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def _tags_str(ticket: dict) -> str:
    tags = ticket.get("tags") or []
    names = []
    for tag in tags:
        if isinstance(tag, dict):
            names.append(str(tag.get("name") or tag.get("id") or ""))
        else:
            names.append(str(tag))
    return ", ".join(n for n in names if n)


def _customer_fields(ticket: dict) -> tuple:
    customer = ticket.get("customer") or {}
    return (
        customer.get("id"),
        customer.get("name") or "",
        customer.get("email") or "",
    )


def _speaker_label(from_agent: bool, channel: str, is_public: bool) -> str:
    if channel == "internal-note" or is_public is False:
        return "Internal"
    return "Agent" if from_agent else "Customer"


def _fetch_messages(base_url, username, api_key, ticket_id: int) -> list[dict]:
    try:
        data = gorgias_api.list_messages(base_url, username, api_key, ticket_id)
    except SystemExit:
        logger.warning("Failed to fetch messages for ticket #%s", ticket_id)
        return []
    except Exception as exc:
        logger.warning("Messages error ticket #%s: %s", ticket_id, exc)
        return []

    if isinstance(data, dict) and "data" in data:
        return data["data"] or []
    if isinstance(data, list):
        return data
    return []


def _message_rows(ticket: dict, messages: list[dict]) -> list[dict]:
    ticket_id = ticket.get("id")
    customer_id, customer_name, customer_email = _customer_fields(ticket)
    base = {
        "ticket_id": ticket_id,
        "ticket_subject": ticket.get("subject") or "",
        "ticket_status": ticket.get("status") or "",
        "ticket_priority": ticket.get("priority") or "",
        "ticket_channel": ticket.get("channel") or "",
        "ticket_tags": _tags_str(ticket),
        "ticket_created_at": ticket.get("created_datetime") or "",
        "ticket_updated_at": ticket.get("updated_datetime") or "",
        "customer_id": customer_id,
        "customer_name": customer_name,
        "customer_email": customer_email,
    }

    rows = []
    for seq, msg in enumerate(messages, start=1):
        from_agent = bool(msg.get("from_agent"))
        channel = msg.get("channel") or ""
        is_public = msg.get("public")
        if is_public is None:
            is_public = channel != "internal-note"
        sender = msg.get("sender") or {}
        rows.append({
            **base,
            "message_id": msg.get("id"),
            "message_seq": seq,
            "message_created_at": msg.get("created_datetime") or "",
            "from_agent": from_agent,
            "speaker": _speaker_label(from_agent, channel, is_public),
            "channel": channel,
            "is_public": is_public,
            "sender_name": sender.get("name") or "",
            "sender_email": sender.get("email") or "",
            "body_text": _clean_text(msg.get("body_text") or msg.get("stripped_text")),
        })
    return rows


def _ticket_summary_row(ticket: dict, messages: list[dict]) -> dict:
    customer_id, customer_name, customer_email = _customer_fields(ticket)
    customer_msgs = [m for m in messages if not m.get("from_agent")]
    agent_msgs = [m for m in messages if m.get("from_agent")]

    def body(m):
        return _clean_text(m.get("body_text") or m.get("stripped_text"))

    conversation_parts = []
    for msg in messages:
        from_agent = bool(msg.get("from_agent"))
        channel = msg.get("channel") or ""
        is_public = msg.get("public")
        if is_public is None:
            is_public = channel != "internal-note"
        label = _speaker_label(from_agent, channel, is_public)
        text = body(msg)
        if text:
            conversation_parts.append(f"[{label}] {text}")

    first_customer = body(customer_msgs[0]) if customer_msgs else ""
    last_agent = body(agent_msgs[-1]) if agent_msgs else ""

    return {
        "ticket_id": ticket.get("id"),
        "subject": ticket.get("subject") or "",
        "status": ticket.get("status") or "",
        "priority": ticket.get("priority") or "",
        "channel": ticket.get("channel") or "",
        "tags": _tags_str(ticket),
        "created_at": ticket.get("created_datetime") or "",
        "updated_at": ticket.get("updated_datetime") or "",
        "closed_at": ticket.get("closed_datetime") or "",
        "customer_id": customer_id,
        "customer_name": customer_name,
        "customer_email": customer_email,
        "message_count": len(messages),
        "customer_message_count": len(customer_msgs),
        "agent_message_count": len(agent_msgs),
        "first_customer_message": first_customer,
        "last_agent_reply": last_agent,
        "conversation_text": "\n\n---\n\n".join(conversation_parts),
    }


def _state_path(label: str) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, f"export_{label}.json")


def _load_state(label: str) -> dict:
    path = _state_path(label)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _save_state(label: str, state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(_state_path(label), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _open_csv(path: str, columns: list[str], resume: bool):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    mode = "a" if resume and exists else "w"
    fh = open(path, mode, encoding="utf-8", newline="")
    writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
    if mode == "w" or not exists:
        writer.writeheader()
    return fh, writer


def export_tickets(*, months: int, label: str, max_tickets: int | None,
                   resume: bool, sleep_sec: float) -> dict:
    base_url, username, api_key = _load_credentials()
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    cutoff_iso = cutoff.isoformat()

    messages_path = os.path.join(EXPORTS_DIR, f"messages_{label}.csv")
    tickets_path = os.path.join(EXPORTS_DIR, f"tickets_{label}.csv")

    state = _load_state(label) if resume else {}
    processed_ids = set(state.get("processed_ticket_ids") or [])
    tickets_exported = int(state.get("tickets_exported") or 0)
    messages_exported = int(state.get("messages_exported") or 0)
    errors = list(state.get("errors") or [])

    msg_fh, msg_writer = _open_csv(messages_path, MESSAGE_COLUMNS, resume)
    tkt_fh, tkt_writer = _open_csv(tickets_path, TICKET_COLUMNS, resume)

    logger.info(
        "Export started: months=%s cutoff=%s label=%s resume=%s already=%s tickets",
        months, cutoff_iso[:10], label, resume, tickets_exported,
    )

    try:
        for ticket in gorgias_api.iter_tickets_since(
            base_url, username, api_key, cutoff_iso, page_size=100,
        ):
            ticket_id = ticket.get("id")
            if ticket_id in processed_ids:
                continue
            if max_tickets is not None and tickets_exported >= max_tickets:
                logger.info("Reached --max-tickets=%s, stopping.", max_tickets)
                break

            messages = _fetch_messages(base_url, username, api_key, ticket_id)
            for row in _message_rows(ticket, messages):
                msg_writer.writerow(row)
                messages_exported += 1

            tkt_writer.writerow(_ticket_summary_row(ticket, messages))
            tickets_exported += 1
            processed_ids.add(ticket_id)

            if tickets_exported % 25 == 0:
                msg_fh.flush()
                tkt_fh.flush()
                _save_state(label, {
                    "cutoff_iso": cutoff_iso,
                    "months": months,
                    "processed_ticket_ids": sorted(processed_ids),
                    "tickets_exported": tickets_exported,
                    "messages_exported": messages_exported,
                    "messages_csv": messages_path,
                    "tickets_csv": tickets_path,
                    "errors": errors[-50:],
                    "status": "in_progress",
                })
                logger.info(
                    "Progress: %s tickets, %s messages",
                    tickets_exported, messages_exported,
                )

            if sleep_sec > 0:
                time.sleep(sleep_sec)

    finally:
        msg_fh.close()
        tkt_fh.close()

    final = {
        "status": "complete",
        "cutoff_iso": cutoff_iso,
        "months": months,
        "label": label,
        "tickets_exported": tickets_exported,
        "messages_exported": messages_exported,
        "messages_csv": messages_path,
        "tickets_csv": tickets_path,
        "errors": errors,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(label, {**final, "processed_ticket_ids": sorted(processed_ids)})
    return final


def main():
    parser = argparse.ArgumentParser(description="Export Gorgias tickets to CSV")
    parser.add_argument("--months", type=int, default=12,
                        help="How many months back to export (default: 12)")
    parser.add_argument("--label", default=None,
                        help="Output file label (default: {months}mo_YYYY-MM-DD)")
    parser.add_argument("--max-tickets", type=int, default=None,
                        help="Stop after N tickets (for testing)")
    parser.add_argument("--resume", action="store_true",
                        help="Append to existing CSVs and skip processed ticket IDs")
    parser.add_argument("--sleep", type=float, default=0.15,
                        help="Seconds to sleep between tickets (rate limit cushion)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    label = args.label or f"{args.months}mo_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    result = export_tickets(
        months=args.months,
        label=label,
        max_tickets=args.max_tickets,
        resume=args.resume,
        sleep_sec=args.sleep,
    )
    print(json.dumps(result, indent=2))
    if result.get("status") != "complete":
        sys.exit(1)


if __name__ == "__main__":
    main()
