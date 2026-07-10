"""Unit tests for the real Anthropic (Claude API) brain adapter (feature F3).

Everything here is fully offline: an ``httpx.MockTransport`` is injected so NO real
network call is ever made. We assert the request shape (URL / headers / body), that
the response is parsed into a draft, that the shared draft cleaner is applied, and
that API errors and missing keys degrade gracefully (never an unhandled exception,
and the factory falls back to MockBrain).
"""
import json

import httpx
import pytest


@pytest.fixture
def anth(server_modules):
    # server_modules ensures fable/server is on sys.path before we import.
    from app.brains.anthropic import AnthropicBrain, BrainConfigError
    from app.brains.base import DraftContext
    return {
        "AnthropicBrain": AnthropicBrain,
        "BrainConfigError": BrainConfigError,
        "DraftContext": DraftContext,
    }


ORDER = {
    "name": "#BB1015",
    "financial_status": "paid",
    "fulfillment_status": "fulfilled",
    "tracking_number": "1Z999AA10123456784",
    "tracking_url": "https://www.ups.com/track?tracknum=1Z999AA10123456784",
    "line_items": [{"title": "Ruffle Romper", "quantity": 1, "sku": "RR-01"}],
}
KB = {
    "file": "policies/shipping-policy.md",
    "title": "Shipping Policy",
    "heading": "International shipping",
    "text": "We ship internationally, including Canada and Israel.",
}

SIGNOFF = "— Buttons Bebe Care Team"
GOOD_REPLY = f"Hi Emma,\n\nGreat news — your order has shipped!\n\n{SIGNOFF}"


def _ctx(DraftContext, *, last_text="Where is my order #BB1015?", risk="low",
         risk_reason=None, orders=None, kb=None):
    return DraftContext(
        ticket_id=7, subject="Order question", channel="email",
        customer={"name": "Emma Wilson", "firstname": "Emma", "email": "emma@example.com"},
        messages=[{"from_agent": False, "body_text": last_text,
                   "sender_name": "Emma", "created_at": "2026-07-10T00:00:00Z"}],
        last_customer_text=last_text, orders=orders or [], returns=[],
        kb_snippets=kb or [], risk=risk, risk_reason=risk_reason,
    )


def _transport(captured, *, status=200, text=GOOD_REPLY):
    """An httpx.MockTransport that records requests and returns a canned response."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if status != 200:
            return httpx.Response(
                status, json={"type": "error",
                              "error": {"type": "overloaded_error", "message": "boom"}})
        return httpx.Response(200, json={
            "id": "msg_01", "type": "message", "role": "assistant",
            "model": "claude-sonnet-4-5", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
        })
    return httpx.MockTransport(handler)


# --- request shape ----------------------------------------------------------
def test_draft_hits_correct_url_headers_and_body(anth):
    captured = []
    brain = anth["AnthropicBrain"](api_key="test-key-123", transport=_transport(captured))
    res = brain.draft(_ctx(anth["DraftContext"], orders=[ORDER], kb=[KB]))

    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "test-key-123"
    assert req.headers["anthropic-version"] == "2023-06-01"

    payload = json.loads(req.content)
    assert payload["model"] == "claude-sonnet-4-5"
    assert isinstance(payload["max_tokens"], int) and payload["max_tokens"] > 0

    # system prompt carries the safety + grounding rules
    system = payload["system"]
    assert "Buttons Bebe" in system
    low = system.lower()
    assert "never invent" in low or "only state facts" in low

    # user content carries the real order + KB context (grounding)
    user = payload["messages"][0]["content"]
    assert "#BB1015" in user
    assert "1Z999AA10123456784" in user            # tracking number from order context
    assert "International shipping" in user          # KB heading passed through

    # response parsed into a draft
    assert res.body_text.startswith("Hi Emma,")
    assert res.body_text.rstrip().endswith(SIGNOFF)
    assert "kb:policies/shipping-policy.md" in res.kb_refs


def test_uses_configured_base_and_model(anth):
    captured = []
    brain = anth["AnthropicBrain"](
        api_key="k", model="claude-test-model",
        base_url="https://api.anthropic.com/", transport=_transport(captured))
    brain.draft(_ctx(anth["DraftContext"]))
    payload = json.loads(captured[0].content)
    assert payload["model"] == "claude-test-model"
    assert str(captured[0].url) == "https://api.anthropic.com/v1/messages"


# --- cleaner is applied -----------------------------------------------------
def test_cleaner_strips_trailing_self_commentary(anth):
    leaky = (
        f"Hi Emma,\n\nYour order shipped and is on its way.\n\n{SIGNOFF}\n\n"
        "The response above was complete and ready to send to the customer."
    )
    brain = anth["AnthropicBrain"](api_key="k", transport=_transport([], text=leaky))
    res = brain.draft(_ctx(anth["DraftContext"]))
    assert "The response above was complete" not in res.body_text
    assert res.body_text.rstrip().endswith(SIGNOFF)
    assert "stripped model self-commentary" in res.notes


def test_empty_model_output_returns_no_draft(anth):
    brain = anth["AnthropicBrain"](api_key="k", transport=_transport([], text="   "))
    res = brain.draft(_ctx(anth["DraftContext"]))
    assert res.body_text == ""
    assert "no_draft" in res.notes


# --- API errors degrade gracefully -----------------------------------------
@pytest.mark.parametrize("status", [429, 500, 503])
def test_api_error_returns_no_draft_without_raising(anth, status):
    captured = []
    brain = anth["AnthropicBrain"](api_key="k", transport=_transport(captured, status=status))
    # must NOT raise
    res = brain.draft(_ctx(anth["DraftContext"]))
    assert res.body_text == ""
    assert "no_draft" in res.notes
    assert len(captured) == 1  # it did attempt the call


def test_transport_exception_returns_no_draft(anth):
    def boom(request):
        raise httpx.ConnectError("no network")
    brain = anth["AnthropicBrain"](api_key="k", transport=httpx.MockTransport(boom))
    res = brain.draft(_ctx(anth["DraftContext"]))
    assert res.body_text == ""
    assert "no_draft" in res.notes


# --- should_draft gate ------------------------------------------------------
def test_bare_thanks_makes_no_draft_and_no_api_call(anth):
    captured = []
    brain = anth["AnthropicBrain"](api_key="k", transport=_transport(captured))
    res = brain.draft(_ctx(anth["DraftContext"], last_text="thanks!"))
    assert res.body_text == ""
    assert captured == []  # gate short-circuits BEFORE any network call


# --- sensitive tickets: no money promises in the system prompt --------------
def test_sensitive_ticket_prompt_forbids_money_promises(anth):
    captured = []
    brain = anth["AnthropicBrain"](api_key="k", transport=_transport(captured))
    brain.draft(_ctx(anth["DraftContext"],
                     last_text="My order is damaged, I want a refund!!",
                     risk="sensitive", risk_reason="mentions 'refund'"))
    system = json.loads(captured[0].content)["system"].lower()
    assert "sensitive" in system
    assert "no promises about money" in system or "do not promise a refund" in system


# --- rewrite ----------------------------------------------------------------
def test_rewrite_sends_current_draft_and_instruction(anth):
    captured = []
    reply = f"Hi Emma,\n\nShorter now.\n\n{SIGNOFF}"
    brain = anth["AnthropicBrain"](api_key="k", transport=_transport(captured, text=reply))
    res = brain.rewrite(
        _ctx(anth["DraftContext"]),
        f"Hi Emma,\n\nA very long original reply here.\n\n{SIGNOFF}",
        "make it shorter",
    )
    user = json.loads(captured[0].content)["messages"][0]["content"]
    assert "make it shorter" in user
    assert "A very long original reply here." in user
    assert res.body_text.startswith("Hi Emma,")
    assert res.body_text.rstrip().endswith(SIGNOFF)


# --- construction / factory fallback ----------------------------------------
def test_missing_key_raises_config_error(anth, monkeypatch):
    monkeypatch.delenv("FABLE_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(anth["BrainConfigError"]):
        anth["AnthropicBrain"]()


def test_factory_falls_back_to_mock_without_key(server_modules, monkeypatch):
    monkeypatch.delenv("FABLE_ANTHROPIC_API_KEY", raising=False)
    brains = server_modules["brains"]
    b = brains.get_brain("anthropic")
    assert b.name == "mock"  # graceful fallback, no crash


def test_name_is_anthropic(anth):
    brain = anth["AnthropicBrain"](api_key="k", transport=_transport([]))
    assert brain.name == "anthropic"
