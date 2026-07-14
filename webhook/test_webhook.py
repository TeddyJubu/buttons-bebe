#!/usr/bin/env python3
"""Test script for the webhook receiver.

Sends three requests:
  1. No signature → expect 401
  2. Valid signature + valid payload → expect 202
  3. Same payload again (duplicate) → expect 200 "duplicate"

Usage:
  python3 test_webhook.py
"""

import hashlib
import hmac
import json
import os
import random
import sys
from datetime import datetime, timezone, timedelta

import httpx

# ── Config ───────────────────────────────────────────────
BASE_URL = os.environ.get("WEBHOOK_BASE_URL", "http://127.0.0.1:8000")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
TENANT_ID = "buttonsbebe"

# ── Sample Gorgias webhook payload ────────────────────────
# Use a timestamp that's 2 minutes ago (within the 10-minute replay window)
# and a unique message_id each run to avoid the duplicate check.
_NOW = datetime.now(timezone.utc)
_RECENT_TS = (_NOW - timedelta(minutes=2)).isoformat()
_UNIQUE_MSG_ID = random.randint(1000000, 9999999)

SAMPLE_PAYLOAD = {
    "ticket": {
        "id": 123456,
        "subject": "Do you ship to Canada?",
        "channel": "email",
        "customer": {"email": "[REDACTED]@example.com"},
        "created_at": _RECENT_TS,
    },
    "message": {
        "id": _UNIQUE_MSG_ID,
        "channel": "email",
        "created_at": _RECENT_TS,
        "from_agent": False,
        "body_text": "Hi, do you ship to Canada? Thanks!",
        "sender": {"email": "[REDACTED]@example.com"},
    },
    "event": "ticket.message.created",
}


def sign(body: bytes, secret: str) -> str:
    """Compute the HMAC-SHA256 signature Gorgias would send."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def main() -> int:
    if not WEBHOOK_SECRET:
        print("WEBHOOK_SECRET must be set before running the live webhook test.", file=sys.stderr)
        return 2

    raw = json.dumps(SAMPLE_PAYLOAD).encode()
    sig = sign(raw, WEBHOOK_SECRET)
    url = f"{BASE_URL}/webhook/gorgias/{TENANT_ID}"

    print("=" * 60)
    print("Webhook Receiver Test")
    print("=" * 60)

    # ── Test 1: No signature (should be 401) ───────────────
    print("\n1. POST without signature → expect 401")
    resp = httpx.post(url, content=raw, headers={"Content-Type": "application/json"})
    print(f"   Status: {resp.status_code} (expected 401)")
    print(f"   Body:   {resp.text}")
    assert resp.status_code == 401, "FAIL: expected 401"
    print("   PASS")

    # ── Test 2: Valid signature (should be 202) ───────────
    print("\n2. POST with valid signature → expect 202")
    resp = httpx.post(url, content=raw, headers={
        "Content-Type": "application/json",
        "X-Gorgias-Signature": sig,
    })
    print(f"   Status: {resp.status_code} (expected 202)")
    print(f"   Body:   {resp.text}")
    assert resp.status_code == 202, "FAIL: expected 202"
    print("   PASS")

    # ── Test 3: Duplicate (should be 200 "duplicate") ─────
    print("\n3. POST same payload again → expect 200 duplicate")
    resp = httpx.post(url, content=raw, headers={
        "Content-Type": "application/json",
        "X-Gorgias-Signature": sig,
    })
    print(f"   Status: {resp.status_code} (expected 200)")
    print(f"   Body:   {resp.text}")
    assert resp.status_code == 200, "FAIL: expected 200"
    assert "duplicate" in resp.text, "FAIL: expected 'duplicate' in response"
    print("   PASS")

    # ── Test 4: Wrong signature (should be 401) ───────────
    print("\n4. POST with wrong signature → expect 401")
    resp = httpx.post(url, content=raw, headers={
        "Content-Type": "application/json",
        "X-Gorgias-Signature": "deadbeef" * 8,
    })
    print(f"   Status: {resp.status_code} (expected 401)")
    assert resp.status_code == 401, "FAIL: expected 401"
    print("   PASS")

    # ── Test 5: Health check ───────────────────────────────
    print("\n5. GET /health → expect 200")
    resp = httpx.get(f"{BASE_URL}/health")
    print(f"   Status: {resp.status_code} (expected 200)")
    print(f"   Body:   {resp.text}")
    assert resp.status_code == 200, "FAIL: expected 200"
    print("   PASS")

    print("\n" + "=" * 60)
    print("All tests PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
