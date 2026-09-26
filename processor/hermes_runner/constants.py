"""Shared constants and result templates for the Hermes runner."""

from __future__ import annotations

import secrets
from typing import Any

from draft_cleaner import SENSITIVE_DRAFT_PREFIX
from shared.priority import RANK


_NONCE_BYTES = 8

# These are the only action values that a trusted Hermes verdict may use.
_ALLOWED_ACTIONS = frozenset(
    {"drafted", "sensitive_draft", "escalated", "no_kb_match", "no_draft_needed"}
)
_ACTION_SEVERITY = {
    "no_draft_needed": 0,
    "drafted": 0,
    "no_kb_match": 1,
    "sensitive_draft": 2,
    "escalated": 3,
}
_UNKNOWN_ACTION_SEVERITY = max(_ACTION_SEVERITY.values()) + 1

_MAX_VERDICT_CANDIDATES = 50
_MAX_JSON_BLOCK = 8_000
_MAX_REASON = 300
_MAX_NOTE = 400
_MAX_REASON_WITH_NOTE = 700
_MIN_NOTE = 40
_NOTE_LABEL = "model wrote after the draft, unverified"


def _make_run_token() -> str:
    """Mint an unguessable per-run marker from the operating-system CSPRNG."""

    return secrets.token_hex(_NONCE_BYTES)


# Execution failure is an operator state, never a customer acknowledgment.
_FALLBACK_RESULT: dict[str, Any] = {
    "priority": "normal",
    "reason": "AI draft unavailable — generation failed",
    "action": "no_kb_match",
    "notify_owner": False,
    "gorgias_priority_set": False,
    "note_posted": False,
    "draft_text": "",
    "no_draft": True,
    "generation_state": "failed",
    "generation_error": "runtime_error",
    "review_required": True,
    "staff_next_step": "Retry AI generation or write the reply manually.",
}


# Never substitute _FALLBACK_RESULT here. A process that ran but failed to
# authenticate its output must not create a sendable holding reply.
_TOKEN_FAILURE_RESULT: dict[str, Any] = {
    "priority": "high",
    "reason": (
        "Hermes output failed run-token authentication — no draft stored; "
        "handle this ticket manually."
    ),
    "action": "sensitive_draft",
    "notify_owner": True,
    "gorgias_priority_set": False,
    "note_posted": False,
    "draft_text": "",
    "no_draft": True,
    "generation_state": "failed",
    "generation_error": "authentication",
    "review_required": True,
    "staff_next_step": "Review the ticket manually; the AI output could not be authenticated.",
}


def _token_failure_result(reason: str | None = None) -> dict[str, Any]:
    """Return an isolated, non-sendable result for failed authentication."""

    result = dict(_TOKEN_FAILURE_RESULT)
    if reason:
        result["reason"] = reason
    return result


_NO_DRAFT_RESULT: dict[str, Any] = {
    "priority": "low",
    "reason": "No draft generated — nothing to answer in the customer message",
    "generation_state": "no_reply",
    "action": "no_draft_needed",
    "notify_owner": False,
    "gorgias_priority_set": False,
    "note_posted": False,
    "draft_text": "",
    "no_draft": True,
}
