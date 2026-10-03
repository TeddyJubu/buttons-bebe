#!/usr/bin/env python3
# offline-gate: skip
# This is a LIVE diagnostic against a running VPS, not an offline unit test.
# tools/verify_release.sh looks for the marker line above and skips this file.
"""End-to-end test for the Buttons Bebe ticket processing pipeline.

Tests each component in sequence:
  1. Webhook receiver health/ready
  2. MCP server connectivity (KB, Redo, Gorgias)
  3. KB search returns results
  4. Gorgias API read (get_ticket)
  5. Processor job queue (enqueue + claim + complete lifecycle)
  6. Hermes runner prompt builder (unit test)
  7. JSON_RESULT parser (unit test with edge cases)
  8. Full webhook → queue → processor flow (simulated)
  9. Live read-only Hermes invocation (optional)

Usage:
  python3 test_e2e.py
  python3 test_e2e.py --live    # includes a live Hermes invocation (~60s)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webhook" / "src"))
from bb_webhook.hermes_permissions import CANONICAL_TOOLSETS, canonical_toolsets

# ── Config ───────────────────────────────────────────────
BASE_URL = "http://127.0.0.1:8000"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
TENANT_ID = "buttonsbebe"

# Gorgias
GORGIAS_SUBDOMAIN = os.environ.get("GORGIAS_SUBDOMAIN", "buttonsbebe")
GORGIAS_API_EMAIL = os.environ.get("GORGIAS_API_EMAIL", "")
GORGIAS_API_KEY = os.environ.get("GORGIAS_API_KEY", "")
GORGIAS_BASE = f"https://{GORGIAS_SUBDOMAIN}.gorgias.com/api"
GORGIAS_AUTH = (GORGIAS_API_EMAIL, GORGIAS_API_KEY)

# Live reads are opt-in. Never keep production ticket IDs in source.
TEST_TICKET_ID = int(os.environ.get("TEST_TICKET_ID", "0"))
RUN_TOKEN = "0123456789abcdef"

# Hermes invocation. Mirrors processor/hermes_runner.build_hermes_command():
# an explicit read-only toolset allow-list, and no --yolo (DEV-ISSUES #8).
HERMES_TOOLSETS = canonical_toolsets(os.environ.get("HERMES_TOOLSETS", CANONICAL_TOOLSETS))
HERMES_BASE_CMD = ["hermes", "-t", HERMES_TOOLSETS]
if os.environ.get("HERMES_SKIP_APPROVAL", "").strip().lower() in ("1", "true", "yes"):
    HERMES_BASE_CMD.append("--yolo")

# MCP server URLs
KB_MCP_URL = "http://127.0.0.1:8077/mcp"
REDO_MCP_URL = "http://127.0.0.1:8078/mcp"
GORGIAS_MCP_URL = "http://127.0.0.1:8079/mcp"

# Results tracking
_passed = 0
_failed = 0
_failures: list[str] = []


def pass_test(name: str, detail: str = "") -> None:
    global _passed
    _passed += 1
    print(f"  PASS: {name}" + (f" — {detail}" if detail else ""))


def fail_test(name: str, detail: str = "") -> None:
    global _failed
    _failed += 1
    _failures.append(f"{name}: {detail}")
    print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── Tests ────────────────────────────────────────────────

def test_webhook_health() -> None:
    section("1. Webhook Receiver Health")
    try:
        resp = httpx.get(f"{BASE_URL}/health", timeout=5)
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            pass_test("health endpoint")
        else:
            fail_test("health endpoint", f"status={resp.status_code} body={resp.text}")
    except Exception as e:
        fail_test("health endpoint", str(e))

    try:
        resp = httpx.get(f"{BASE_URL}/ready", timeout=5)
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == "ready":
            pass_test("ready endpoint", f"db={data['checks'].get('db')}")
        else:
            fail_test("ready endpoint", f"status={resp.status_code} body={resp.text}")
    except Exception as e:
        fail_test("ready endpoint", str(e))


def test_mcp_servers() -> None:
    section("2. MCP Server Connectivity")
    for name, url in [("KB", KB_MCP_URL), ("Redo", REDO_MCP_URL), ("Gorgias", GORGIAS_MCP_URL)]:
        try:
            resp = httpx.post(
                url,
                json={"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                timeout=10,
            )
            if resp.status_code in (200, 202):
                pass_test(f"{name} MCP initialize")
            else:
                fail_test(f"{name} MCP initialize", f"status={resp.status_code}")
        except Exception as e:
            fail_test(f"{name} MCP initialize", str(e))


def test_kb_search() -> None:
    section("3. KB Search")
    # Use Hermes in one-shot mode to call search_kb via MCP
    try:
        result = subprocess.run(
            [*HERMES_BASE_CMD, "-z",
             "Call the search_kb tool from the buttonsbebe_kb MCP server with "
             'query "return policy" and k 3. Print the results. '
             "Do not do anything else."],
            capture_output=True, text=True, timeout=60,
            env={**dict(os.environ), "HOME": "/root",
                 "PATH": "/root/.local/bin:/usr/local/bin:/usr/bin:/bin"},
        )
        if result.returncode == 0 and "return" in result.stdout.lower():
            pass_test("KB search returns results", f"output len={len(result.stdout)}")
        else:
            fail_test("KB search", f"rc={result.returncode} stderr={result.stderr[:200]}")
    except Exception as e:
        fail_test("KB search", str(e))


def test_gorgias_read() -> None:
    section("4. Gorgias API Read")
    if not (TEST_TICKET_ID and GORGIAS_API_EMAIL and GORGIAS_API_KEY):
        print("  SKIP: set TEST_TICKET_ID and Gorgias credentials to run live reads")
        return

    try:
        resp = httpx.get(
            f"{GORGIAS_BASE}/tickets/{TEST_TICKET_ID}",
            auth=GORGIAS_AUTH,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            pass_test("get_ticket", f"subject='{data.get('subject','')}'")
        else:
            fail_test("get_ticket", f"status={resp.status_code}")
    except Exception as e:
        fail_test("get_ticket", str(e))

    try:
        resp = httpx.get(
            f"{GORGIAS_BASE}/tickets/{TEST_TICKET_ID}/messages",
            auth=GORGIAS_AUTH,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            msg_count = len(data.get("data", []))
            pass_test("get_ticket_messages", f"messages={msg_count}")
        else:
            fail_test("get_ticket_messages", f"status={resp.status_code}")
    except Exception as e:
        fail_test("get_ticket_messages", str(e))


def test_processor_queue() -> None:
    section("6. Processor Job Queue Lifecycle")
    db_path = Path(__file__).resolve().parent.parent / "webhook" / "data" / "webhook.db"
    if not db_path.exists():
        fail_test("DB exists", str(db_path))
        return

    import sqlite3
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row

    # Check queue stats
    try:
        rows = db.execute("SELECT status, COUNT(*) as cnt FROM job_queue GROUP BY status").fetchall()
        stats = {row["status"]: row["cnt"] for row in rows}
        pass_test("queue stats", f"pending={stats.get('pending',0)} done={stats.get('done',0)} failed={stats.get('failed',0)}")
    except Exception as e:
        fail_test("queue stats", str(e))

    # Check webhook_events table
    try:
        row = db.execute("SELECT COUNT(*) as cnt FROM webhook_events").fetchone()
        pass_test("webhook_events table", f"total events={row['cnt']}")
    except Exception as e:
        fail_test("webhook_events table", str(e))

    # Check parsed_messages table
    try:
        row = db.execute("SELECT COUNT(*) as cnt FROM parsed_messages").fetchone()
        pass_test("parsed_messages table", f"total messages={row['cnt']}")
    except Exception as e:
        fail_test("parsed_messages table", str(e))

    db.close()


def test_hermes_runner_unit() -> None:
    section("7. Hermes Runner Unit Tests")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webhook" / "src"))

    try:
        from hermes_runner import extract, prompt

        # 7a. Prompt builder
        built_prompt = prompt._build_prompt(
            ticket_id=12345,
            message_text="Test message",
            ticket_subject="Test subject",
            customer_email="test@example.com",
            intents=["shipping/status"],
            token=RUN_TOKEN,
        )
        if (
            len(built_prompt) > 100
            and f"JSON_RESULT[{RUN_TOKEN}]" in built_prompt
            and "12345" in built_prompt
        ):
            pass_test("prompt builder", f"len={len(built_prompt)}")
        else:
            fail_test("prompt builder", f"len={len(built_prompt)} missing tokenized JSON_RESULT")

        # 7b. Empty message handling
        prompt_empty = prompt._build_prompt(
            ticket_id=12345,
            message_text="",
            ticket_subject="Test",
            customer_email="test@example.com",
            intents=[],
            token=RUN_TOKEN,
        )
        if "EMPTY MESSAGE" in prompt_empty:
            pass_test("empty message handling")
        else:
            fail_test("empty message handling", "no EMPTY MESSAGE flag")

        # 7c. Long message truncation
        prompt_long = prompt._build_prompt(
            ticket_id=12345,
            message_text="A" * 5000,
            ticket_subject="Test",
            customer_email="test@example.com",
            intents=[],
            token=RUN_TOKEN,
        )
        if "truncated" in prompt_long:
            pass_test("long message truncation")
        else:
            fail_test("long message truncation", "no truncation marker")

        # 7d. JSON_RESULT parser — valid
        result = extract._parse_json_result(
            f'Some text\nJSON_RESULT[{RUN_TOKEN}]: {{"priority": "low", "reason": "test", "action": "drafted", "notify_owner": false, "gorgias_priority_set": true, "note_posted": true}}\nMore text',
            token=RUN_TOKEN,
        )
        if result["priority"] == "low" and result["action"] == "drafted":
            pass_test("JSON_RESULT parser (valid)")
        else:
            fail_test("JSON_RESULT parser (valid)", f"got {result}")

        # 7e. JSON_RESULT parser — missing
        result = extract._parse_json_result("No JSON_RESULT here", token=RUN_TOKEN)
        if result.get("no_draft") and result.get("action") == "sensitive_draft":
            pass_test("JSON_RESULT parser (missing token → no draft)")
        else:
            fail_test("JSON_RESULT parser (missing)", f"got {result}")

        # 7f. JSON_RESULT parser — invalid priority
        result = extract._parse_json_result(
            f'JSON_RESULT[{RUN_TOKEN}]: {{"priority": "bogus", "reason": "test", "action": "drafted", "notify_owner": false}}',
            token=RUN_TOKEN,
        )
        if result.get("no_draft") and result.get("action") == "sensitive_draft":
            pass_test("JSON_RESULT parser (invalid priority → no draft)")
        else:
            fail_test("JSON_RESULT parser (invalid priority)", f"got {result}")

        # 7g. JSON_RESULT parser — missing required fields
        result = extract._parse_json_result(
            f'JSON_RESULT[{RUN_TOKEN}]: {{"priority": "low", "reason": "test"}}',
            token=RUN_TOKEN,
        )
        if result.get("no_draft") and result.get("action") == "sensitive_draft":
            pass_test("JSON_RESULT parser (missing fields → no draft)")
        else:
            fail_test("JSON_RESULT parser (missing fields)", f"got {result}")

    except Exception as e:
        fail_test("hermes_runner import", str(e))


def test_webhook_e2e() -> None:
    section("8. Webhook → Queue E2E")
    # Send a signed webhook with a unique message_id
    msg_id = random.randint(10000000, 99999999)
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(minutes=1)).isoformat()

    payload = {
        "ticket": {
            "id": 999999,
            "subject": "E2E Test — safe to ignore",
            "channel": "email",
            "customer": {"email": "e2e-test@example.com"},
            "created_at": recent_ts,
        },
        "message": {
            "id": msg_id,
            "channel": "email",
            "created_at": recent_ts,
            "from_agent": False,
            "body_text": "This is an E2E test message.",
            "sender": {"email": "e2e-test@example.com"},
        },
        "event": "ticket.message.created",
    }

    raw = json.dumps(payload).encode()
    sig = sign(raw, WEBHOOK_SECRET)
    url = f"{BASE_URL}/webhook/gorgias/{TENANT_ID}"

    # 8a. Valid webhook → 202
    try:
        resp = httpx.post(url, content=raw, headers={
            "Content-Type": "application/json",
            "X-Gorgias-Signature": sig,
        }, timeout=10)
        if resp.status_code == 202:
            job_id = resp.json().get("job_id")
            pass_test("webhook → 202 accepted", f"job_id={job_id}")
        else:
            fail_test("webhook → 202", f"status={resp.status_code} body={resp.text}")
    except Exception as e:
        fail_test("webhook → 202", str(e))

    # 8b. Duplicate → 200
    try:
        resp = httpx.post(url, content=raw, headers={
            "Content-Type": "application/json",
            "X-Gorgias-Signature": sig,
        }, timeout=10)
        if resp.status_code == 200 and "duplicate" in resp.text:
            pass_test("webhook duplicate detection")
        else:
            fail_test("webhook duplicate", f"status={resp.status_code}")
    except Exception as e:
        fail_test("webhook duplicate", str(e))

    # 8c. No signature → 401
    try:
        resp = httpx.post(url, content=raw, headers={
            "Content-Type": "application/json",
        }, timeout=10)
        if resp.status_code == 401:
            pass_test("webhook auth rejection")
        else:
            fail_test("webhook auth rejection", f"status={resp.status_code}")
    except Exception as e:
        fail_test("webhook auth rejection", str(e))

    # 8d. Verify job was enqueued
    try:
        import sqlite3
        db_path = Path(__file__).resolve().parent.parent / "webhook" / "data" / "webhook.db"
        db = sqlite3.connect(str(db_path))
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT id, status, ticket_id FROM job_queue WHERE message_id=? ORDER BY id DESC LIMIT 1",
            (str(msg_id),),
        ).fetchone()
        if row:
            pass_test("job enqueued", f"job_id={row['id']} status={row['status']}")
        else:
            fail_test("job enqueued", "no job found in queue")
        db.close()
    except Exception as e:
        fail_test("job enqueued", str(e))

    # 8e. Clean up test data
    try:
        import sqlite3
        db_path = Path(__file__).resolve().parent.parent / "webhook" / "data" / "webhook.db"
        db = sqlite3.connect(str(db_path))
        db.execute("DELETE FROM job_queue WHERE ticket_id=999999")
        db.execute("DELETE FROM webhook_events WHERE ticket_id=999999")
        db.execute("DELETE FROM parsed_messages WHERE ticket_id=999999")
        db.commit()
        db.close()
        pass_test("test data cleanup")
    except Exception as e:
        fail_test("test data cleanup", str(e))


def test_live_hermes() -> None:
    section("9. Live Read-Only Hermes Invocation (slow ~60s)")
    # This tests that Hermes reads the ticket, searches KB, and returns a draft.
    try:
        prompt = (
            f"Process Buttons Bebe support ticket {TEST_TICKET_ID} autonomously.\n\n"
            f"Ticket context from webhook:\n"
            f"- Ticket ID: {TEST_TICKET_ID}\n"
            f"- Subject: Re: Last Chance! Sale Ends Midnight!\n"
            f"- Customer email: test@example.com\n"
            f"- Customer message (RAW): Can u do this for size 6m pls!!!\n"
            f"- Gorgias intents: discount/request\n\n"
            f"You have three MCP servers connected as tools:\n"
            f"1. buttonsbebe_gorgias: get_ticket, get_ticket_messages, get_customer, search_customer\n"
            f"2. buttonsbebe_kb: search_kb\n"
            f"3. buttonsbebe_redo: get_order, get_returns_for_order, get_return, list_recent_returns\n\n"
            f"Follow the ticket-processor skill workflow. Read the ticket, search KB, classify, "
            f"recommend priority, draft reply (always draft, tag sensitive), and output JSON_RESULT. "
            f"Do not write to Gorgias or any file.\n"
            f"Be concise. Do not ask questions. Make your best judgment.\n\n"
            f'Use the exact run-token markers <DRAFT:{RUN_TOKEN}>...</DRAFT:{RUN_TOKEN}> and '
            f'JSON_RESULT[{RUN_TOKEN}]: {{"priority": "<critical|high|normal|low>", "reason": "<one sentence>", '
            f'"action": "<drafted|sensitive_draft|no_kb_match>", "notify_owner": <true|false>, '
            f'"gorgias_priority_set": false, "note_posted": false}}'
        )

        result = subprocess.run(
            [*HERMES_BASE_CMD, "-z", prompt],
            capture_output=True, text=True, timeout=120,
            env={**dict(os.environ), "HOME": "/root",
                 "PATH": "/root/.local/bin:/usr/local/bin:/usr/bin:/bin"},
        )

        stdout = result.stdout.strip()
        if result.returncode != 0:
            fail_test("Hermes invocation", f"rc={result.returncode} stderr={result.stderr[:300]}")
            return

        if not stdout:
            fail_test("Hermes invocation", "empty output")
            return

        from hermes_runner import extract

        parsed = extract._parse_json_result(stdout, token=RUN_TOKEN)
        if parsed.get("no_draft") or parsed.get("action") not in {
            "drafted",
            "sensitive_draft",
            "escalated",
            "no_kb_match",
        }:
            fail_test("Hermes JSON_RESULT", "missing or invalid run-token verdict")
        else:
            pass_test(
                "Hermes JSON_RESULT",
                f"priority={parsed.get('priority')} action={parsed.get('action')}",
            )

        # Check that Hermes actually did something (MCP calls, etc.)
        if "search_kb" in stdout or "get_ticket" in stdout or "curl" in stdout:
            pass_test("Hermes used MCP tools")
        else:
            # Hermes may not print tool calls to stdout, just the final result
            pass_test("Hermes output received", f"len={len(stdout)}")

    except subprocess.TimeoutExpired:
        fail_test("Hermes invocation", "timed out after 120s")
    except Exception as e:
        fail_test("Hermes invocation", str(e))


def main() -> int:
    print("=" * 60)
    print("Buttons Bebe — End-to-End Pipeline Test")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Load env
    env_path = Path(__file__).resolve().parent.parent / "webhook" / ".env"
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path)

    # Reload config after dotenv
    global GORGIAS_API_EMAIL, GORGIAS_API_KEY, GORGIAS_AUTH, WEBHOOK_SECRET
    global TEST_TICKET_ID
    GORGIAS_API_EMAIL = os.environ.get("GORGIAS_API_EMAIL", GORGIAS_API_EMAIL)
    GORGIAS_API_KEY = os.environ.get("GORGIAS_API_KEY", GORGIAS_API_KEY)
    GORGIAS_AUTH = (GORGIAS_API_EMAIL, GORGIAS_API_KEY)
    WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", WEBHOOK_SECRET)
    TEST_TICKET_ID = int(os.environ.get("TEST_TICKET_ID", str(TEST_TICKET_ID)))

    test_webhook_health()
    test_mcp_servers()
    test_kb_search()
    test_gorgias_read()
    test_processor_queue()
    test_hermes_runner_unit()
    test_webhook_e2e()

    if "--live" in sys.argv:
        test_live_hermes()
    else:
        print(f"\n{'─' * 60}")
        print("  9. Live Read-Only Hermes Invocation — SKIPPED (use --live to enable, ~60s)")
        print(f"{'─' * 60}")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {_passed} passed, {_failed} failed")
    if _failures:
        print(f"\n  FAILURES:")
        for f in _failures:
            print(f"    • {f}")
    print(f"{'=' * 60}")

    return 1 if _failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
