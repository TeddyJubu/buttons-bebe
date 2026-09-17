#!/usr/bin/env python3
"""Run the real processor/Hermes path against one synthetic Gorgias ticket."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
PROCESSOR = ROOT / "processor"
sys.path[:0] = [str(DEMO), str(PROCESSOR)]

from verify_config import load_env, validate  # noqa: E402


def _load_demo_environment() -> None:
    values = load_env(DEMO / ".env.example")
    errors = validate(values)
    if errors:
        raise RuntimeError("invalid demo environment: " + "; ".join(errors))
    # Remove every inherited client-integration variable before installing the
    # verified demo values. This covers legacy spellings as well as the current
    # client-credentials names without ever reading or printing their values.
    client_prefixes = (
        "SHOPIFY_",
        "GORGIAS_",
        "REDO_",
        "WHATSAPP_",
        "WA_",
        "TWILIO_",
        "FEEDBACK_",
    )
    for key in tuple(os.environ):
        if key.startswith(client_prefixes):
            os.environ.pop(key, None)
    os.environ.update(values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticket", type=int, default=61002)
    args = parser.parse_args(argv)

    _load_demo_environment()
    fixtures = json.loads((DEMO / "fixtures" / "gorgias.json").read_text())
    ticket = next(
        (item for item in fixtures["tickets"] if int(item["id"]) == args.ticket),
        None,
    )
    if ticket is None:
        raise SystemExit(f"synthetic ticket {args.ticket} not found")
    customer_messages = [
        message for message in ticket.get("messages", [])
        if not message.get("from_agent", False)
    ]
    if not customer_messages:
        raise SystemExit(f"synthetic ticket {args.ticket} has no customer message")
    latest = customer_messages[-1]

    import dotenv

    with patch.object(dotenv, "load_dotenv", lambda *_args, **_kwargs: False):
        import config
        import hermes_runner

    config.ProcessorSettings.model_config["env_file"] = None
    config.get_settings.cache_clear()   # lru_cache replaced the _settings = None reset
    result = hermes_runner.process_ticket_with_hermes(
        ticket_id=args.ticket,
        message_text=str(latest.get("body_text", "")),
        ticket_subject=str(ticket.get("subject", "")),
        customer_email=str(ticket.get("customer", {}).get("email", "")),
        intents=[],
    )

    safe_result = {
        key: result.get(key)
        for key in (
            "priority",
            "action",
            "reason",
            "notify_owner",
            "gorgias_priority_set",
            "note_posted",
            "draft_text",
            "no_draft",
        )
    }
    print(json.dumps(safe_result, indent=2, ensure_ascii=False))
    if result.get("gorgias_priority_set") or result.get("note_posted"):
        return 2
    if not result.get("draft_text"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
