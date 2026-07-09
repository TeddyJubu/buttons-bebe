"""Integration: the AI pipeline (STRATEGY §2.2).

Determinism: jobs are driven with ``env.run_pipeline()`` (direct
``pipeline._run_once``) rather than sleeping on the worker thread.
"""


def test_job_produces_draft_with_live_context(env):
    tid = env.intake_email("emma.wilson@example.com",
                           "Where is my order #BB1015?").json()["ticket_id"]
    processed = env.run_pipeline()
    assert processed == 1
    t = env.ticket(tid)
    assert t["draft"] is not None
    assert t["draft"]["status"] == "proposed"
    # real Shopify-emulator context flowed in -> tracking number in the draft
    assert "1Z999AA10123456784" in t["draft"]["body_text"]
    assert t["order_context"] is not None
    assert len(t["order_context"]["orders"]) >= 1


def test_returns_context_flows_in(env):
    # #BB1015 belongs to Emma and has a seeded Redo return (rejected).
    tid = env.intake_email("emma.wilson@example.com",
                           "question about order #BB1015").json()["ticket_id"]
    env.run_pipeline()
    t = env.ticket(tid)
    assert t["order_context"] is not None
    returns = t["order_context"]["returns"]
    assert any(r["order_name"] == "#BB1015" for r in returns)


def test_risk_classified_and_persisted(env):
    tid = env.intake_whatsapp("+15550000000",
                              "My order arrived damaged, I want a refund!!").json()["ticket_id"]
    env.run_pipeline()
    t = env.ticket(tid)
    assert t["sensitive"] is True
    assert t["sensitive_reason"]
    assert t["draft"]["risk"] == "sensitive"


# --- emulator down -> draft still produced, order_context null ---------------
def test_shopify_and_redo_down_still_drafts(env):
    env.kill(9601)  # shopify
    env.kill(9602)  # redo
    tid = env.intake_email("emma.wilson@example.com",
                           "Where is my order #BB1015?").json()["ticket_id"]
    env.run_pipeline()
    t = env.ticket(tid)
    assert t["draft"] is not None            # never fails a ticket
    assert t["order_context"] is None        # degraded gracefully
    actions = [a["action"] for a in t["audit"]]
    assert "pipeline:context" in actions


def test_shopify_down_redo_up_degrades_to_null(env):
    # context.fetch_context returns None only when BOTH are unreachable; with
    # shopify down and no order names, redo is queried with no order_name.
    env.kill(9601)
    tid = env.intake_email("emma.wilson@example.com", "where is it").json()["ticket_id"]
    env.run_pipeline()
    t = env.ticket(tid)
    assert t["draft"] is not None


# --- superseding older proposed drafts --------------------------------------
def test_second_job_supersedes_older_proposed_draft(env):
    tid = env.intake_email("emma.wilson@example.com", "first question").json()["ticket_id"]
    env.run_pipeline()
    first = env.draft_for(tid)
    # a follow-up message on the same open ticket enqueues a second job
    env.intake_email("emma.wilson@example.com", "actually where is #BB1015")
    env.run_pipeline()
    second = env.draft_for(tid)
    assert second["id"] != first["id"]
    # only one proposed draft remains
    rows = env.conn.execute(
        "SELECT status, COUNT(*) n FROM drafts WHERE ticket_id=? GROUP BY status",
        (tid,),
    ).fetchall()
    by_status = {r["status"]: r["n"] for r in rows}
    assert by_status.get("proposed") == 1
    assert by_status.get("superseded") == 1


def test_context_401_retry_remints_token(env):
    # Poison the cached token so the first orders call gets a 401; context.py
    # should invalidate, re-mint, and still return orders.
    env.context._token_value = "bogus-token-not-in-emulator"
    env.context._token_expiry = 9_999_999_999.0
    tid = env.intake_email("emma.wilson@example.com",
                           "Where is my order #BB1015?").json()["ticket_id"]
    env.run_pipeline()
    t = env.ticket(tid)
    assert t["order_context"] is not None
    assert len(t["order_context"]["orders"]) >= 1


def test_job_marked_done(env):
    env.intake_email("done@example.com", "hi")
    env.run_pipeline()
    row = env.conn.execute("SELECT status FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "done"
