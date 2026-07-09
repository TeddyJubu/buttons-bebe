"""Integration: channel intake -> ticket/customer creation (STRATEGY §2.2)."""
import time
from datetime import datetime, timedelta, timezone


# --- basic intake per channel -----------------------------------------------
def test_email_intake_returns_202_and_ids(env):
    r = env.intake_email("emma.wilson@example.com", "Where is my order #BB1015?",
                         from_name="Emma Wilson", subject="Order?")
    assert r.status_code == 202
    body = r.json()
    assert body["ticket_id"] > 0
    assert body["message_id"] > 0
    t = env.ticket(body["ticket_id"])
    assert t["channel"] == "email"
    assert t["customer"]["email"] == "emma.wilson@example.com"


def test_chat_intake_creates_ticket(env):
    r = env.intake_chat("sess-1", "Do you ship to Canada?", name="Visitor")
    assert r.status_code == 202
    t = env.ticket(r.json()["ticket_id"])
    assert t["channel"] == "chat"


def test_whatsapp_intake_creates_ticket(env):
    r = env.intake_whatsapp("+15558231838", "hi there", name="Emma")
    assert r.status_code == 202
    t = env.ticket(r.json()["ticket_id"])
    assert t["channel"] == "whatsapp"


# --- customer find-or-create ------------------------------------------------
def test_customer_is_reused_by_email(env):
    a = env.intake_email("dup@example.com", "first").json()
    b = env.intake_email("dup@example.com", "second").json()
    ta = env.ticket(a["ticket_id"])
    tb = env.ticket(b["ticket_id"])
    assert ta["customer"]["id"] == tb["customer"]["id"]


def test_new_customer_created_when_unknown(env):
    a = env.intake_email("alice@example.com", "hi").json()
    b = env.intake_email("bob@example.com", "hi").json()
    assert env.ticket(a["ticket_id"])["customer"]["id"] != env.ticket(b["ticket_id"])["customer"]["id"]


# --- 7-day open-ticket reuse ------------------------------------------------
def test_same_channel_within_window_appends_to_ticket(env):
    a = env.intake_email("emma.wilson@example.com", "first message").json()
    b = env.intake_email("emma.wilson@example.com", "second message").json()
    assert a["ticket_id"] == b["ticket_id"]  # reused
    t = env.ticket(a["ticket_id"])
    assert len(t["messages"]) == 2


def test_different_channel_makes_new_ticket(env):
    a = env.intake_email("emma.wilson@example.com", "email msg").json()
    # same person (email known) but arriving on chat -> different ticket
    b = env.intake_chat("sess-x", "chat msg", email="emma.wilson@example.com").json()
    assert a["ticket_id"] != b["ticket_id"]


def test_stale_ticket_beyond_window_makes_new_ticket(env):
    a = env.intake_email("late@example.com", "first").json()
    tid = a["ticket_id"]
    # age the ticket 8 days into the past (relative to the real clock) so it is
    # outside the 7-day reuse window.
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    env.conn.execute(
        "UPDATE tickets SET last_message_at=?, created_at=? WHERE id=?",
        (stale, stale, tid),
    )
    env.conn.commit()
    b = env.intake_email("late@example.com", "second").json()
    assert b["ticket_id"] != tid  # a fresh ticket, not the stale one
    # but the customer is reused (same person)
    assert env.ticket(b["ticket_id"])["customer"]["id"] == env.ticket(tid)["customer"]["id"]


def test_chat_same_session_appends(env):
    a = env.intake_chat("sess-reuse", "first chat message", name="Visitor").json()
    b = env.intake_chat("sess-reuse", "second chat message").json()
    assert a["ticket_id"] == b["ticket_id"]  # same session -> same ticket
    t = env.ticket(a["ticket_id"])
    assert len(t["messages"]) == 2


def test_whatsapp_same_phone_reuses_and_enriches(env):
    a = env.intake_whatsapp("+15559990000", "first").json()
    # second message now carries a name -> customer is enriched, not duplicated
    b = env.intake_whatsapp("+15559990000", "second", name="Jordan Blake").json()
    assert a["ticket_id"] == b["ticket_id"]
    cust = env.ticket(a["ticket_id"])["customer"]
    assert cust["name"] == "Jordan Blake"


# --- validation -------------------------------------------------------------
def test_malformed_email_body_is_422(env):
    r = env.client.post("/fable/api/intake/email", json={"subject": "no from_email or body"})
    assert r.status_code == 422


def test_malformed_whatsapp_body_is_422(env):
    r = env.client.post("/fable/api/intake/whatsapp", json={"name": "x"})
    assert r.status_code == 422


# --- enqueues a pipeline job ------------------------------------------------
def test_intake_enqueues_a_job(env):
    env.intake_email("q@example.com", "queue me")
    depth = env.pipeline.queue_depth(env.conn)
    assert depth >= 1


# --- one real-thread smoke (STRATEGY: keep 1-2 exercising the worker) -------
def test_real_pipeline_thread_produces_draft(env):
    env.pipeline.start()
    try:
        tid = env.intake_email("emma.wilson@example.com",
                               "Where is my order #BB1015?").json()["ticket_id"]
        draft = None
        for _ in range(30):  # up to ~3s
            draft = env.draft_for(tid)
            if draft:
                break
            time.sleep(0.1)
        assert draft is not None
        assert "1Z999AA10123456784" in draft["body_text"]
    finally:
        env.pipeline.stop()
