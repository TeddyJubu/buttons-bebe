"""
prioritize.py — assigns IMMEDIATE / HIGH / LOW priority to every ticket.

IMMEDIATE: notify owner, act now.
  Time-sensitive — waiting makes the problem impossible to fix.
  - Address / zip correction (before ship)
  - Size or item correction (before fulfillment)
  - Pre-shipment cancellation
  - Pickup ↔ shipping switch (before fulfillment)
  - Urgent delivery rerouting (failed delivery, redirect needed)
  - Anything irreversible after a hard deadline

HIGH: notify owner, draft reply, can wait a few hours.
  Critical for retention/revenue but a short delay is acceptable.
  - Refund / chargeback / cancellation (post-fulfillment)
  - Damaged / wrong / missing item received
  - Payment disputes
  - Order not received (tracking shows delivered, customer doesn't have it)
  - Angry or abusive language
  - Repeated follow-ups (same customer, 3+ messages, ticket still open)

LOW: auto-draft, routine.
  Informational — safe to queue or automate.
  - Order status questions
  - Shipping delay inquiries (general, not urgent reroute)
  - Product / sizing questions
  - Policy FAQs
  - Thank you / general inquiries
  - Newsletter / opt-out requests

Returns: {"level": "IMMEDIATE"|"HIGH"|"LOW", "reason": str, "action": str}
"""

import logging
import re

log = logging.getLogger('teddy.prioritize')

# ── Monotonic safety invariant ────────────────────────────────────────────────
# Priority can only be raised, never lowered. If rule A says HIGH and rule B
# says LOW, the result must stay HIGH. Call enforce_monotonic() after any step
# that might modify the priority (e.g. a future LLM pass) to guarantee this.
_PRIORITY_RANK = {'LOW': 0, 'HIGH': 1, 'IMMEDIATE': 2}


def enforce_monotonic(floor: dict, candidate: dict) -> dict:
    """
    Return candidate if it is >= floor in priority; otherwise return floor.

    floor     — the hard-rule result that cannot be lowered
    candidate — result from a secondary check (e.g. LLM-augmented analysis)

    This is the formal safety proof: even a hostile or hallucinating secondary
    check cannot reduce priority below what the deterministic rules mandated.
    """
    floor_rank     = _PRIORITY_RANK.get(floor.get('level', 'LOW'), 0)
    candidate_rank = _PRIORITY_RANK.get(candidate.get('level', 'LOW'), 0)
    if candidate_rank >= floor_rank:
        return candidate
    log.warning(
        "Monotonic safety: blocked priority downgrade %s → %s ('%s')",
        floor['level'], candidate.get('level'), candidate.get('reason', ''),
    )
    return floor

# ── IMMEDIATE signals (regex — allows words between key terms) ────────────────
# These require human action BEFORE something irreversible happens.
_IMMEDIATE_REGEX = [
    r'change\b.{0,20}\baddress',   r'update\b.{0,20}\baddress',
    r'address\b.{0,20}\bchange',   r'fix\b.{0,20}\baddress',
    r'correct\b.{0,20}\baddress',  r'wrong\b.{0,20}\baddress',
    r'address\b.{0,20}\bwrong',    r'address\b.{0,20}\bcorrect',
    r'wrong\b.{0,10}\bzip',        r'zip\b.{0,10}\bwrong',
    r'zip code', r'zipcode',
    r'change\b.{0,15}\bsize',
    r'size\b.{0,10}\bchange',      r'switch\b.{0,10}\bsize',
    r'cancel\b.{0,20}\border',     r'cancel\b.{0,10}\bmy',
    r"don'?t ship", r'do not ship', r'hold\b.{0,10}\border',
    r'stop\b.{0,10}\border',
    r'switch\b.{0,15}\bshipping',  r'switch\b.{0,15}\bpickup',
    r'change\b.{0,15}\bshipping',  r'change\b.{0,15}\bpickup',
    r'pickup\b.{0,15}\bshipping',  r'shipping\b.{0,15}\bpickup',
    r'before\b.{0,30}\bships?\b',
    r'failed delivery', r'delivery failed', r'undeliverable',
    r'return to sender', r'reroute', r'redirect\b.{0,15}\bpackage',
    r'redirect\b.{0,15}\bdelivery', r'delivery attempt', r'missed delivery',
]
_IMMEDIATE_COMPILED = [re.compile(p) for p in _IMMEDIATE_REGEX]

# ── HIGH signals ──────────────────────────────────────────────────────────────
_HIGH_EXACT = {
    # Financial
    'refund', 'chargeback', 'dispute', 'money back', 'get my money',
    'overcharged', 'double charged', 'charged twice', 'wrong amount',
    'billing issue', 'payment failed', 'payment issue',
    # Item problems (received)
    'damaged', 'broken', 'defective', 'arrived damaged',
    'wrong item', 'wrong order', 'incorrect item', 'not what i ordered',
    'missing item', 'item missing', 'not in my package',
    'missing from order', 'incomplete order',
    # Delivery failure
    'not received my order', 'not received my package', 'not received it',
    'not received the item', 'never received', 'says delivered', 'marked delivered',
    'shows delivered', 'delivered but', 'still waiting', 'never arrived',
    'lost package', 'lost in transit',
    # Legal / anger
    'lawyer', 'attorney', 'lawsuit', 'legal action', 'sue',
    'fraud', 'scam', 'fake', 'bbb', 'better business',
    'furious', 'disgusted', 'unacceptable', 'terrible', 'horrible',
    'worst', 'never again', 'going public', 'post about this',
    'social media', 'instagram', 'tiktok',
}

# ── LOW signals — anything that is clearly routine ────────────────────────────
_LOW_EXACT = {
    'thank you', 'thanks', 'received my order', 'love it', 'love my order',
    'arrived safely', 'got my package', 'everything looks great',
    'unsubscribe', 'opt out', 'newsletter', 'stop emails',
    'when does it launch', 'when is the launch', 'new arrivals',
    'what are your hours', 'store hours',
}


def _contains(text: str, phrases: set) -> str:
    """Return the first matching phrase found as substring, or empty string."""
    for phrase in phrases:
        if phrase in text:
            return phrase
    return ''

def _matches_immediate(text: str) -> str:
    """Return the first IMMEDIATE regex pattern that matches, or empty string."""
    for pattern in _IMMEDIATE_COMPILED:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return ''


def _is_repeated_followup(customer_messages: list) -> bool:
    """True when customer has sent 3+ messages on the same ticket."""
    return len(customer_messages) >= 3


def prioritize(
    intent: str,
    kb_confidence: str,
    order_data,
    message: str,
    all_messages: list,
) -> dict:
    """
    prioritize(...) -> {"level": str, "reason": str, "action": str}

    Checks IMMEDIATE first, then HIGH, then defaults to LOW.
    Priority can only be bumped UP by subsequent rules — never down.
    """
    msg = message.lower()
    customer_msgs = [m for m in all_messages if not m.get('from_agent')]

    # ── IMMEDIATE ────────────────────────────────────────────────────────────
    hit = _matches_immediate(msg)
    if hit:
        return {
            'level':  'IMMEDIATE',
            'reason': f'Time-sensitive action required: "{hit}"',
            'action': 'notify_owner_act_now',
        }

    # ── HIGH ─────────────────────────────────────────────────────────────────
    hit = _contains(msg, _HIGH_EXACT)
    if hit:
        return {
            'level':  'HIGH',
            'reason': f'Critical issue: "{hit}"',
            'action': 'draft_internal_note_notify_owner',
        }

    # Repeated follow-up (3+ customer messages on same ticket)
    if _is_repeated_followup(customer_msgs):
        return {
            'level':  'HIGH',
            'reason': f'Repeated follow-up: {len(customer_msgs)} customer messages on this ticket',
            'action': 'draft_internal_note_notify_owner',
        }

    # No KB match on a non-routine topic → bump to HIGH (needs human expertise)
    if kb_confidence == 'NONE' and intent not in ('ORDER_STATUS', 'GENERAL', 'UNKNOWN'):
        return {
            'level':  'HIGH',
            'reason': 'No KB articles matched — needs human expertise',
            'action': 'draft_internal_note_notify_owner',
        }

    # Genuinely unknown intent with no KB help
    if intent == 'UNKNOWN' and kb_confidence == 'NONE':
        return {
            'level':  'HIGH',
            'reason': 'Could not identify intent and no KB match',
            'action': 'draft_internal_note_notify_owner',
        }

    # ── LOW ──────────────────────────────────────────────────────────────────
    hit = _contains(msg, _LOW_EXACT)
    if hit:
        return {
            'level':  'LOW',
            'reason': f'Routine: "{hit}"',
            'action': 'auto_draft',
        }

    # All remaining intents default to LOW
    return {
        'level':  'LOW',
        'reason': f'Routine inquiry (intent={intent})',
        'action': 'auto_draft',
    }
