"""Unit tests for MockBrain determinism + safety (TESTING-STRATEGY §2.1)."""
import pytest


@pytest.fixture
def brains(server_modules):
    return server_modules["brains"]


def _ctx(brains, *, last_text, risk="low", orders=None, returns=None,
         customer=None, subject="", channel="email"):
    customer = customer if customer is not None else {"name": "Emma Wilson", "firstname": "Emma"}
    return brains.DraftContext(
        ticket_id=1, subject=subject, channel=channel, customer=customer,
        messages=[{"from_agent": False, "body_text": last_text,
                   "sender_name": "Emma", "created_at": "2026-07-10T00:00:00Z"}],
        last_customer_text=last_text, orders=orders or [], returns=returns or [],
        kb_snippets=[], risk=risk,
    )


ORDER = {
    "name": "#BB1015", "fulfillment_status": "fulfilled",
    "tracking_number": "1Z999AA10123456784",
    "tracking_url": "https://www.ups.com/track?tracknum=1Z999AA10123456784",
}


# --- determinism ------------------------------------------------------------
def test_same_context_same_draft(brains):
    b = brains.MockBrain()
    ctx = _ctx(brains, last_text="Where is my order #BB1015?", orders=[ORDER])
    a = b.draft(ctx).body_text
    c = b.draft(_ctx(brains, last_text="Where is my order #BB1015?", orders=[ORDER])).body_text
    assert a == c


def test_brain_name_is_mock(brains):
    assert brains.MockBrain().name == "mock"


# --- order-status draft uses real tracking data -----------------------------
def test_order_status_includes_tracking_number(brains):
    res = brains.MockBrain().draft(
        _ctx(brains, last_text="Where is my order #BB1015?", orders=[ORDER]))
    assert "1Z999AA10123456784" in res.body_text
    assert "#BB1015" in res.body_text
    assert any(ref.startswith("order:") for ref in res.kb_refs)


def test_picks_order_referenced_in_message(brains):
    other = {"name": "#BB2000", "fulfillment_status": None}
    res = brains.MockBrain().draft(
        _ctx(brains, last_text="status update on #BB1015 please", orders=[other, ORDER]))
    assert "#BB1015" in res.body_text
    assert "1Z999AA10123456784" in res.body_text


# --- no orders -> asks for an order number ----------------------------------
def test_no_orders_asks_for_order_number(brains):
    res = brains.MockBrain().draft(
        _ctx(brains, last_text="where is my package??", orders=[]))
    assert "order number" in res.body_text.lower()


# --- shipping-to-country ----------------------------------------------------
def test_shipping_question_answered(brains):
    res = brains.MockBrain().draft(
        _ctx(brains, last_text="Do you ship to Canada?", orders=[]))
    assert "ship" in res.body_text.lower()
    assert "policy:shipping" in res.kb_refs


# --- sensitive: no promises -------------------------------------------------
def test_sensitive_makes_no_refund_or_promise(brains):
    res = brains.MockBrain().draft(
        _ctx(brains, last_text="My order arrived damaged, I want a refund!!",
             risk="sensitive", orders=[ORDER]))
    low = res.body_text.lower()
    assert "refund" not in low
    # no hard commitment wording
    assert "we will refund" not in low
    assert "you will receive a refund" not in low
    assert "escalation" in " ".join(res.kb_refs)


# --- always signs off -------------------------------------------------------
@pytest.mark.parametrize("last,risk,orders", [
    ("Where is my order #BB1015?", "low", [ORDER]),
    ("Do you ship to Canada?", "low", []),
    ("random question about buttons", "low", []),
    ("this is damaged", "sensitive", []),
])
def test_always_signs_off(brains, last, risk, orders):
    res = brains.MockBrain().draft(_ctx(brains, last_text=last, risk=risk, orders=orders))
    assert res.body_text.rstrip().endswith("— Buttons Bebe Care Team")


# --- greeting by first name -------------------------------------------------
def test_greets_by_firstname(brains):
    res = brains.MockBrain().draft(
        _ctx(brains, last_text="hello", customer={"name": "Emma Wilson", "firstname": "Emma"}))
    assert res.body_text.startswith("Hi Emma,")


def test_greets_there_when_no_name(brains):
    res = brains.MockBrain().draft(
        _ctx(brains, last_text="hello", customer={}))
    assert res.body_text.startswith("Hi there,")


def test_firstname_derived_from_full_name_when_no_firstname(brains):
    res = brains.MockBrain().draft(
        _ctx(brains, last_text="hello", customer={"name": "Emma Wilson"}))
    assert res.body_text.startswith("Hi Emma,")


# --- rewrite transforms -----------------------------------------------------
def test_rewrite_shorter(brains):
    b = brains.MockBrain()
    original = b.draft(_ctx(brains, last_text="Where is my order #BB1015?", orders=[ORDER])).body_text
    res = b.rewrite(_ctx(brains, last_text="Where is my order #BB1015?", orders=[ORDER]),
                    original, "make it shorter")
    assert len(res.body_text) < len(original)
    assert res.body_text.rstrip().endswith("— Buttons Bebe Care Team")


def test_rewrite_friendlier_adds_warmth_and_keeps_greeting(brains):
    b = brains.MockBrain()
    original = "Hi Emma,\n\nYour order shipped.\n\n— Buttons Bebe Care Team"
    res = b.rewrite(_ctx(brains, last_text="x"), original, "make it friendlier")
    assert res.body_text.startswith("Hi Emma,")
    assert len(res.body_text) > len(original)


def test_rewrite_other_is_tagged_passthrough(brains):
    b = brains.MockBrain()
    original = "Hi Emma,\n\nBody.\n\n— Buttons Bebe Care Team"
    res = b.rewrite(_ctx(brains, last_text="x"), original, "translate to French")
    assert "translate to French" in res.body_text
    assert original in res.body_text


# --- brain factory ----------------------------------------------------------
def test_get_brain_defaults_to_mock(brains):
    assert brains.get_brain("mock").name == "mock"
    assert brains.get_brain("something-unknown").name == "mock"


def test_get_brain_stub_adapters_raise(brains):
    a = brains.get_brain("anthropic")
    h = brains.get_brain("hermes")
    assert a.name == "anthropic"
    assert h.name == "hermes"
    ctx = _ctx(brains, last_text="hi")
    with pytest.raises(NotImplementedError):
        a.draft(ctx)
    with pytest.raises(NotImplementedError):
        h.draft(ctx)
