"""Integration: dashboard stats move after activity (STRATEGY §2.2)."""


def test_stats_shape_on_empty(env):
    s = env.client.get("/fable/api/stats").json()
    assert set(["tickets_today", "open", "avg_first_response_minutes",
                "drafts_accepted_pct", "by_channel"]).issubset(s.keys())
    assert s["open"] == 0


def test_counts_move_after_intake(env):
    before = env.client.get("/fable/api/stats").json()
    env.intake_email("emma.wilson@example.com", "Where is my order #BB1015?")
    env.intake_chat("s1", "Do you ship to Canada?")
    env.run_pipeline()
    after = env.client.get("/fable/api/stats").json()
    assert after["open"] == before["open"] + 2
    assert after["tickets_today"] >= before["tickets_today"] + 2
    assert after["by_channel"].get("email", 0) >= 1
    assert after["by_channel"].get("chat", 0) >= 1


def test_draft_acceptance_pct_after_send(env):
    tid = env.intake_email("emma.wilson@example.com",
                           "Where is my order #BB1015?").json()["ticket_id"]
    env.run_pipeline()
    env.client.post(f"/fable/api/tickets/{tid}/send",
                    json={"text": env.draft_for(tid)["body_text"]})
    s = env.client.get("/fable/api/stats").json()
    # exactly one decided draft, and it was sent (accepted) -> 100%
    assert s["drafts_accepted_pct"] == 100.0


def test_avg_first_response_after_reply(env):
    tid = env.intake_email("emma.wilson@example.com", "hi").json()["ticket_id"]
    env.run_pipeline()
    env.client.post(f"/fable/api/tickets/{tid}/send", json={"text": "hello back"})
    s = env.client.get("/fable/api/stats").json()
    assert s["avg_first_response_minutes"] >= 0.0
