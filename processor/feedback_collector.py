"""Feedback loop collector — STUB.

To be implemented in the next step. For now, logs the agent message
so the processor can be tested end-to-end.

When an agent replies to a ticket that the AI previously drafted on,
the feedback loop:
  1. Fetches the agent's actual reply from Gorgias
  2. Finds any previous AI draft (internal note) on the same ticket
  3. Stores the agent's version in KB/learned/ticket-{id}.md
  4. This captures how the human improved/corrected the AI draft
  5. Periodically, the KB index is rebuilt to include learned content
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from config import get_settings
from logging_setup import get_logger, log_event

logger = get_logger(__name__)

# Retained only for backwards-compatible imports from older VPS snapshots.
# The live learning path is console-action capture plus nightly promotion.
# Keep this legacy hook fail-closed unless an operator deliberately enables it
# for a bounded rollback test.
LEGACY_FEEDBACK_OPT_IN = "FEEDBACK_LEGACY_OPT_IN"


def _legacy_opted_in() -> bool:
    return os.environ.get(LEGACY_FEEDBACK_OPT_IN) == "1"


def process_agent_reply(
    payload: dict[str, Any],
    ticket_thread: list[dict] | None = None,
) -> bool:
    """Process an agent's reply for the feedback/learning loop.

    Args:
        payload: Job payload (message_text, ticket_id, author_email, etc.)
        ticket_thread: Full conversation from Gorgias (all messages)

    Returns:
        True if the agent reply was captured, False on error or if
        no previous AI draft existed.
    """
    if not _legacy_opted_in():
        log_event(
            logger,
            "WARNING",
            "Legacy feedback collector disabled; use console-action learning",
            ticket_id=payload.get("ticket_id"),
            action="legacy_feedback_disabled",
        )
        return False

    ticket_id = payload.get("ticket_id")
    message_text = payload.get("message_text", "")
    author_email = payload.get("author_email", "")

    # STUB: log the agent reply
    # Real implementation will:
    #   1. Search ticket_thread for AI draft (internal note from processor)
    #   2. If found: extract the AI draft and the agent's actual reply
    #   3. Create KB/learned/ticket-{ticket_id}.md with:
    #      - YAML front-matter (source_type: agent_reply, review_pending: true)
    #      - The agent's reply as the "correct" answer
    #      - The AI draft as reference (what was improved)
    #   4. Log the capture
    #   5. Optionally notify that new learned content is available

    log_event(logger, "INFO", "Agent reply captured for feedback loop (stub)",
              ticket_id=ticket_id,
              author_email=author_email,
              message_preview=message_text[:100] if message_text else "")

    # Check if there's a previous AI draft on this ticket
    has_ai_draft = False
    if ticket_thread:
        for msg in ticket_thread:
            # Look for internal notes from the processor
            # (identified by "DRAFT REPLY" prefix or processor sender)
            text = msg.get("body_text", "") or msg.get("text", "")
            if isinstance(text, str) and "DRAFT REPLY" in text:
                has_ai_draft = True
                break

    if has_ai_draft:
        log_event(logger, "INFO", "Previous AI draft found — learning opportunity",
                  ticket_id=ticket_id)
    else:
        log_event(logger, "DEBUG", "No previous AI draft on this ticket",
                  ticket_id=ticket_id)

    return True
