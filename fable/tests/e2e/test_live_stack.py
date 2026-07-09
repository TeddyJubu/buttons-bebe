"""End-to-end live-stack tests (TESTING-STRATEGY §2.3, API-CONTRACT §7).

Marked ``@pytest.mark.e2e`` — skipped unless ``FABLE_E2E=1``. Boots the real four
services (real uvicorn, real HTTP on 127.0.0.1) via the ``scripts/run-*.sh``
launchers with the DB on ``/tmp``, runs the demo scenario, and checks
kill-emulator resilience. Everything is torn down at the end.

Sandbox note: some environments set HTTP(S)/SOCKS proxy vars. This process talks
to the stack with a ``trust_env=False`` httpx client and the launcher scripts'
own curl health checks honour ``no_proxy=127.0.0.1`` — so every call stays direct
on localhost.
"""
import os
import pathlib
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e

FABLE_DIR = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = FABLE_DIR / "scripts"
EMUS = FABLE_DIR / "emulators"

FABLE = "http://127.0.0.1:9600"
SHOPIFY = "http://127.0.0.1:9601"
REDO = "http://127.0.0.1:9602"
MAILBOX = "http://127.0.0.1:9603"


def _stack_env(**extra):
    env = dict(os.environ)
    env.update(extra)
    return env


def _wait_health(client, url, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.get(url + "/health", timeout=2.0).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


@pytest.fixture(scope="module")
def stack():
    db = "/tmp/fable_e2e.db"
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(db + suffix)
        except OSError:
            pass
    env = _stack_env(FABLE_DB=db, FABLE_HOST="127.0.0.1", FABLE_PORT="9600")

    # Boot the whole stack (emulators + server). run-all.sh nohup's the processes.
    subprocess.run(["bash", str(SCRIPTS / "run-all.sh")], env=env,
                   capture_output=True, text=True, timeout=40)

    client = httpx.Client(trust_env=False)
    healthy = all([
        _wait_health(client, FABLE),
        _wait_health(client, SHOPIFY),
        _wait_health(client, REDO),
        _wait_health(client, MAILBOX),
    ])

    def poll_draft(ticket_id, timeout=12.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = client.get(f"{FABLE}/fable/api/tickets/{ticket_id}", timeout=3.0).json()["ticket"]
            if t.get("draft"):
                return t
            time.sleep(0.3)
        return client.get(f"{FABLE}/fable/api/tickets/{ticket_id}", timeout=3.0).json()["ticket"]

    ns = type("Stack", (), {})()
    ns.client = client
    ns.healthy = healthy
    ns.poll_draft = poll_draft
    ns.env = env

    try:
        yield ns
    finally:
        client.close()
        subprocess.run(["bash", str(SCRIPTS / "stop-server.sh")], env=env,
                       capture_output=True, text=True)
        subprocess.run(["bash", str(EMUS / "stop-emulators.sh")], env=env,
                       capture_output=True, text=True)
        # belt-and-suspenders in case a restarted shopify proc lingers
        subprocess.run("pkill -f 'emulators/shopify/app[.]py' 2>/dev/null || true",
                       shell=True, env=env)


def test_all_services_healthy(stack):
    assert stack.healthy, "not all four services reported healthy"


def test_demo_scenario(stack):
    assert stack.healthy
    c = stack.client

    # 2. Email: Emma asks about #BB1015 -> low-risk draft with real tracking.
    inc = c.post(f"{MAILBOX}/simulate/incoming", json={
        "from_email": "emma.wilson@example.com", "from_name": "Emma Wilson",
        "subject": "Where is my order?", "body_text": "Where is my order #BB1015?"},
        timeout=5.0).json()
    assert inc["forwarded"] is True
    email_tid = inc["ticket_id"]
    t_email = stack.poll_draft(email_tid)
    assert t_email["draft"] is not None
    assert "1Z999AA10123456784" in t_email["draft"]["body_text"]  # real Shopify context
    assert t_email["draft"]["risk"] == "low"

    # 3. Chat: ship to Canada? -> draft.
    chat_tid = c.post(f"{FABLE}/fable/api/intake/chat", json={
        "session_id": "e2e-chat", "name": "Nora", "body_text": "Do you ship to Canada?"},
        timeout=5.0).json()["ticket_id"]
    t_chat = stack.poll_draft(chat_tid)
    assert t_chat["draft"] is not None

    # 4. WhatsApp: damaged + refund -> SENSITIVE + careful draft.
    wa_tid = c.post(f"{FABLE}/fable/api/intake/whatsapp", json={
        "phone": "+15558231838", "name": "Emma",
        "body_text": "My order arrived damaged, I want a refund!!"}, timeout=5.0).json()["ticket_id"]
    t_wa = stack.poll_draft(wa_tid)
    assert t_wa["sensitive"] is True
    assert t_wa["draft"]["risk"] == "sensitive"
    assert "refund" not in t_wa["draft"]["body_text"].lower()  # no promises

    # 5. Console verbs.
    c.delete(f"{MAILBOX}/outbox", timeout=5.0)  # start from a clean outbox
    send = c.post(f"{FABLE}/fable/api/tickets/{email_tid}/send",
                  json={"text": t_email["draft"]["body_text"]}, timeout=5.0)
    assert send.status_code == 200
    outbox = c.get(f"{MAILBOX}/outbox", timeout=5.0).json()
    assert outbox["count"] == 1
    assert outbox["outbox"][0]["to"] == "emma.wilson@example.com"

    note = c.post(f"{FABLE}/fable/api/tickets/{chat_tid}/note",
                  json={"text": "internal: shipping policy sent"}, timeout=5.0)
    assert note.status_code == 200 and note.json()["message"]["public"] is False

    rewrite = c.post(f"{FABLE}/fable/api/tickets/{wa_tid}/rewrite",
                     json={"instruction": "make it friendlier"}, timeout=5.0)
    assert rewrite.status_code == 200
    assert rewrite.json()["draft"]["status"] == "proposed"

    # 6. Gorgias-compat lists all three tickets.
    gc = c.get(f"{FABLE}/api/tickets", timeout=5.0).json()
    ids = {t["id"] for t in gc["data"]}
    assert {email_tid, chat_tid, wa_tid}.issubset(ids)
    assert gc["object"] == "list"


def test_kill_emulator_resilience(stack):
    assert stack.healthy
    c = stack.client

    # kill the Shopify emulator mid-run
    subprocess.run("pkill -f 'emulators/shopify/app[.]py' 2>/dev/null || true",
                   shell=True, env=stack.env)
    time.sleep(0.8)
    # confirm it's actually down
    down = False
    try:
        c.get(f"{SHOPIFY}/health", timeout=1.5)
    except Exception:
        down = True
    assert down, "shopify emulator did not go down"

    # a new intake STILL drafts, with Shopify context degraded (no orders).
    tid = c.post(f"{FABLE}/fable/api/intake/email", json={
        "from_email": "degraded@example.com", "body_text": "hello, any update on my order?"},
        timeout=5.0).json()["ticket_id"]
    t = stack.poll_draft(tid)
    assert t["draft"] is not None            # never crashes a ticket
    # Shopify is down (Redo may still answer), so there are no orders in context.
    oc = t["order_context"]
    assert oc is None or oc.get("orders") == [], "Shopify context should be degraded"

    # restart Shopify, context returns.
    time.sleep(1.0)  # let the port free after the kill
    with open("/tmp/fable_e2e_shopify_restart.log", "w") as logf:
        subprocess.Popen(
            ["python3", str(EMUS / "shopify" / "app.py")], env=stack.env,
            stdout=logf, stderr=subprocess.STDOUT)
    assert _wait_health(c, SHOPIFY, timeout=15.0), "shopify did not come back"

    tid2 = c.post(f"{FABLE}/fable/api/intake/email", json={
        "from_email": "emma.wilson@example.com",
        "body_text": "any update on order #BB1015?"}, timeout=5.0).json()["ticket_id"]
    t2 = stack.poll_draft(tid2)
    assert t2["order_context"] is not None
    assert "1Z999AA10123456784" in t2["draft"]["body_text"]


def test_rate_limit_storm_does_not_crash_tickets(stack):
    """A rate-limit storm against Shopify (X-Emulator-Scenario) proves the
    emulator 429s, and the pipeline keeps drafting tickets (never crashes)."""
    assert stack.healthy
    c = stack.client
    tok = c.post(f"{SHOPIFY}/admin/oauth/access_token", json={
        "client_id": "test-client-id", "client_secret": "test-client-secret",
        "grant_type": "client_credentials"}, timeout=5.0).json()["access_token"]
    r = c.get(f"{SHOPIFY}/admin/api/2026-07/orders.json?status=any",
              headers={"X-Shopify-Access-Token": tok, "X-Emulator-Scenario": "rate-limit"},
              timeout=5.0)
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "2.0"

    # a fresh ticket still gets a draft
    tid = c.post(f"{FABLE}/fable/api/intake/email", json={
        "from_email": "storm@example.com", "body_text": "just checking in"}, timeout=5.0).json()["ticket_id"]
    t = stack.poll_draft(tid)
    assert t["draft"] is not None
