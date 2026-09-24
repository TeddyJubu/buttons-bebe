#!/usr/bin/env python3
"""
Test script for the Gorgias Webhook Receiver.
Starts the server, sends test webhook payloads, and verifies responses.
"""

import json
import time
import threading
import urllib.request
import urllib.error
import subprocess
import sys
import os
import signal

SERVER_URL = "http://localhost:8080"

def wait_for_server(timeout=10):
    for _ in range(timeout * 10):
        try:
            resp = urllib.request.urlopen(f"{SERVER_URL}/health", timeout=2)
            if resp.status == 200:
                return True
        except (urllib.error.URLError, OSError, ConnectionRefusedError):
            time.sleep(0.1)
    return False

def get_config_secret():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path) as f:
        return json.load(f).get("secret_token", "")

def send_webhook(event_type, payload, secret=None):
    url = f"{SERVER_URL}/webhook"
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    # Always send the configured secret unless an explicit override is given
    if secret is not None:
        headers["X-Webhook-Secret"] = secret
    else:
        cfg_secret = get_config_secret()
        if cfg_secret:
            headers["X-Webhook-Secret"] = cfg_secret
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def main():
    # Start the server in a subprocess
    print("Starting server...")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), "server.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        # Wait for server to be ready
        if not wait_for_server():
            print("FAIL: Server did not start in time")
            proc.kill()
            proc.wait()
            sys.exit(1)

        print("Server is running. Running tests...\n")

        # Test 1: Health check
        print("=" * 50)
        print("TEST 1: Health check (GET /health)")
        resp = urllib.request.urlopen(f"{SERVER_URL}/health")
        data = json.loads(resp.read())
        assert resp.status == 200, f"Expected 200, got {resp.status}"
        assert data["status"] == "ok", f"Expected status 'ok', got {data['status']}"
        print(f"  PASS - Status {resp.status}, status={data['status']}")

        # Test 2: Test page
        print("\nTEST 2: Test page (GET /test)")
        resp = urllib.request.urlopen(f"{SERVER_URL}/test")
        assert resp.status == 200, f"Expected 200, got {resp.status}"
        body = resp.read().decode()
        assert "Gorgias Webhook Receiver" in body
        print(f"  PASS - Status {resp.status}, HTML returned")

        # Test 3: Ticket created webhook (with configured secret)
        print("\nTEST 3: Ticket created webhook (POST /webhook, with config secret)")
        payload = {
            "event": "ticket.created",
            "data": {
                "id": 123456,
                "subject": "Customer needs help with order #1001",
                "customer": {"email": "customer@example.com"},
                "messages": [],
            },
        }
        status, resp_data = send_webhook("ticket.created", payload)
        assert status == 200, f"Expected 200, got {status}"
        assert resp_data.get("status") == "received", f"Unexpected body: {resp_data}"
        assert resp_data.get("event") is not None, "Response missing 'event' field"
        print(f"  PASS - Status {status}, event={resp_data['event']}")

        # Test 4: Message created webhook (agent reply)
        print("\nTEST 4: Message created webhook (POST /webhook)")
        payload = {
            "event": "ticket.message.created",
            "data": {
                "id": 789,
                "ticket_id": 123456,
                "from_agent": True,
                "body_text": "Hi! Your order has been shipped.",
                "sender": {"email": "agent@example.com"},
            },
        }
        status, resp_data = send_webhook("ticket.message.created", payload)
        assert status == 200, f"Expected 200, got {status}"
        assert resp_data.get("status") == "received", f"Unexpected body: {resp_data}"
        assert resp_data.get("event") is not None, "Response missing 'event' field"
        print(f"  PASS - Status {status}, event={resp_data['event']}")

        # Test 5: Invalid JSON
        print("\nTEST 5: Invalid JSON body (POST /webhook)")
        url = f"{SERVER_URL}/webhook"
        # Send the correct secret so we get past auth, then hit JSON parse failure
        cfg_secret = get_config_secret()
        bad_headers = {"Content-Type": "application/json"}
        if cfg_secret:
            bad_headers["X-Webhook-Secret"] = cfg_secret
        req = urllib.request.Request(url, data=b"not json", headers=bad_headers, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "Should have gotten 400"
        except urllib.error.URLError as e:
            if not isinstance(e, urllib.error.HTTPError):
                raise
            assert e.code == 400, f"Expected 400, got {e.code}"
            print(f"  PASS - Status {e.code} (bad JSON rejected)")

        # Test 6: 404 for unknown path
        print("\nTEST 6: Unknown path (GET /nonexistent)")
        try:
            urllib.request.urlopen(f"{SERVER_URL}/nonexistent")
            assert False
        except urllib.error.URLError as e:
            if not isinstance(e, urllib.error.HTTPError):
                raise
            assert e.code == 404, f"Expected 404, got {e.code}"
            print(f"  PASS - Status {e.code}")

        # Test 7: Secret token validation
        print("\nTEST 7: Secret token validation")
        # Read config to check if secret is set
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path) as f:
            config = json.load(f)
        secret = config.get("secret_token", "")

        if not secret or secret.startswith("change-this"):
            print("FAIL: secret_token is not configured — cannot verify auth contract.")
            print("      Set a real secret_token in config.json before running tests.")
            sys.exit(1)

        _auth_tests_ran = True
        # Test with wrong secret
        payload = {"event": "ticket.created", "data": {"id": 999}}
        status, _ = send_webhook("ticket.created", payload, secret="wrong-secret")
        assert status == 401, f"Expected 401, got {status}"
        print(f"  PASS - Wrong secret rejected with {status}")

        # Test with correct secret
        status, resp_data = send_webhook("ticket.created", payload, secret=secret)
        assert status == 200, f"Expected 200, got {status}"
        print(f"  PASS - Correct secret accepted with {status}")

        # /api/kb-review POST must also require the secret (auth bypass fix).
        url = f"{SERVER_URL}/api/kb-review/approve"
        req = urllib.request.Request(
            url,
            data=json.dumps({"id": 1}).encode(),
            headers={"Content-Type": "application/json", "X-Webhook-Secret": "wrong-secret"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "/api/kb-review POST with wrong secret must be 401"
        except urllib.error.URLError as e:
            if not isinstance(e, urllib.error.HTTPError):
                raise
            assert e.code == 401, f"Expected 401 for kb-review POST with wrong secret, got {e.code}"
        print(f"  PASS - /api/kb-review POST with wrong secret rejected with 401")

        # GET /api/kb-review must also require auth (do_GET auth fix).
        get_req = urllib.request.Request(
            f"{SERVER_URL}/api/kb-review",
            headers={"X-Webhook-Secret": "wrong-secret"},
            method="GET",
        )
        try:
            urllib.request.urlopen(get_req, timeout=5)
            assert False, "GET /api/kb-review with wrong secret must be 401"
        except urllib.error.URLError as e:
            if not isinstance(e, urllib.error.HTTPError):
                raise
            assert e.code == 401, f"Expected 401 for GET /api/kb-review with wrong secret, got {e.code}"
        print(f"  PASS - GET /api/kb-review with wrong secret rejected with 401")

        print("\n" + "=" * 50)
        print("ALL TESTS PASSED")
        print("=" * 50)

        # Verify JSONL log was created
        jsonl_path = os.path.join(os.path.dirname(__file__), "webhook_events.jsonl")
        if os.path.exists(jsonl_path):
            with open(jsonl_path) as f:
                lines = f.readlines()
            print(f"\nJSONL log: {len(lines)} events logged at {jsonl_path}")

    finally:
        print("\nShutting down test server...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

if __name__ == "__main__":
    main()