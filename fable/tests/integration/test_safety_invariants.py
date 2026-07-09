"""Integration: the four safety invariants (TESTING-STRATEGY §0 & §2.2).

1. No draft is ever auto-sent.
2. Nothing leaves localhost (outbox empty until a human send).
3. Sensitive tickets are always flagged.
4. Everything is audited.
"""
import pytest


# --- 1 + 2: outbox stays empty until a human Send ---------------------------
def test_pipeline_never_sends_outbox_empty(env):
    """Drafting a reply must NOT put anything in the mailbox outbox."""
    env.intake_email("emma.wilson@example.com", "Where is my order #BB1015?")
    env.intake_chat("s1", "Do you ship to Canada?")
    env.intake_whatsapp("+15550000000", "damaged item, refund!!")
    env.run_pipeline()
    # drafts exist...
    n_drafts = env.conn.execute(
        "SELECT COUNT(*) n FROM drafts WHERE status='proposed'").fetchone()["n"]
    assert n_drafts == 3
    # ...but nothing has "left" the system.
    assert env.mailbox.get("/outbox").json()["count"] == 0
    assert env.conn.execute("SELECT COUNT(*) n FROM whatsapp_outbox").fetchone()["n"] == 0
    # no public agent message exists yet
    assert env.conn.execute(
        "SELECT COUNT(*) n FROM messages WHERE from_agent=1 AND public=1").fetchone()["n"] == 0


def test_outbox_populated_only_after_human_send(env):
    tid = env.intake_email("emma.wilson@example.com",
                           "Where is my order #BB1015?").json()["ticket_id"]
    env.run_pipeline()
    assert env.mailbox.get("/outbox").json()["count"] == 0  # before send
    env.client.post(f"/fable/api/tickets/{tid}/send",
                    json={"text": env.draft_for(tid)["body_text"]})
    assert env.mailbox.get("/outbox").json()["count"] == 1  # after human send


# --- 3: sensitive tickets always flagged ------------------------------------
@pytest.mark.parametrize("text,channel", [
    ("I want a refund for my order", "email"),
    ("this arrived damaged", "whatsapp"),
    ("my order never arrived", "chat"),
    ("THIS IS COMPLETELY UNACCEPTABLE AND I AM FURIOUS RIGHT NOW", "email"),
    ("stop scamming me!!!", "whatsapp"),
])
def test_sensitive_tickets_flagged(env, text, channel):
    if channel == "email":
        tid = env.intake_email("cust@example.com", text).json()["ticket_id"]
    elif channel == "chat":
        tid = env.intake_chat("s-sens", text).json()["ticket_id"]
    else:
        tid = env.intake_whatsapp("+15551112222", text).json()["ticket_id"]
    env.run_pipeline()
    t = env.ticket(tid)
    assert t["sensitive"] is True
    assert t["sensitive_reason"]
    assert t["draft"]["risk"] == "sensitive"


def test_sensitive_draft_makes_no_promises(env):
    tid = env.intake_email("cust@example.com",
                           "My order is damaged, I demand a refund!!").json()["ticket_id"]
    env.run_pipeline()
    body = env.draft_for(tid)["body_text"].lower()
    assert "refund" not in body
    assert "we will refund" not in body


# --- 4: everything is audited -----------------------------------------------
def test_every_mutation_appends_audit_row(env):
    tid = env.intake_email("emma.wilson@example.com",
                           "Where is my order #BB1015?").json()["ticket_id"]
    env.run_pipeline()
    env.client.patch(f"/fable/api/tickets/{tid}", json={"status": "snoozed"})
    env.client.post(f"/fable/api/tickets/{tid}/rewrite", json={"instruction": "shorter"})
    env.client.post(f"/fable/api/tickets/{tid}/note", json={"text": "n"})
    actions = [a["action"] for a in env.audit.for_ticket(env.conn, tid)]
    # intake, pipeline steps, patch, rewrite, note all recorded
    for expected in ("intake", "pipeline:context", "pipeline:risk", "pipeline:draft",
                     "patch", "rewrite", "note"):
        assert expected in actions, f"missing audit action: {expected}"


def test_audit_endpoint_lists_recent(env):
    env.intake_email("a@example.com", "hi")
    env.run_pipeline()
    audit = env.client.get("/fable/api/audit?limit=10").json()["audit"]
    assert len(audit) >= 1
    assert set(["ticket_id", "who", "action", "detail", "at"]).issubset(audit[0].keys())


# --- config: no outbound URL points off-localhost ---------------------------
def test_no_server_config_url_leaves_localhost(env):
    for base in (env.config.SHOPIFY_BASE, env.config.REDO_BASE, env.config.MAILBOX_BASE):
        assert base.startswith("http://127.0.0.1"), f"off-localhost base URL: {base}"
    # SUPPORT_EMAIL is a display address, but the transports are all local.
    assert env.config.HOST == "127.0.0.1"
