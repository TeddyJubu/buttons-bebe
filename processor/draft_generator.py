"""Draft reply generator — STUB.

To be implemented in the next step. For now, returns a placeholder
draft so the processor can be tested end-to-end.

Planned implementation:
  - System prompt: "You are a Buttons Bebe support agent. Use only the
    KB content provided. Follow agent-core-rules. Never promise refunds.
    Be direct and helpful."
  - Context: KB search results + ticket conversation + Shopify order data
  - LLM call via OpenAI-compatible API (Ollama Cloud or local)
  - Returns draft text marked as "DRAFT — for human review"
"""

from __future__ import annotations

from typing import Any

from config import get_settings
from logging_setup import get_logger, log_event

logger = get_logger(__name__)


def generate_draft(
    payload: dict[str, Any],
    kb_results: list[dict] | None = None,
    order_data: dict | None = None,
    ticket_thread: list[dict] | None = None,
) -> str | None:
    """Generate a draft reply for a customer message.

    Args:
        payload: Job payload (message_text, customer_email, subject, etc.)
        kb_results: KB search results (list of passages with text + score)
        order_data: Shopify order data if available
        ticket_thread: Full conversation thread from Gorgias

    Returns:
        Draft text string, or None if generation fails.
        The draft is marked as "DRAFT — for human review before sending."
    """
    # STUB: return a placeholder draft
    # Real implementation will:
    #   1. Build system prompt from agent-core-rules
    #   2. Build context from KB results + ticket + order data
    #   3. Call LLM API (OpenAI-compatible)
    #   4. Post-process: strip any "send this" instructions, add DRAFT header
    #   5. Return the draft text

    customer = payload.get("customer_email", "customer")
    subject = payload.get("ticket_subject", "")
    message = payload.get("message_text", "")

    draft = (
        f"DRAFT REPLY — for human review before sending.\n\n"
        f"Hi {customer.split('@')[0] if '@' in customer else 'there'},\n\n"
        f"Thank you for reaching out regarding: {subject}\n\n"
        f"[Draft generation is not yet implemented. This is a placeholder.]\n"
        f"The real draft will be generated using KB content and order data.\n\n"
        f"Best,\n"
        f"Buttons Bebe Support\n"
    )

    log_event(logger, "DEBUG", "Draft generator stub: returning placeholder",
              message_id=payload.get("message_id"),
              ticket_id=payload.get("ticket_id"))

    return draft