"""Integration: ticket actions send / note / rewrite (STRATEGY §2.2)."""


def _draft_ticket(env, *, channel="email", email="emma.wilson@example.com",
                  phone="+15558231838", text="Where is my order #BB1015?"):
    if channel == "email":
        tid = env.intake_email(email, text).json()["ticket_id"]
    elif channel == "chat":
        tid = env.intake_chat("sess-actions", text, email=email).json()["ticket_id"]
    else:
        tid = env.intake_whatsapp(phone, text).json()["ticket_id"]
    env.run_pipeline()
    return tid


# --- send: email -> mailbox outbox ------------------------------------------
def test_send_email_captured_in_mailbox_outbox(env):
    tid = _draft_ticket(env, channel="email")
    draft = env.draft_for(tid)
    r = env.client.post(f"/fable/api/tickets/{tid}/send", json={"text": draft["body_text"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    ob = env.mailbox.get("/outbox").json()
    assert ob["count"] == 1
    assert ob["outbox"][0]["to"] == "emma.wilson@example.com"
    # the draft is now marked sent
    assert env.conn.execute(
        "SELECT status FROM drafts WHERE ticket_id=? ORDER BY id DESC LIMIT 1", (tid,)
    ).fetchone()["status"] == "sent"


# --- send: chat -> long-poll (no external transport) ------------------------
def test_send_chat_served_via_longpoll(env):
    tid = _draft_ticket(env, channel="chat")
    r = env.client.post(f"/fable/api/tickets/{tid}/send", json={"text": "Yes, we ship to Canada!"})
    assert r.status_code == 200
    msgs = env.client.get("/fable/api/chat/sess-actions/messages?after=0").json()["messages"]
    agent = [m for m in msgs if m["from_agent"]]
    assert any("Canada" in m["body_text"] for m in agent)
    # chat send must NOT touch the mailbox outbox
    assert env.mailbox.get("/outbox").json()["count"] == 0


# --- send: whatsapp -> whatsapp_outbox table --------------------------------
def test_send_whatsapp_stored_in_outbox_table(env):
    tid = _draft_ticket(env, channel="whatsapp")
    r = env.client.post(f"/fable/api/tickets/{tid}/send", json={"text": "Thanks for reaching out"})
    assert r.status_code == 200
    row = env.conn.execute(
        "SELECT phone, body_text FROM whatsapp_outbox WHERE ticket_id=?", (tid,)
    ).fetchone()
    assert row is not None
    assert row["body_text"] == "Thanks for reaching out"
    # and never leaves via the mailbox
    assert env.mailbox.get("/outbox").json()["count"] == 0


# --- send on a closed ticket -> 409 -----------------------------------------
def test_send_on_closed_ticket_is_409(env):
    tid = _draft_ticket(env, channel="email")
    env.client.patch(f"/fable/api/tickets/{tid}", json={"status": "closed"})
    r = env.client.post(f"/fable/api/tickets/{tid}/send", json={"text": "hello"})
    assert r.status_code == 409


# --- transport failure -> 502, draft stays proposed, nothing sent -----------
def test_send_email_transport_failure_is_502_and_draft_unchanged(env):
    tid = _draft_ticket(env, channel="email")
    env.kill(9603)  # mailbox down
    r = env.client.post(f"/fable/api/tickets/{tid}/send", json={"text": "hi"})
    assert r.status_code == 502
    # draft is still proposed (nothing left the system)
    assert env.draft_for(tid)["status"] == "proposed"
    # no message was stored for the send attempt
    n_agent = env.conn.execute(
        "SELECT COUNT(*) n FROM messages WHERE ticket_id=? AND from_agent=1", (tid,)
    ).fetchone()["n"]
    assert n_agent == 0


# --- note -------------------------------------------------------------------
def test_note_is_internal_and_not_public(env):
    tid = _draft_ticket(env, channel="email")
    r = env.client.post(f"/fable/api/tickets/{tid}/note", json={"text": "internal: verified order"})
    assert r.status_code == 200
    msg = r.json()["message"]
    assert msg["public"] is False
    assert msg["channel"] == "internal-note"
    # nothing left the system
    assert env.mailbox.get("/outbox").json()["count"] == 0
    # draft consumed as 'noted'
    assert env.conn.execute(
        "SELECT status FROM drafts WHERE ticket_id=? ORDER BY id DESC LIMIT 1", (tid,)
    ).fetchone()["status"] == "noted"


# --- rewrite ----------------------------------------------------------------
def test_rewrite_shorter_returns_new_proposed_draft(env):
    tid = _draft_ticket(env, channel="email")
    original = env.draft_for(tid)
    r = env.client.post(f"/fable/api/tickets/{tid}/rewrite", json={"instruction": "make it shorter"})
    assert r.status_code == 200
    new_draft = r.json()["draft"]
    assert new_draft["id"] != original["id"]
    assert new_draft["status"] == "proposed"
    assert len(new_draft["body_text"]) < len(original["body_text"])


def test_rewrite_without_draft_is_409(env):
    # a ticket with no proposed draft (never ran the pipeline)
    tid = env.intake_email("nodraft@example.com", "hi").json()["ticket_id"]
    r = env.client.post(f"/fable/api/tickets/{tid}/rewrite", json={"instruction": "shorter"})
    assert r.status_code == 409


# --- audit rows for each action ---------------------------------------------
def test_every_action_writes_an_audit_row(env):
    tid = _draft_ticket(env, channel="email")
    env.client.post(f"/fable/api/tickets/{tid}/rewrite", json={"instruction": "shorter"})
    env.client.post(f"/fable/api/tickets/{tid}/note", json={"text": "n"})
    tid2 = _draft_ticket(env, channel="chat")
    env.client.post(f"/fable/api/tickets/{tid2}/send", json={"text": "sent"})
    actions = {a["action"] for a in env.audit.list_recent(env.conn, 200)}
    assert {"send", "note", "rewrite", "intake"}.issubset(actions)


# --- action on missing ticket -> 404 ----------------------------------------
def test_action_on_missing_ticket_is_404(env):
    r = env.client.post("/fable/api/tickets/999999/note", json={"text": "x"})
    assert r.status_code == 404
