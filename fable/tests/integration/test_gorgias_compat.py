"""Integration: Gorgias-compat layer at /api (STRATEGY §2.2).

These are the reads/writes the VPS tools use by swapping only their base URL.
"""
import pytest


@pytest.fixture
def three_tickets(env):
    e = env.intake_email("emma.wilson@example.com", "Where is my order #BB1015?").json()["ticket_id"]
    c = env.intake_chat("sess-gc", "Do you ship to Canada?").json()["ticket_id"]
    w = env.intake_whatsapp("+15550000000",
                            "damaged, refund!!").json()["ticket_id"]
    env.run_pipeline()
    return {"email": e, "chat": c, "whatsapp": w}


# --- list tickets (envelope) ------------------------------------------------
def test_list_tickets_envelope(env, three_tickets):
    r = env.client.get("/api/tickets")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert "next_cursor" in body["meta"] and "total_resources" in body["meta"]
    assert body["meta"]["total_resources"] == 3
    t = body["data"][0]
    # Gorgias field names present
    for field in ("created_datetime", "updated_datetime", "channel", "via",
                  "priority", "customer", "is_unread"):
        assert field in t


def test_list_tickets_limit_clamp(env, three_tickets):
    r = env.client.get("/api/tickets?limit=1")
    assert len(r.json()["data"]) == 1


# --- single ticket + messages -----------------------------------------------
def test_get_ticket_includes_messages(env, three_tickets):
    r = env.client.get(f"/api/tickets/{three_tickets['email']}")
    assert r.status_code == 200
    t = r.json()
    assert t["id"] == three_tickets["email"]
    assert isinstance(t["messages"], list) and len(t["messages"]) >= 1
    m = t["messages"][0]
    for field in ("created_datetime", "from_agent", "public", "body_text", "via", "channel"):
        assert field in m


def test_get_ticket_messages_endpoint(env, three_tickets):
    r = env.client.get(f"/api/tickets/{three_tickets['email']}/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) >= 1


def test_get_missing_ticket_is_404(env):
    assert env.client.get("/api/tickets/999999").status_code == 404


# --- customers --------------------------------------------------------------
def test_search_customers_by_email(env, three_tickets):
    r = env.client.get("/api/customers?email=emma.wilson@example.com")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    assert body["data"][0]["email"] == "emma.wilson@example.com"


def test_get_customer_by_id(env, three_tickets):
    cust_id = env.ticket(three_tickets["email"])["customer"]["id"]
    r = env.client.get(f"/api/customers/{cust_id}")
    assert r.status_code == 200
    assert r.json()["email"] == "emma.wilson@example.com"


def test_get_missing_customer_is_404(env):
    assert env.client.get("/api/customers/999999").status_code == 404


# --- POST internal note (the VPS writer path) -------------------------------
def test_post_internal_note(env, three_tickets):
    tid = three_tickets["email"]
    r = env.client.post(f"/api/tickets/{tid}/messages",
                        json={"channel": "internal", "body_text": "note from a tool"})
    assert r.status_code == 201
    m = r.json()
    assert m["public"] is False
    assert m["channel"] == "internal-note"
    assert m["from_agent"] is True
    assert m["body_text"] == "note from a tool"
    # it never leaves the system
    assert env.mailbox.get("/outbox").json()["count"] == 0
    # and it is audited
    actions = [a["action"] for a in env.audit.for_ticket(env.conn, tid)]
    assert "gorgias-compat:message" in actions


def test_post_note_missing_ticket_is_404(env):
    r = env.client.post("/api/tickets/999999/messages",
                        json={"channel": "internal", "body_text": "x"})
    assert r.status_code == 404


def test_all_three_tickets_listed(env, three_tickets):
    ids = {t["id"] for t in env.client.get("/api/tickets").json()["data"]}
    assert set(three_tickets.values()).issubset(ids)
