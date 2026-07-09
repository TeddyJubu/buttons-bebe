"""Integration: chat widget long-poll `after` cursor semantics (STRATEGY §2.2)."""


def test_after_cursor_returns_only_newer_messages(env):
    # customer opens a chat -> one public inbound message
    env.intake_chat("sess-lp", "Do you ship to Canada?", name="Visitor")
    env.run_pipeline()

    first = env.client.get("/fable/api/chat/sess-lp/messages?after=0").json()["messages"]
    assert len(first) == 1
    assert first[0]["from_agent"] is False
    last_id = first[0]["id"]

    # after the newest id -> empty until an agent replies
    empty = env.client.get(f"/fable/api/chat/sess-lp/messages?after={last_id}").json()["messages"]
    assert empty == []

    # agent sends a reply
    tid = env.conn.execute(
        "SELECT ticket_id FROM chat_sessions WHERE session_id='sess-lp'").fetchone()["ticket_id"]
    env.client.post(f"/fable/api/tickets/{tid}/send", json={"text": "Yes we do!"})

    after = env.client.get(f"/fable/api/chat/sess-lp/messages?after={last_id}").json()["messages"]
    assert len(after) == 1
    assert after[0]["from_agent"] is True
    assert after[0]["body_text"] == "Yes we do!"


def test_unknown_session_returns_empty(env):
    assert env.client.get("/fable/api/chat/nope/messages").json()["messages"] == []


def test_internal_note_not_visible_to_chat_widget(env):
    env.intake_chat("sess-priv", "hello")
    env.run_pipeline()
    tid = env.conn.execute(
        "SELECT ticket_id FROM chat_sessions WHERE session_id='sess-priv'").fetchone()["ticket_id"]
    env.client.post(f"/fable/api/tickets/{tid}/note", json={"text": "internal only"})
    msgs = env.client.get("/fable/api/chat/sess-priv/messages?after=0").json()["messages"]
    assert all("internal only" != m["body_text"] for m in msgs)
