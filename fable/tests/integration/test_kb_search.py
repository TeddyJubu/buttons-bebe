"""Integration: keyword knowledge-base search (feature F4).

Asserts real behaviour against the repo's actual ``kb/`` content:
* a shipping question returns the shipping / international policy snippet,
* gibberish / empty queries return nothing,
* only policies/ + faq/ + intents/ are searched (never learned/ or tickets/),
* the pipeline wires the KB snippets into the DraftContext handed to the brain.
"""
import pytest


@pytest.fixture
def kb(server_modules):
    from app import kb_search
    kb_search.reset_cache()  # read the KB files fresh
    return kb_search


# --- the headline case ------------------------------------------------------
def test_ship_to_canada_returns_shipping_snippet(kb):
    results = kb.search("do you ship to canada")
    assert results, "expected at least one KB match for a shipping question"

    # a shipping / international file is among the results
    assert any("ship" in r["file"] or "international" in r["file"] for r in results)

    # the best match actually speaks to international / Canada shipping
    top = results[0]
    assert "ship" in top["file"] or "international" in top["file"]
    assert "canada" in top["text"].lower() or "international" in top["text"].lower()


def test_results_capped_at_three(kb):
    results = kb.search("do you ship to canada")
    assert 1 <= len(results) <= 3
    # each snippet carries file + heading + a bounded text blob
    for r in results:
        assert r["file"] and "heading" in r
        assert len(r["text"]) <= 402  # ~400 chars + an ellipsis


def test_refund_window_matches_returns_policy(kb):
    results = kb.search("how long is my order refundable")
    assert results
    joined = " ".join(r["file"] for r in results)
    assert "return" in joined or "refund" in joined


# --- empty / no-match queries -----------------------------------------------
def test_gibberish_returns_empty(kb):
    assert kb.search("zzxqwv plovxkq flooberwomp") == []


def test_empty_or_stopword_query_returns_empty(kb):
    assert kb.search("") == []
    assert kb.search("   ") == []
    assert kb.search("do you have the a to of") == []  # all stop-words


# --- scope: never search learned/ or tickets/ -------------------------------
def test_only_policies_faq_intents_are_searched(kb):
    results = kb.search("refund window store credit final sale exchange")
    assert results
    for r in results:
        top_dir = r["file"].split("/")[0]
        assert top_dir in ("policies", "faq", "intents"), r["file"]


# --- pipeline wires snippets into the brain context -------------------------
def test_pipeline_context_includes_kb_snippets(env, monkeypatch):
    from app.brains import MockBrain

    captured = {}

    class CapturingBrain:
        name = "mock"

        def __init__(self):
            self._inner = MockBrain()

        def draft(self, ctx):
            captured["ctx"] = ctx
            return self._inner.draft(ctx)

        def rewrite(self, ctx, current_draft, instruction):
            return self._inner.rewrite(ctx, current_draft, instruction)

    # Swap the brain the pipeline uses so we can inspect the context it receives.
    monkeypatch.setattr(env.pipeline, "get_brain", lambda: CapturingBrain())

    env.intake_chat("kb-canada", "Do you ship to Canada?")
    env.run_pipeline()

    ctx = captured["ctx"]
    assert ctx.kb_snippets, "pipeline did not pass KB snippets into the brain context"
    assert any("ship" in s["file"] or "international" in s["file"] for s in ctx.kb_snippets)

    # the KB step is audited
    actions = [a["action"] for a in env.audit.for_ticket(env.conn, ctx.ticket_id)]
    assert "pipeline:kb" in actions
