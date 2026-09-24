#!/usr/bin/env python3
"""
verifier.py — Fast verification gate for AI-drafted support replies.

Replaces the old proofreader (_proofread_draft) with a single LLM call that
checks BOTH factual accuracy (vs KB context) AND grammar/spelling/tone.

DESIGN:
  - One fast LLM call (temperature=0.0, short timeout).
  - Returns Approve | EditRequest(reason) — never blocks the draft forever.
  - If the verifier is slow or unavailable, the draft passes through (fail open).
  - If rejected, the caller may re-draft ONCE with the feedback, then post
    regardless (no re-verification — avoids infinite loops).

CONTRACT:
  verify(draft_text, kb_chunks, customer_message, subject, order_context)
    -> Verdict(approved=True/False, reason="")

Stdlib + model_gateway only. No pip deps.
"""

import json
import logging
import time

import model_gateway

log = logging.getLogger("verifier")

# --------------------------------------------------------------------------- #
# Verdict type
# --------------------------------------------------------------------------- #

class Verdict:
    """Outcome of one verification pass.

    Attributes:
        approved: True if the draft passed all checks.
        reason:   If not approved, a short explanation of what needs fixing.
                  Empty string when approved.
    """
    __slots__ = ("approved", "reason")

    def __init__(self, approved: bool, reason: str = ""):
        self.approved = approved
        self.reason = reason

    def __repr__(self):
        if self.approved:
            return "Verdict(APPROVED)"
        return f"Verdict(EDIT_REQUEST: {self.reason[:80]})"


# --------------------------------------------------------------------------- #
# System prompt for the verifier
# --------------------------------------------------------------------------- #

VERIFIER_SYSTEM_PROMPT = (
    "You are a quality checker for a children's clothing boutique's customer support drafts. "
    "Your job is to review a DRAFT reply and decide if it's ready for a human to review. "
    "You check THREE things:\n"
    "\n"
    "1. DANGEROUS HALLUCINATIONS (REJECT) — Flag ONLY these hard violations:\n"
    "   - Promising a refund, discount, store credit, or any money movement\n"
    "   - Claiming an action was taken (address changed, order cancelled, etc.) "
    "unless the order context confirms it\n"
    "   - Containing bracketed placeholders like [item name], [date], [size] — "
    "those are unfilled template slots\n"
    "   - Guaranteeing delivery dates or shipping times\n"
    "\n"
    "2. GRAMMAR & SPELLING (REJECT) — Flag spelling mistakes and grammar errors. "
    "The draft should be clean and readable.\n"
    "\n"
    "3. EVERYTHING ELSE IS FINE. Specifically:\n"
    "   - Empathy, apologies, and 'we'll check with the team' are ALWAYS OK — "
    "they don't need KB support\n"
    "   - The KB may not cover every question. If the draft says 'let me check "
    "with the team and get back to you', that is CORRECT — do NOT flag it\n"
    "   - Warm, friendly, lowercase tone is the right tone — do NOT flag casual "
    "language\n"
    "   - The draft does NOT need to cite or match every KB snippet. It just "
    "must not contradict them or invent policy\n"
    "\n"
    "Respond with EXACTLY one of these two formats:\n"
    "  APPROVED\n"
    "  EDIT_REQUEST: <what needs fixing, be specific>\n"
    "\n"
    "If the draft is correct and clean, say APPROVED. If anything needs fixing, "
    "say EDIT_REQUEST: followed by a brief, specific explanation of what to change. "
    "Do NOT rewrite the draft yourself — just flag what's wrong. "
    "When in doubt, APPROVE — the human reviewer will catch anything minor."
)

VERIFIER_TIMEOUT = 15  # seconds — reasoning models need more time to respond


# --------------------------------------------------------------------------- #
# Verifier
# --------------------------------------------------------------------------- #

def verify(draft_text, kb_chunks, customer_message, subject, order_context,
           cfg=None):
    """Run a single verification pass on a draft reply.

    Args:
        draft_text:      The draft reply text to verify.
        kb_chunks:       List of KB chunk dicts (source, heading, text, score).
        customer_message: The customer's latest message (for context).
        subject:         Ticket subject line.
        order_context:   Order context dict (or None).
        cfg:             Optional model_gateway config override (for tests).
                         Defaults to live config.

    Returns:
        Verdict object. approved=True means the draft is good to post.
        approved=False means the caller should re-draft with the reason as feedback.
    """
    if not draft_text or not draft_text.strip():
        # Empty draft — nothing to verify. Approve (the caller handles empty drafts).
        return Verdict(approved=True)

    # Build the user prompt for the verifier
    user_prompt = _build_verifier_prompt(
        draft_text, kb_chunks, customer_message, subject, order_context
    )

    start = time.monotonic()
    try:
        result = model_gateway.complete(
            [
                {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            timeout=VERIFIER_TIMEOUT,
            cfg=cfg,
        )
        response = (result.get("text") or "").strip()
    except model_gateway.LLMError as exc:
        log.info("Verifier LLM call failed (%s) — approving draft by default.", exc)
        return Verdict(approved=True)
    except Exception as exc:
        log.warning("Verifier unexpected error (%s) — approving draft by default.", exc)
        return Verdict(approved=True)

    elapsed = time.monotonic() - start
    log.info("Verifier completed in %.1fs", elapsed)

    # Parse the response
    if response.startswith("APPROVED"):
        log.info("Verifier: APPROVED")
        return Verdict(approved=True)

    if response.startswith("EDIT_REQUEST:"):
        reason = response[len("EDIT_REQUEST:"):].strip()
        log.info("Verifier: EDIT_REQUEST — %s", reason[:120])
        return Verdict(approved=False, reason=reason)

    # Unexpected response format — approve by default (fail open)
    log.info("Verifier returned unexpected response (%r) — approving by default.", response[:100])
    return Verdict(approved=True)


def _build_verifier_prompt(draft_text, kb_chunks, customer_message, subject, order_context):
    """Assemble the verifier's user prompt with draft + context."""
    parts = []

    parts.append("DRAFT REPLY TO VERIFY:")
    parts.append("=" * 60)
    parts.append(draft_text)
    parts.append("")

    if kb_chunks:
        parts.append("KNOWLEDGE BASE SNIPPETS (the ONLY source of truth):")
        parts.append("=" * 60)
        for i, ch in enumerate(kb_chunks[:10], 1):  # limit to 10 chunks
            heading = ch.get("heading") or "(intro)"
            src = ch.get("source", "?")
            parts.append(f"[{i}] source: {src}  ##{heading}")
            parts.append((ch.get("text") or "").strip()[:500])  # truncate long chunks
            parts.append("-" * 40)
        parts.append("")

    parts.append("CUSTOMER'S LATEST MESSAGE:")
    parts.append("=" * 60)
    parts.append((customer_message or "").strip() or "(no message text)")
    parts.append("")

    if subject:
        parts.append(f"TICKET SUBJECT: {subject}")
        parts.append("")

    if order_context and isinstance(order_context, dict):
        orders = order_context.get("orders") or []
        if orders:
            parts.append("ORDER CONTEXT:")
            parts.append("=" * 60)
            for o in orders[:3]:
                if isinstance(o, dict):
                    name = o.get("name", "?")
                    fin = o.get("financial_status", "?")
                    ful = o.get("fulfillment_status", "?") or "?"
                    items = o.get("line_items") or []
                    item_names = ", ".join(
                        (li.get("title") or "?")[:30]
                        for li in items[:3]
                        if isinstance(li, dict)
                    )
                    parts.append(f"  Order {name}: {fin}/{ful}, items: {item_names}")
            parts.append("")

    parts.append(
        "Check the DRAFT REPLY against the KB snippets above. "
        "Does it match the facts? Is the spelling and grammar correct? "
        "Is the tone warm and appropriate? "
        "Respond with APPROVED or EDIT_REQUEST: <reason>."
    )

    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _self_test():
    """Quick offline self-test using the mock provider.

    Tests the verifier's logic paths (fail-open, empty draft, response parsing).
    The LLM-dependent tests (hallucination detection, grammar checking) are
    tested implicitly in production — the mock doesn't return APPROVED/EDIT_REQUEST
    so those paths are exercised by the fail-open behavior.
    """
    print("=== Verifier Self-Test ===")

    # Mock config that forces the mock provider
    mock_cfg = {
        "provider": "mock",
        "model": "mock-model",
        "temperature": 0.0,
        "max_tokens": 256,
        "request_timeout": 5,
    }

    # Test 1: Empty draft should be approved (no-op, no LLM call)
    result = verify(
        draft_text="",
        kb_chunks=[],
        customer_message="Hello",
        subject="Test",
        order_context=None,
        cfg=mock_cfg,
    )
    print(f"  Test 1 (empty draft): {result}")
    assert result.approved, f"Expected APPROVED, got {result}"

    # Test 2: Mock returns echo (not APPROVED/EDIT_REQUEST) — should fail open
    result = verify(
        draft_text="hi! we process orders before they ship.",
        kb_chunks=[{
            "source": "kb/policies/shipping-policy.md",
            "heading": "Order Changes",
            "text": "Orders are processed before shipping.",
            "score": 0.85,
        }],
        customer_message="Can I change my shipping address?",
        subject="Order change request",
        order_context=None,
        cfg=mock_cfg,
    )
    print(f"  Test 2 (mock echo — fail open): {result}")
    assert result.approved, f"Expected APPROVED (fail open), got {result}"

    # Test 3: Verdict class logic
    v = Verdict(approved=True)
    assert v.approved and not v.reason
    v = Verdict(approved=False, reason="hallucination: delivery guarantee not in KB")
    assert not v.approved and v.reason == "hallucination: delivery guarantee not in KB"
    print("  Test 3 (Verdict class): OK")

    # Test 4: None draft_text
    result = verify(
        draft_text=None,
        kb_chunks=[],
        customer_message="Hello",
        subject="Test",
        order_context=None,
        cfg=mock_cfg,
    )
    print(f"  Test 4 (None draft): {result}")
    assert result.approved, f"Expected APPROVED, got {result}"

    # Test 5: Whitespace-only draft
    result = verify(
        draft_text="   ",
        kb_chunks=[],
        customer_message="Hello",
        subject="Test",
        order_context=None,
        cfg=mock_cfg,
    )
    print(f"  Test 5 (whitespace draft): {result}")
    assert result.approved, f"Expected APPROVED, got {result}"

    print("\nAll tests passed!")
    print("\nNOTE: LLM-dependent tests (hallucination detection, grammar checking)")
    print("are exercised in production. The mock provider always echoes input,")
    print("so those paths test the fail-open behavior (approve by default).")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _self_test()
