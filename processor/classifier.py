"""Ticket priority classifier — deterministic first-pass screen.

Runs BEFORE Hermes as an advisory safety net. If the classifier flags a
ticket as sensitive, the orchestrator treats it as sensitive even if the
LLM misclassifies it. The classifier can only ESCALATE (NORMAL → HIGH/
IMMEDIATE), never de-escalate the LLM's assessment.

Classification logic:
  IMMEDIATE — refunds, chargebacks, disputes, wrong/damaged items,
              angry customers, order value > $200 with complaint.
              → Sensitive, notify owner.
  HIGH      — urgent shipping, final sale exceptions, address changes,
              sizing/fabric unknown, cancellations.
              → Sensitive (if applicable), notify owner.
  NORMAL    — shipping status, pickup questions, sizing help (info available),
              exchange requests, promo codes, general product questions.
              → Standard draft.

The classifier uses keyword matching on the message text + Gorgias intent
names + KB sensitivity flags. It does NOT call any external APIs — it is
purely deterministic and runs in <1ms.
"""

from __future__ import annotations

import re
from typing import Any

from config import get_settings
from logging_setup import get_logger, log_event

logger = get_logger(__name__)

# Priority constants
IMMEDIATE = "immediate"
HIGH = "high"
NORMAL = "normal"

# ── Keyword patterns ────────────────────────────────────────

# IMMEDIATE: money/dispute/damage/angry — always sensitive
_IMMEDIATE_KEYWORDS = [
    # Refunds and money
    r"\brefund\b", r"\bchargeback\b", r"\bdispute\b", r"\bmoney\s+back\b",
    r"\breimburse\b", r"\breimbursement\b", r"\bcompensat\w*\b",
    r"\bcredit\s+(my|your|our)\s+account\b", r"\bissue\s+a\s+refund\b",
    r"\breturn\s+(my|the)\s+(money|payment|funds)\b",
    # Damaged/wrong/missing items
    r"\bdamaged?\b", r"\bdefect\w*\b", r"\bbroken\b", r"\btorn\b",
    r"\bwrong\s+(item|size|color|colour|product|order)\b",
    r"\bmissing\s+(item|piece|part|product)\b",
    r"\bnever\s+(received|arrived|came|got)\b",
    r"\bdidn'?t\s+(receive|get|arrive)\b", r"\bnot\s+received\b",
    r"\blost\s+(package|parcel|order|shipment)\b",
    r"\bstolen\s+(package|parcel|order|shipment)\b",
    r"\b(package|parcel|order|shipment)\s+(?:was|is|got|has\s+been)\s+(lost|stolen)\b",
    # Angry/abusive
    r"\b(angry|furious|outraged|disgusted|appalled|unacceptable)\b",
    r"\b(terrible|horrible|awful|worst)\s+(service|experience|company|store)\b",
    r"\bnever\s+(shopping|buying|ordering)\s+(here|from\s+you)\b",
    r"\b(bbb|better\s+business\s+bureau|consumer\s+protection|small\s+claims)\b",
    r"\b(lawsuit|sue|legal\s+action|attorney|lawyer)\b",
    # Fraud
    r"\bfraud\b", r"\bscam\b", r"\bunauthorized\s+charg\w+\b",
]

# HIGH: urgent/time-sensitive — may or may not be sensitive
_HIGH_KEYWORDS = [
    r"\burgent\b", r"\basap\b", r"\brush\b", r"\bexpress\b",
    r"\bneed\s+(it|them)\s+(by|before|tomorrow|today|monday|tuesday|wednesday|thursday|friday)\b",
    r"\bdeadline\b", r"\btime\s+sensitive\b",
    # Address changes (time-critical if before shipment)
    r"\bchange\s+(my\s+)?(shipping\s+)?address\b", r"\bwrong\s+address\b",
    r"\bupdate\s+(my\s+)?address\b", r"\bnew\s+address\b",
    # Cancellations
    r"\bcancel\s+(my\s+)?(order|item|purchase)\b", r"\bcancellation\b",
    # Final sale exceptions
    r"\bfinal\s+sale\b", r"\bno\s+returns?\b",
    # Not received (general)
    r"\bwhere\s+is\s+my\s+(order|package|parcel)\b",
    r"\b(haven'?t|have\s+not)\s+(received|gotten|seen)\b",
    r"\bnot\s+yet\s+(received|arrived|delivered)\b",
    r"\blast\s+(chance|warning)\b",
    # Multiple follow-ups
    r"\b(following\s+up|follow\s?up)\b.*(again|still|yet|no\s+(response|reply|answer))\b",
]

# Intent names that indicate sensitive topics
_SENSITIVE_INTENTS = {
    "order/wrong", "order/missing", "order/damaged",
    "refund", "refund/request", "chargeback", "dispute",
    "cancel", "cancellation", "address-change",
    "payment-error", "payment-dispute",
}

# Intent names that indicate high urgency
_HIGH_INTENTS = {
    "urgent", "rush", "cancel", "cancellation",
    "address-change", "final-sale-exception",
}

_HIGH_SENSITIVE_INTENTS = {
    "cancel", "cancellation", "address-change", "final-sale-exception",
}

_HIGH_SENSITIVE_PATTERN = re.compile(
    r"\b(final\s+sale|change\s+(?:my\s+)?(?:shipping\s+)?address|"
    r"wrong\s+address|update\s+(?:my\s+)?address|new\s+address|"
    r"cancel(?:lation)?(?:\s+(?:my\s+)?(?:order|item|purchase))?)\b",
    re.IGNORECASE,
)

# Angry indicator count threshold — if 2+ angry keywords, force IMMEDIATE
_ANGRY_KEYWORDS = [
    r"\b(angry|furious|outraged|disgusted|appalled|unacceptable)\b",
    r"\b(terrible|horrible|awful|worst)\b",
    r"\bnever\s+(shopping|buying|ordering)\s+(here|from\s+you)\b",
    r"\b(bbb|better\s+business\s+bureau|consumer\s+protection|small\s+claims)\b",
    r"\b(lawsuit|sue|legal\s+action|attorney|lawyer)\b",
    r"\b(scam|fraud|rip\s?off|robbed)\b",
]

# Repeated follow-up pattern (3+ messages, no reply)
_FOLLOWUP_PATTERN = re.compile(
    r"(following\s+up|follow\s?up|still\s+(no|waiting)|yet\s+again|"
    r"any\s+(update|response|reply|answer)|"
    r"(2nd|3rd|second|third|fourth)\s+(time|attempt|message|email|follow))",
    re.IGNORECASE,
)


def _match_keywords(text: str, patterns: list[str]) -> int:
    """Return the count of keyword patterns that match in text."""
    text_lower = text.lower()
    count = 0
    for pattern in patterns:
        if re.search(pattern, text_lower):
            count += 1
    return count


def classify(
    payload: dict[str, Any],
    kb_results: list[dict] | None = None,
    order_data: dict | None = None,
) -> dict[str, Any]:
    """Classify a ticket message into a priority level.

    Args:
        payload: The job payload (message_text, intents, customer_email, etc.)
        kb_results: Results from search_kb (list of dicts with "sensitive" flag)
        order_data: Shopify order data if available (dict with "total_price", etc.)

    Returns:
        {
            "priority": "immediate" | "high" | "normal",
            "reason": str,              # why this priority was chosen
            "sensitive": bool,          # KB flagged as sensitive
            "should_draft": bool,       # should we draft a reply? (always True)
            "should_notify_owner": bool, # should we send WhatsApp alert?
            "source": str,              # "deterministic" (this classifier)
        }
    """
    message_text = (payload.get("message_text") or "").lower()
    ticket_subject = (payload.get("ticket_subject") or "").lower()
    combined_text = f"{ticket_subject} {message_text}"

    # Extract intent names from payload
    raw_intents = payload.get("intents", [])
    if isinstance(raw_intents, list):
        intent_names = set()
        for i in raw_intents:
            if isinstance(i, dict) and i.get("name"):
                intent_names.add(i["name"].lower())
            elif isinstance(i, str):
                intent_names.add(i.lower())
    else:
        intent_names = set()

    # Check KB sensitivity flag
    kb_sensitive = False
    if kb_results:
        for result in kb_results:
            if isinstance(result, dict) and result.get("sensitive"):
                kb_sensitive = True
                break

    # ── IMMEDIATE conditions ────────────────────────────────
    immediate_hits = _match_keywords(combined_text, _IMMEDIATE_KEYWORDS)
    angry_hits = _match_keywords(combined_text, _ANGRY_KEYWORDS)
    sensitive_intent_hit = bool(intent_names & _SENSITIVE_INTENTS)

    if immediate_hits > 0 or sensitive_intent_hit or kb_sensitive:
        reason_parts = []
        if immediate_hits > 0:
            reason_parts.append(f"keyword match ({immediate_hits} sensitive keywords)")
        if sensitive_intent_hit:
            reason_parts.append(f"sensitive intent ({intent_names & _SENSITIVE_INTENTS})")
        if kb_sensitive:
            reason_parts.append("KB sensitive flag")

        # Check for angry customer (2+ angry keywords → force immediate)
        if angry_hits >= 2:
            reason_parts.append(f"angry customer ({angry_hits} angry keywords)")

        # Check order value > $200 with complaint keywords
        if order_data:
            try:
                total = float(order_data.get("total_price", 0))
                if total > 200 and immediate_hits > 0:
                    reason_parts.append(f"high order value (${total:.2f})")
            except (ValueError, TypeError):
                pass

        log_event(logger, "INFO", "Classifier: IMMEDIATE",
                  ticket_id=payload.get("ticket_id"),
                  reason="; ".join(reason_parts))

        return {
            "priority": IMMEDIATE,
            "reason": "; ".join(reason_parts),
            "sensitive": True,
            "should_draft": True,
            "should_notify_owner": True,
            "source": "deterministic",
        }

    # ── HIGH conditions ─────────────────────────────────────
    high_hits = _match_keywords(combined_text, _HIGH_KEYWORDS)
    high_intent_hit = bool(intent_names & _HIGH_INTENTS)

    # Check for repeated follow-ups (3+ messages with no reply is CRITICAL in
    # the LLM prompt, but we can't count messages here — we look for the
    # follow-up keyword pattern as a HIGH signal)
    followup_match = _FOLLOWUP_PATTERN.search(combined_text)

    if high_hits > 0 or high_intent_hit or followup_match:
        high_sensitive = bool(intent_names & _HIGH_SENSITIVE_INTENTS) or bool(
            _HIGH_SENSITIVE_PATTERN.search(combined_text)
        )
        reason_parts = []
        if high_hits > 0:
            reason_parts.append(f"keyword match ({high_hits} urgent keywords)")
        if high_intent_hit:
            reason_parts.append(f"urgent intent ({intent_names & _HIGH_INTENTS})")
        if followup_match:
            reason_parts.append("follow-up pattern detected")

        log_event(logger, "INFO", "Classifier: HIGH",
                  ticket_id=payload.get("ticket_id"),
                  reason="; ".join(reason_parts))

        return {
            "priority": HIGH,
            "reason": "; ".join(reason_parts),
            "sensitive": high_sensitive,
            "should_draft": True,
            "should_notify_owner": True,
            "source": "deterministic",
        }

    # ── NORMAL (default) ────────────────────────────────────
    log_event(logger, "DEBUG", "Classifier: NORMAL",
              ticket_id=payload.get("ticket_id"))

    return {
        "priority": NORMAL,
        "reason": "no sensitive/urgent keywords or intents detected",
        "sensitive": False,
        "should_draft": True,
        "should_notify_owner": False,
        "source": "deterministic",
    }
