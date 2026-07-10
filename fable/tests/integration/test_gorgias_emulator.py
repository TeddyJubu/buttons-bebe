"""Integration: the Gorgias API emulator (Stream R / R1).

Proves the emulator behaves like the real Gorgias read API: HTTP Basic auth,
cursor pagination that walks to completion, Gorgias-shaped fields, and messages
that include internal notes. Runs fully in-process via a Starlette TestClient —
no sockets.
"""
import base64
import importlib.util
import pathlib
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

EMU_PATH = pathlib.Path(__file__).resolve().parents[2] / "emulators" / "gorgias" / "app.py"

GOOD_EMAIL = "agent@buttonsbebe.com"
GOOD_KEY = "test-gorgias-key"


def _load_gorgias():
    """Import the emulator app.py fresh under a unique module name (avoids the
    clash with the server's own top-level ``app`` package and keeps each test
    isolated)."""
    name = f"fable_emu_gorgias_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, EMU_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _auth_header(email=GOOD_EMAIL, key=GOOD_KEY):
    token = base64.b64encode(f"{email}:{key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def emu():
    return _load_gorgias()


@pytest.fixture
def client(emu):
    return TestClient(emu.app, headers=_auth_header())


# --- auth -------------------------------------------------------------------
def test_no_auth_is_401(emu):
    bare = TestClient(emu.app)
    r = bare.get("/api/tickets")
    assert r.status_code == 401
    assert "error" in r.json()  # Gorgias-shaped error body


def test_wrong_credentials_is_401(emu):
    bad = TestClient(emu.app, headers=_auth_header(key="nope"))
    r = bad.get("/api/tickets")
    assert r.status_code == 401


def test_good_credentials_ok(client):
    r = client.get("/api/tickets")
    assert r.status_code == 200
    assert r.json()["object"] == "list"


def test_health_and_reset_need_no_auth(emu):
    bare = TestClient(emu.app)
    assert bare.get("/health").status_code == 200
    assert bare.post("/emulator/reset").status_code == 200


# --- pagination -------------------------------------------------------------
def test_list_envelope_shape(client, emu):
    body = client.get("/api/tickets").json()
    assert body["object"] == "list"
    assert "next_cursor" in body["meta"]
    assert "total_resources" in body["meta"]
    assert body["meta"]["total_resources"] == len(emu.STATE["tickets"])


def test_limit_is_respected_and_clamped(client):
    assert len(client.get("/api/tickets", params={"limit": 5}).json()["data"]) == 5
    # over-max clamps to 100 (we have fewer than 100 tickets, so all are returned)
    assert len(client.get("/api/tickets", params={"limit": 9999}).json()["data"]) <= 100


def test_cursor_walks_every_ticket_exactly_once(client, emu):
    seen = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 4}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/api/tickets", params=params).json()
        seen.extend(t["id"] for t in body["data"])
        pages += 1
        cursor = body["meta"]["next_cursor"]
        if not cursor:
            break
        assert pages < 100, "pagination did not terminate"

    expected = sorted(t["id"] for t in emu.STATE["tickets"])
    assert sorted(seen) == expected
    assert len(seen) == len(set(seen)) == len(expected)  # no dupes, no gaps
    assert pages > 1  # actually paginated across multiple pages


# --- shapes -----------------------------------------------------------------
def test_ticket_fields_match_gorgias(client):
    t = client.get("/api/tickets").json()["data"][0]
    for field in ("id", "status", "channel", "via", "priority", "subject",
                  "customer", "created_datetime", "updated_datetime",
                  "last_message_datetime", "is_unread", "external_id"):
        assert field in t, f"missing ticket field: {field}"
    assert t["via"] == "api"
    assert "T" in t["created_datetime"]  # ISO 8601


def test_single_ticket_includes_messages(client):
    t = client.get("/api/tickets/6001").json()
    assert t["id"] == 6001
    assert isinstance(t["messages"], list) and len(t["messages"]) >= 1
    m = t["messages"][0]
    for field in ("id", "ticket_id", "public", "channel", "via", "from_agent",
                  "body_text", "created_datetime"):
        assert field in m


def test_unknown_ticket_is_404(client):
    assert client.get("/api/tickets/999999").status_code == 404


def test_messages_endpoint_paginates(client):
    body = client.get("/api/tickets/6001/messages").json()
    assert body["object"] == "list"
    assert len(body["data"]) >= 1
    assert all(m["ticket_id"] == 6001 for m in body["data"])


def test_messages_include_internal_notes(client):
    # ticket 6004 (damaged/refund) carries an internal note.
    msgs = client.get("/api/tickets/6004/messages").json()["data"]
    notes = [m for m in msgs if not m["public"]]
    assert notes, "expected at least one internal note"
    note = notes[0]
    assert note["channel"] == "internal-note"
    assert note["from_agent"] is True
    assert note["receiver"] is None


def test_public_reply_has_receiver_and_from_agent(client):
    msgs = client.get("/api/tickets/6001/messages").json()["data"]
    replies = [m for m in msgs if m["public"] and m["from_agent"]]
    assert replies
    assert replies[0]["receiver"] is not None
    incoming = [m for m in msgs if not m["from_agent"]]
    assert incoming and incoming[0]["public"] is True


# --- customers --------------------------------------------------------------
def test_customer_search_by_email(client):
    body = client.get("/api/customers", params={"email": "emma.wilson@example.com"}).json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    assert body["data"][0]["email"] == "emma.wilson@example.com"


def test_customer_by_id(client):
    c = client.get("/api/customers/5001").json()
    assert c["id"] == 5001
    assert c["email"] == "emma.wilson@example.com"


def test_unknown_customer_is_404(client):
    assert client.get("/api/customers/999999").status_code == 404


def test_customers_list_paginates_all(client, emu):
    seen = []
    cursor = None
    while True:
        params = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/api/customers", params=params).json()
        seen.extend(c["id"] for c in body["data"])
        cursor = body["meta"]["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(c["id"] for c in emu.STATE["customers"])


def test_reset_reseeds(emu):
    bare = TestClient(emu.app)
    # mutate then reset
    emu.STATE["tickets"].pop()
    bare.post("/emulator/reset")
    state = bare.get("/emulator/state").json()
    assert state["tickets"] == 15
    assert state["customers"] == 10
