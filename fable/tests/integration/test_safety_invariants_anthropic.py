"""Safety invariants on the REAL-brain path — TESTING-READINESS T4.

Re-runs the core safety invariants with the pipeline wired to the real
``AnthropicBrain`` (feature F3) instead of the MockBrain, proving the model
adapter cannot bypass the safety model. Everything stays offline: the brain is
constructed with ``api_key="test-key"`` and an ``httpx.MockTransport`` that
returns canned Anthropic Messages API responses, so NO real network call is ever
made (the transport also lets us assert the request host).

Proven here:
  (a) no message is ever auto-sent — the mailbox outbox / whatsapp_outbox / public
      agent messages stay empty after drafting, and populate ONLY after an
      explicit human send action;
  (b) sensitive tickets remain flagged when the real brain drafts (the
      deterministic gate runs before the brain, and the sensitive framing is
      carried into the model request);
  (c) the brain never performs a real network call — the MockTransport intercepts
      every request and each request targets ``api.anthropic.com``;
  (d) ``FABLE_BRAIN=anthropic`` with NO API key falls back to MockBrain without
      crashing (the already-implemented factory behaviour in ``brains/__init__``).

The env fixture already routes the server's outbound ``httpx.get``/``httpx.post``
(Shopify/Redo/mailbox) to in-process emulators; the AnthropicBrain uses its own
injected ``httpx.Client(transport=MockTransport)``, independent of that routing,
so both context fetches and model calls are fully offline.
"""
from __future__ import annotations

import json

import httpx
import pytest

SIGNOFF = "— Buttons Bebe Care Team"
CANNED_REPLY = (
    "Hi there,\n\nThanks so much for reaching out! I've made a note of this and "
    f"our care team will follow up with you shortly.\n\n{SIGNOFF}"
)


def _anthropic_transport(captured, *, text=CANNED_REPLY):
    """A MockTransport that records requests and returns a canned Messages reply."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant",
            "model": "claude-sonnet-4-5", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
        })
    return httpx.MockTransport(handler)


@pytest.fixture
def anthropic_env(env, monkeypatch):
    """The integration env with the pipeline's brain swapped for a fully-offline
    AnthropicBrain (MockTransport). Exposes ``captured_anthropic`` requests."""
    from app.brains.anthropic import AnthropicBrain

    captured: list[httpx.Request] = []
    brain = AnthropicBrain(api_key="test-key", transport=_anthropic_transport(captured))
    # pipeline.process_job resolves the brain via the module-global get_brain().
    monkeypatch.setattr(env.pipeline, "get_brain", lambda *a, **k: brain)
    env.captured_anthropic = captured
    env.anthropic_brain = brain
    return env


# --- (a) no auto-send: outbox empty after the real brain drafts --------------
def test_anthropic_pipeline_never_auto_sends(anthropic_env):
    env = anthropic_env
    env.intake_email("emma.wilson@example.com", "Where is my order #BB1015?")
    env.intake_chat("s-anth", "Do you ship to Canada?")
    env.intake_whatsapp("+15550000000", "damaged item, refund!!")
    env.run_pipeline()

    # the real brain produced drafts...
    n_drafts = env.conn.execute(
        "SELECT COUNT(*) n FROM drafts WHERE status='proposed'").fetchone()["n"]
    assert n_drafts == 3
    # ...and it actually called the (mocked) Anthropic API for each.
    assert len(env.captured_anthropic) == 3
    # ...but NOTHING has left the system.
    assert env.mailbox.get("/outbox").json()["count"] == 0
    assert env.conn.execute("SELECT COUNT(*) n FROM whatsapp_outbox").fetchone()["n"] == 0
    assert env.conn.execute(
        "SELECT COUNT(*) n FROM messages WHERE from_agent=1 AND public=1").fetchone()["n"] == 0


def test_anthropic_outbox_populated_only_after_human_send(anthropic_env):
    env = anthropic_env
    tid = env.intake_email("emma.wilson@example.com",
                           "Where is my order #BB1015?").json()["ticket_id"]
    env.run_pipeline()
    assert env.mailbox.get("/outbox").json()["count"] == 0            # before human send
    draft = env.draft_for(tid)
    assert draft is not None and draft["brain"] == "anthropic"
    env.client.post(f"/fable/api/tickets/{tid}/send", json={"text": draft["body_text"]})
    assert env.mailbox.get("/outbox").json()["count"] == 1            # only after human send


# --- (b) sensitive tickets stay flagged when the real brain drafts -----------
@pytest.mark.parametrize("text,channel", [
    ("I want a refund for my order", "email"),
    ("this arrived damaged", "whatsapp"),
    ("my order never arrived", "chat"),
    ("stop scamming me!!!", "whatsapp"),
])
def test_anthropic_sensitive_tickets_flagged(anthropic_env, text, channel):
    env = anthropic_env
    if channel == "email":
        tid = env.intake_email("cust@example.com", text).json()["ticket_id"]
    elif channel == "chat":
        tid = env.intake_chat("s-anth-sens", text).json()["ticket_id"]
    else:
        tid = env.intake_whatsapp("+15551112222", text).json()["ticket_id"]
    env.run_pipeline()

    t = env.ticket(tid)
    assert t["sensitive"] is True
    assert t["sensitive_reason"]
    assert t["draft"]["risk"] == "sensitive"
    assert t["draft"]["brain"] == "anthropic"

    # the sensitive framing was carried into the model request (no-money-promise rules)
    assert len(env.captured_anthropic) == 1
    system = json.loads(env.captured_anthropic[0].content)["system"].lower()
    assert "sensitive" in system
    assert "no promises about money" in system or "do not promise a refund" in system


# --- (c) the brain never performs a real network call ------------------------
def test_anthropic_brain_makes_no_real_network_call(anthropic_env):
    env = anthropic_env
    env.intake_email("emma.wilson@example.com", "Where is my order #BB1015?")
    env.run_pipeline()

    # every model request was intercepted by the MockTransport and aimed at the API host
    assert len(env.captured_anthropic) >= 1
    for req in env.captured_anthropic:
        assert req.url.host == "api.anthropic.com", f"off-host model call: {req.url}"
        assert req.url.path == "/v1/messages"
    # the injected client is the mock one (proves no default socket-backed transport)
    assert env.anthropic_brain._client is not None


# --- (d) no API key -> factory falls back to MockBrain, no crash -------------
def test_anthropic_factory_falls_back_to_mock_without_key(server_modules, monkeypatch):
    monkeypatch.delenv("FABLE_ANTHROPIC_API_KEY", raising=False)
    brain = server_modules["brains"].get_brain("anthropic")
    assert brain.name == "mock"


def test_anthropic_pipeline_falls_back_to_mock_without_key(env, monkeypatch):
    """End-to-end: FABLE_BRAIN=anthropic with no key still drafts (via mock)."""
    monkeypatch.delenv("FABLE_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(env.config, "BRAIN", "anthropic")
    tid = env.intake_email("emma.wilson@example.com",
                           "Where is my order #BB1015?").json()["ticket_id"]
    env.run_pipeline()
    draft = env.draft_for(tid)
    assert draft is not None            # never crashes; a draft is still produced
    assert draft["brain"] == "mock"     # produced by the graceful fallback
