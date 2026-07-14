#!/usr/bin/env python3
"""
priority_logic.py — AI-powered ticket priority analysis for Buttons Bebe.

Uses the LLM (via model_gateway) to analyze the customer message and determine
priority based on the owner's criteria. Falls back to keyword patterns if the
LLM is unavailable.

Priority levels:
  URGENT  — Act within minutes. If we don't act right now, the customer's
            order gets worse, stuck, or impossible to fix.
  HIGH    — Act within a few hours. This hurts revenue, trust, or reputation,
            but a short delay won't make it unfixable.
  NORMAL  — Queue or auto-draft. Informational, routine, or tied to an active
            order problem.
  LOW     — Queue or auto-draft. Generic thank-yous, unsubscribe requests, etc.

The AI analysis prompt embeds the owner's criteria directly, so the LLM
learns the exact scenarios and reasoning for each priority level.
"""

import json
import logging
import re

import model_gateway

log = logging.getLogger("gorgias-priority")

# Priority levels
PRIORITY_URGENT = "urgent"
PRIORITY_HIGH = "high"
PRIORITY_NORMAL = "normal"
PRIORITY_LOW = "low"

VALID_PRIORITIES = (PRIORITY_URGENT, PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW)

# Maps to Gorgias API priority values.
# Gorgias's canonical priorities are: low, normal, high, critical.
# Our internal "urgent" label maps to Gorgias "critical".
GORGIAS_PRIORITY_MAP = {
    PRIORITY_URGENT: "critical",
    PRIORITY_HIGH: "high",
    PRIORITY_NORMAL: "normal",
    PRIORITY_LOW: "low",
}

# Action flags for each priority level
class PriorityAction:
    """What the system should do for a given priority level."""
    def __init__(self, priority, notify_owner_immediately=False,
                 draft_internal_note=True, notify_owner_after_draft=False,
                 include_draft_in_notification=False):
        self.priority = priority
        self.notify_owner_immediately = notify_owner_immediately
        self.draft_internal_note = draft_internal_note
        self.notify_owner_after_draft = notify_owner_after_draft
        self.include_draft_in_notification = include_draft_in_notification

    def __repr__(self):
        return (f"PriorityAction(priority={self.priority}, "
                f"notify_now={self.notify_owner_immediately}, "
                f"draft={self.draft_internal_note}, "
                f"notify_after_draft={self.notify_owner_after_draft})")


# Predefined action sets per priority level.
_ACTIONS = {
    PRIORITY_URGENT: PriorityAction(
        priority=PRIORITY_URGENT,
        notify_owner_immediately=True,
        draft_internal_note=True,
        notify_owner_after_draft=True,
        include_draft_in_notification=True,
    ),
    PRIORITY_HIGH: PriorityAction(
        priority=PRIORITY_HIGH,
        notify_owner_immediately=False,
        draft_internal_note=True,
        notify_owner_after_draft=False,
        include_draft_in_notification=False,
    ),
    PRIORITY_NORMAL: PriorityAction(
        priority=PRIORITY_NORMAL,
        notify_owner_immediately=False,
        draft_internal_note=True,
        notify_owner_after_draft=False,
        include_draft_in_notification=False,
    ),
    PRIORITY_LOW: PriorityAction(
        priority=PRIORITY_LOW,
        notify_owner_immediately=False,
        draft_internal_note=True,
        notify_owner_after_draft=False,
        include_draft_in_notification=False,
    ),
}


# ---------------------------------------------------------------------------
# AI Priority Analysis — the core of the new system
# ---------------------------------------------------------------------------

PRIORITY_SYSTEM_PROMPT = """You are a priority analyst for Buttons Bebe, a children's clothing boutique.

Follow this decision tree IN ORDER. Output ONLY a JSON object with two fields:
{"priority": "urgent|high|normal|low", "reason": "brief 1-sentence explanation"}

---

STEP 1 — Is this tied to an order or customer?
Check the ORDER CONTEXT and CONVERSATION HISTORY provided below.

- If the customer has NO active order AND the message is a general question
  (product inquiry, sizing, availability, feedback, unsubscribe, thank you,
  off-topic, or any question not about a specific order):
  → LOW. Stop here. Do NOT continue to Step 2.

- If the message IS about an order (shipping, item, address, payment, return,
  exchange, damage, tracking, cancellation, or any order-specific topic):
  → Continue to Step 2.

---

STEP 2 — How time-sensitive is the action?

URGENT — Immediate action needed within minutes.
If we wait even an hour, the situation becomes worse or unfixable.
Examples:
- Pre-shipment address/size/item change (order about to leave warehouse)
- Pre-shipment cancellation (order can still be stopped)
- Active delivery reroute (package going to wrong/closed address)
- Fraud or security concern
- Angry/threatening language (retention risk that escalates fast)

HIGH — Action needed within hours.
It costs money or trust to delay, but the situation won't become unfixable.
Examples:
- Refund/chargeback request (post-fulfillment)
- Damaged/wrong/missing item
- Order not received (fulfilled but no delivery)
- Payment dispute

NORMAL — Routine order-related query. No time pressure.
Examples:
- "Where is my order?" (tracking exists, no active issue)
- Shipping ETA inquiry
- Return/exchange process question
- Product/sizing question from a customer who HAS an order

---

RULES:
1. Output ONLY {"priority": "...", "reason": "..."} — no other text.
2. "priority" must be one of: urgent, high, normal, low.
3. "reason" must be a brief 1-sentence explanation.
4. Follow the decision tree in order. STEP 1 first, then STEP 2.
5. If the customer has NO order and the question is generic → LOW.
6. If the customer HAS an order but the question is routine → NORMAL.
7. Only URGENT if delaying would make things unfixable.
8. When in doubt between two levels, choose the LOWER one (don't over-escalate)."""


def _ai_analyze_priority(customer_message, subject=None, conversation=None, order_context=None):
    """Use the LLM to analyze the customer message and determine priority.

    Args:
        customer_message — the customer's latest message text
        subject — the ticket subject (optional)
        conversation — recent conversation history (optional)
        order_context — order information (optional)

    Returns:
        dict with "priority" and "reason" keys, or None on failure.
    """
    if not customer_message or not customer_message.strip():
        return None

    # Build the user prompt with all available context
    parts = []

    if subject:
        parts.append(f"TICKET SUBJECT: {subject}")
        parts.append("")

    parts.append("CUSTOMER MESSAGE:")
    parts.append(customer_message.strip())
    parts.append("")

    # Add conversation history if available (last 5 messages)
    if conversation:
        recent = conversation[-5:] if len(conversation) > 5 else conversation
        parts.append("CONVERSATION HISTORY:")
        for msg in recent:
            sender = "Agent" if msg.get("from_agent") else "Customer"
            body = (msg.get("body_text") or msg.get("stripped_text") or "").strip()
            if body:
                parts.append(f"  [{sender}]: {body[:200]}")
        parts.append("")

    # Add order context if available
    if order_context and isinstance(order_context, dict):
        orders = order_context.get("orders") or []
        if orders:
            parts.append("ORDER CONTEXT:")
            for o in orders[:3]:
                if isinstance(o, dict):
                    name = o.get("name", "?")
                    fin = o.get("financial_status", "?")
                    ful = o.get("fulfillment_status", "?")
                    items = o.get("line_items") or []
                    item_names = ", ".join(
                        (li.get("title") or "?")[:30]
                        for li in items[:3]
                        if isinstance(li, dict)
                    )
                    parts.append(f"  Order {name}: payment={fin}, fulfillment={ful}, items: {item_names}")
            parts.append("")
        else:
            parts.append("ORDER CONTEXT: No orders found for this customer.")
            parts.append("")
    else:
        parts.append("ORDER CONTEXT: No order data available for this customer.")
        parts.append("")

    parts.append("Analyze this message and determine the priority level.")

    user_prompt = "\n".join(parts)

    raw = ""
    try:
        result = model_gateway.complete(
            [
                {"role": "system", "content": PRIORITY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,  # deterministic for priority classification
        )
        raw = (result.get("text") or "").strip()

        if not raw:
            return None

        # Parse the JSON response
        # Handle cases where the LLM wraps JSON in markdown code blocks
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

        data = json.loads(raw)
        priority = data.get("priority", "").lower().strip()
        reason = data.get("reason", "")

        if priority in VALID_PRIORITIES:
            return {"priority": priority, "reason": reason}
        else:
            log.warning("AI returned invalid priority '%s' — falling back to keyword analysis", priority)
            return None

    except json.JSONDecodeError as exc:
        log.warning("AI priority response was not valid JSON (%s): %s", exc, raw[:200])
        return None
    except model_gateway.LLMError as exc:
        log.warning("AI priority analysis failed (LLM error): %s", exc)
        return None
    except Exception as exc:
        log.warning("AI priority analysis failed (unexpected error): %s", exc)
        return None


# ---------------------------------------------------------------------------
# Fallback keyword patterns (used when AI is unavailable)
# ---------------------------------------------------------------------------

_URGENT_KEYWORDS = [
    # Address change before shipment
    (re.compile(r"\bchange\s+(?:my\s+)?(?:shipping\s+)?address\b", re.I),
     "Address change requested"),
    (re.compile(r"\b(?:update|correct|fix)\s+(?:my\s+)?(?:shipping\s+)?address\b", re.I),
     "Address correction requested"),
    (re.compile(r"\bwrong\s+address\b", re.I),
     "Wrong address on order"),
    # Pre-shipment cancellation
    (re.compile(r"\bcancel\s+(?:this|my|the)\s+order\b", re.I),
     "Cancellation requested"),
    (re.compile(r"\bdon(?:'t|ot)\s+(?:ship|send)\b", re.I),
     "Customer asking to hold shipment"),
    # Delivery reroute
    (re.compile(r"\b(?:reroute|redirect|change\s+delivery)\b", re.I),
     "Delivery reroute requested"),
    # Angry language
    (re.compile(r"\b(?:disgusting|outrageous|unacceptable|absolutely\s+ridiculous)\b", re.I),
     "Angry customer language"),
    (re.compile(r"\b(?:speak\s+to\s+(?:your\s+)?manager|speak\s+to\s+(?:a\s+)?supervisor)\b", re.I),
     "Customer wants manager"),
    # Frustrated follow-ups
    (re.compile(r"\b(?:still\s+waiting|still\s+no\s+(?:response|reply|answer))\b", re.I),
     "Repeated follow-up"),
    (re.compile(r"\b(?:been\s+\d+\s+days|weeks?\s+and\s+(?:still\s+)?no)\b", re.I),
     "Long wait — frustrated"),
]

_HIGH_KEYWORDS = [
    # Refund/chargeback
    (re.compile(r"\b(?:refund|chargeback|dispute)\b", re.I),
     "Refund/chargeback request"),
    # Damaged/wrong/missing
    (re.compile(r"\b(?:damaged|broken|wrong\s+item|missing|never\s+received)\b", re.I),
     "Damaged/wrong/missing item"),
    # Order not received
    (re.compile(r"\b(?:didn(?:'t|t)\s+receive|haven(?:'t|t)\s+received|not\s+received|never\s+got)\b", re.I),
     "Order not received"),
]

_LOW_KEYWORDS = [
    re.compile(r"\bthank\s+you\b", re.I),
    re.compile(r"\bthanks\b", re.I),
    re.compile(r"\bunsubscribe\b", re.I),
    re.compile(r"\bopt\s+out\b", re.I),
]


def _keyword_fallback_priority(customer_message):
    """Keyword-based fallback when AI is unavailable."""
    if not customer_message:
        return PRIORITY_NORMAL

    msg = customer_message.strip()

    # Check URGENT keywords
    for pattern, reason in _URGENT_KEYWORDS:
        if pattern.search(msg):
            log.info("Keyword fallback: URGENT — %s", reason)
            return PRIORITY_URGENT

    # Check HIGH keywords
    for pattern, reason in _HIGH_KEYWORDS:
        if pattern.search(msg):
            log.info("Keyword fallback: HIGH — %s", reason)
            return PRIORITY_HIGH

    # Check LOW keywords
    for pattern in _LOW_KEYWORDS:
        if pattern.search(msg):
            return PRIORITY_LOW

    return PRIORITY_NORMAL


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def determine_priority(classification, customer_message="", subject=None,
                       conversation=None, order_context=None):
    """Determine ticket priority using AI analysis with keyword fallback.

    Args:
        classification   — a classifier.Classification object (used as context
                           for the AI, and as fallback source for urgency/escalate)
        customer_message — the customer's latest message text
        subject          — the ticket subject (optional)
        conversation     — recent conversation history (optional)
        order_context    — order information (optional)

    Returns:
        A tuple of (priority, analysis) where:
          - priority is a string: PRIORITY_URGENT, PRIORITY_HIGH,
            PRIORITY_NORMAL, or PRIORITY_LOW
          - analysis is a dict with the AI's analysis (intent, key_issues,
            suggested_query_terms, etc.) or None if AI was unavailable
    """
    # Step 1: Try AI-powered priority analysis
    ai_result = _ai_analyze_priority(
        customer_message,
        subject=subject,
        conversation=conversation,
        order_context=order_context,
    )

    if ai_result:
        priority = ai_result["priority"]
        reason = ai_result["reason"]
        log.info(
            "AI priority analysis: %s — %s (category=%s)",
            priority.upper(), reason,
            getattr(classification, "category", "unknown"),
        )
        # Return full analysis for use in KB query and draft generation
        analysis = {
            "priority": priority,
            "reason": reason,
            "customer_message": customer_message,
            "subject": subject,
        }
        return priority, analysis

    # Step 2: Fallback to keyword analysis if AI unavailable
    log.info("AI priority analysis unavailable — using keyword fallback")
    fallback_priority = _keyword_fallback_priority(customer_message)
    analysis = {
        "priority": fallback_priority,
        "reason": "keyword fallback (AI unavailable)",
        "customer_message": customer_message,
        "subject": subject,
    }
    return fallback_priority, analysis


def get_actions(priority):
    """Get the action set for a priority level.

    Returns:
        A PriorityAction object describing what the system should do.
    """
    return _ACTIONS.get(priority, _ACTIONS[PRIORITY_LOW])
