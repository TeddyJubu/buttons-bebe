"""Integration: Fable native Tickets API (STRATEGY §2.2)."""
import pytest


@pytest.fixture
def seeded(env):
    """Three tickets across channels; one sensitive."""
    t_email = env.intake_email("emma.wilson@example.com",
                               "Where is my order #BB1015?").json()["ticket_id"]
    t_chat = env.intake_chat("sess-canada", "Do you ship to Canada?").json()["ticket_id"]
    t_wa = env.intake_whatsapp("+15550000000",
                               "My order arrived damaged, I want a refund!!").json()["ticket_id"]
    env.run_pipeline()
    return {"email": t_email, "chat": t_chat, "whatsapp": t_wa}


def test_list_all_and_counts(env, seeded):
    data = env.client.get("/fable/api/tickets?status=all").json()
    assert len(data["tickets"]) == 3
    counts = data["counts"]
    assert counts["open"] == 3
    assert counts["sensitive_open"] == 1
    assert counts["closed"] == 0


def test_filter_by_channel(env, seeded):
    data = env.client.get("/fable/api/tickets?channel=whatsapp").json()
    assert len(data["tickets"]) == 1
    assert data["tickets"][0]["channel"] == "whatsapp"


def test_filter_by_sensitive(env, seeded):
    data = env.client.get("/fable/api/tickets?sensitive=true").json()
    assert len(data["tickets"]) == 1
    assert data["tickets"][0]["sensitive"] is True
    assert data["tickets"][0]["sensitive_reason"]


def test_filter_by_status(env, seeded):
    env.client.patch(f"/fable/api/tickets/{seeded['chat']}", json={"status": "closed"})
    open_data = env.client.get("/fable/api/tickets?status=open").json()
    closed_data = env.client.get("/fable/api/tickets?status=closed").json()
    assert len(open_data["tickets"]) == 2
    assert len(closed_data["tickets"]) == 1


def test_search_q_matches_body(env, seeded):
    data = env.client.get("/fable/api/tickets?q=Canada").json()
    assert len(data["tickets"]) == 1
    assert data["tickets"][0]["id"] == seeded["chat"]


def test_search_q_matches_customer_email(env, seeded):
    data = env.client.get("/fable/api/tickets?q=emma.wilson").json()
    assert any(t["id"] == seeded["email"] for t in data["tickets"])


def test_get_full_ticket_shape(env, seeded):
    t = env.ticket(seeded["email"])
    assert set(["id", "subject", "status", "channel", "sensitive", "customer",
                "messages", "draft", "order_context", "audit", "tags"]).issubset(t.keys())
    assert t["has_draft"] is True
    assert isinstance(t["messages"], list) and len(t["messages"]) >= 1


def test_get_missing_ticket_is_404(env):
    assert env.client.get("/fable/api/tickets/424242").status_code == 404


# --- PATCH ------------------------------------------------------------------
def test_patch_status(env, seeded):
    r = env.client.patch(f"/fable/api/tickets/{seeded['email']}", json={"status": "snoozed"})
    assert r.status_code == 200
    assert r.json()["ticket"]["status"] == "snoozed"


def test_patch_tags(env, seeded):
    r = env.client.patch(f"/fable/api/tickets/{seeded['email']}",
                         json={"tags": ["vip", "shipping"]})
    assert r.status_code == 200
    assert sorted(r.json()["ticket"]["tags"]) == ["shipping", "vip"]
    # re-patching replaces the tag set
    r2 = env.client.patch(f"/fable/api/tickets/{seeded['email']}", json={"tags": ["only"]})
    assert r2.json()["ticket"]["tags"] == ["only"]


def test_patch_snooze_and_assignee(env, seeded):
    r = env.client.patch(f"/fable/api/tickets/{seeded['email']}",
                         json={"snooze_until": "2026-08-01T00:00:00Z", "assignee": "tony"})
    assert r.status_code == 200
    row = env.conn.execute(
        "SELECT snooze_until, assignee FROM tickets WHERE id=?", (seeded["email"],)
    ).fetchone()
    assert row["snooze_until"] == "2026-08-01T00:00:00Z"
    assert row["assignee"] == "tony"


def test_patch_missing_ticket_is_404(env):
    assert env.client.patch("/fable/api/tickets/424242", json={"status": "closed"}).status_code == 404


# --- pagination cursor ------------------------------------------------------
def test_limit_and_cursor_paginate(env):
    ids = [env.intake_email(f"user{i}@example.com", f"msg {i}").json()["ticket_id"]
           for i in range(5)]
    page1 = env.client.get("/fable/api/tickets?status=all&limit=2").json()
    assert len(page1["tickets"]) == 2
    assert page1["next_cursor"] is not None
    page2 = env.client.get(
        f"/fable/api/tickets?status=all&limit=2&cursor={page1['next_cursor']}").json()
    assert len(page2["tickets"]) == 2
    # no overlap between pages
    p1_ids = {t["id"] for t in page1["tickets"]}
    p2_ids = {t["id"] for t in page2["tickets"]}
    assert p1_ids.isdisjoint(p2_ids)
    assert set(ids)  # created
