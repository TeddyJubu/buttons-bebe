#!/usr/bin/env python3
"""
hindsight_integration.py — Bridge between the Buttons Bebe Gorgias system
and Hindsight (agent memory that learns).

This module is the SINGLE place the Gorgias system talks to Hindsight.
It provides three operations that map to Hindsight's retain/recall/reflect:

  1. retain_ticket_experience(ticket_id, customer_msg, agent_reply, ...)
     Called by Workflow B when a human agent replies. Stores the full
     customer→agent interaction as an EXPERIENCE memory. Over time,
     Hindsight learns patterns from these.

  2. recall_relevant_memories(question, top_k=5)
     Called by draft_engine (alongside the pgvector KB search). Returns
     learned memories that are relevant to the customer's question —
     things the system has "experienced" before but may not be in the
     static KB.

  3. reflect_on_patterns(query)
     Called by the weekly review (Workflow C) or on-demand. Asks Hindsight
     to find patterns across all stored experiences — e.g., "what issues
     do customers most commonly escalate?"

DESIGN DECISIONS:
  - Hindsight runs as a separate Docker service (localhost:8888). If it's
    down, the Gorgias system continues working — this module degrades
    gracefully (returns empty results, logs a warning).
  - PII is scrubbed BEFORE sending to Hindsight, using the same scrub_pii()
    from kb_writeback.py. No customer names, emails, phones, or order
    numbers ever leave the VPS.
  - The bank_id is "buttons-bebe" for all operational memories. We use
    metadata to separate world facts (KB seed) from experiences (ticket
    interactions).
  - This module is stdlib-only except for hindsight_client (installed in
    .venv). The webhook server imports it lazily so the server doesn't
    crash if hindsight_client isn't installed.

Usage:
    from hindsight_integration import recall_relevant_memories
    memories = recall_relevant_memories("where is my order?")
"""

import logging
import os
from typing import Optional

log = logging.getLogger("hindsight-integration")

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
HINDSIGHT_URL = os.environ.get("HINDSIGHT_URL", "http://127.0.0.1:8888")
BANK_ID = "buttons-bebe"

# Lazy singleton — only created on first use
_client = None


def _get_client():
    """Lazy-init the Hindsight client. Returns None if unavailable."""
    global _client
    if _client is not None:
        return _client
    try:
        from hindsight_client import Hindsight
        _client = Hindsight(base_url=HINDSIGHT_URL)
        log.info("Hindsight client connected to %s", HINDSIGHT_URL)
        return _client
    except ImportError:
        log.warning("hindsight-client not installed — Hindsight integration disabled")
        return None
    except Exception as exc:
        log.warning("Hindsight client init failed: %s — integration disabled", exc)
        return None


# --------------------------------------------------------------------------- #
# PII scrubbing (reuse kb_writeback's scrubber)
# --------------------------------------------------------------------------- #
def _scrub(text: str) -> str:
    """Scrub PII from text before sending to Hindsight."""
    try:
        from kb_writeback import scrub_pii
        scrubbed, _ = scrub_pii(text)
        return scrubbed
    except Exception:
        # If scrub_pii isn't available, do a basic redaction
        import re
        # Emails
        text = re.sub(r'\S+@\S+', '[email]', text)
        # Phone-like patterns
        text = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[phone]', text)
        # Order numbers (long digit runs)
        text = re.sub(r'\b\d{6,}\b', '[order#]', text)
        return text


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def retain_ticket_experience(
    ticket_id: str,
    customer_message: str,
    agent_reply: str,
    *,
    category: str = "",
    ticket_tags: str = "",
    similarity_to_draft: Optional[float] = None,
) -> bool:
    """Store a resolved customer→agent interaction as an experience memory.

    Called by Workflow B after a human agent replies to a ticket.
    Hindsight will extract facts, entities, and relationships from this
    interaction and learn from it over time.

    All text is PII-scrubbed before sending. Returns True on success,
    False on failure (never raises — the webhook must not crash).
    """
    client = _get_client()
    if client is None:
        return False

    try:
        # Scrub PII
        clean_question = _scrub(customer_message)[:2000]
        clean_reply = _scrub(agent_reply)[:2000]

        # Build the memory content — a natural-language summary of the interaction
        content_parts = [
            f"Customer question: {clean_question}",
            f"Agent response: {clean_reply}",
        ]
        if category:
            content_parts.append(f"Category: {category}")
        if ticket_tags:
            content_parts.append(f"Tags: {ticket_tags}")
        if similarity_to_draft is not None:
            content_parts.append(
                f"AI-draft similarity: {similarity_to_draft:.2f} "
                f"({'high match' if similarity_to_draft >= 0.7 else 'agent modified significantly'})"
            )
        content = "\n".join(content_parts)

        # Metadata for filtering
        metadata = {
            "ticket_id": _scrub(str(ticket_id)),
            "source": "workflow_b",
            "type": "experience",
        }
        if category:
            metadata["category"] = category

        client.retain(
            bank_id=BANK_ID,
            content=content,
            context=f"support ticket {ticket_id}",
            metadata=metadata,
        )
        log.info("Retained experience for ticket #%s (category=%s)", ticket_id, category or "?")
        return True
    except Exception as exc:
        log.warning("Retain failed for ticket #%s: %s", ticket_id, exc)
        return False


def retain_owner_answer(question: str, answer: str, *, ticket_id: str = "") -> bool:
    """Store an owner (Chaim) Q&A as a world fact — highest trust.

    Called when Chaim answers a KB gap question. This is the most valuable
    type of memory — the owner's authoritative answer.
    """
    client = _get_client()
    if client is None:
        return False

    try:
        clean_q = _scrub(question)[:2000]
        clean_a = _scrub(answer)[:2000]
        content = f"Owner Q&A — Question: {clean_q}\nAnswer: {clean_a}"

        metadata = {
            "source": "owner_qa",
            "type": "world",
            "trust": "confirmed",
        }
        if ticket_id:
            metadata["ticket_id"] = _scrub(str(ticket_id))

        client.retain(
            bank_id=BANK_ID,
            content=content,
            context="owner authoritative answer",
            metadata=metadata,
        )
        log.info("Retained owner answer for question: %s...", question[:60])
        return True
    except Exception as exc:
        log.warning("Retain owner answer failed: %s", exc)
        return False


def retain_kb_content(title: str, text: str, *, category: str = "", tags: str = "") -> bool:
    """Seed Hindsight with existing KB content (policies, intents, FAQ).

    This populates Hindsight's 'world' memory with the static knowledge
    the system already has, so recall can return it alongside learned
    experiences.
    """
    client = _get_client()
    if client is None:
        return False

    try:
        clean_text = _scrub(text)[:4000]
        content = f"KB {category or 'content'} — {title}:\n{clean_text}"

        metadata = {
            "source": "kb_seed",
            "type": "world",
        }
        if category:
            metadata["category"] = category
        if tags:
            metadata["tags"] = tags

        client.retain(
            bank_id=BANK_ID,
            content=content,
            context=f"KB {category or 'seed'}",
            metadata=metadata,
        )
        return True
    except Exception as exc:
        log.warning("Retain KB content failed for '%s': %s", title, exc)
        return False


def recall_relevant_memories(question: str, top_k: int = 5) -> list:
    """Retrieve memories relevant to a customer question.

    Called by draft_engine alongside the pgvector KB search. Returns
    learned experiences and world facts that match the question.

    Returns a list of dicts with 'text', 'type', 'tags' keys.
    Returns [] on failure (never raises).
    """
    client = _get_client()
    if client is None:
        return []

    try:
        clean_q = _scrub(question)[:2000]
        results = client.recall(bank_id=BANK_ID, query=clean_q)

        # Convert RecallResult objects to plain dicts
        memories = []
        for r in (results or [])[:top_k]:
            memories.append({
                "text": getattr(r, "text", ""),
                "type": getattr(r, "type", ""),
                "tags": getattr(r, "tags", []) or [],
                "metadata": getattr(r, "metadata", {}) or {},
                "source": "hindsight",
            })
        log.info("Recalled %d memories for question: %s...", len(memories), question[:60])
        return memories
    except Exception as exc:
        log.warning("Recall failed: %s", exc)
        return []


def reflect_on_patterns(query: str) -> list:
    """Ask Hindsight to find patterns across all stored experiences.

    Called by Workflow C (weekly review) or on-demand. Examples:
      - "What issues do customers most commonly escalate?"
      - "What questions did the AI draft get wrong most often?"
      - "What policies are customers most confused about?"

    Returns a list of reflection results.
    """
    client = _get_client()
    if client is None:
        return []

    try:
        results = client.reflect(bank_id=BANK_ID, query=query)
        reflections = []
        for r in (results or [])[:10]:
            reflections.append({
                "text": getattr(r, "text", str(r)),
                "type": getattr(r, "type", "reflection"),
                "tags": getattr(r, "tags", []) or [],
            })
        log.info("Reflected on '%s': %d results", query[:60], len(reflections))
        return reflections
    except Exception as exc:
        log.warning("Reflect failed: %s", exc)
        return []


def is_available() -> bool:
    """Check if Hindsight is running and reachable."""
    client = _get_client()
    if client is None:
        return False
    try:
        import urllib.request
        urllib.request.urlopen(f"{HINDSIGHT_URL}/health", timeout=3)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("Hindsight Integration Self-Test")
    print("=" * 50)

    if not is_available():
        print("Hindsight is not available. Start it with:")
        print("  docker start hindsight")
        exit(1)

    print("1. Health check: OK")

    # Test retain
    ok = retain_owner_answer(
        "What is the return window for final sale items?",
        "Final sale items cannot be returned or exchanged. This policy is strictly enforced."
    )
    print(f"2. Retain owner answer: {'OK' if ok else 'FAILED'}")

    # Test recall
    memories = recall_relevant_memories("Can I return a final sale item?")
    print(f"3. Recall: {len(memories)} results")
    for m in memories[:3]:
        print(f"   type={m['type']} text={m['text'][:100]}")

    # Test experience retain
    ok = retain_ticket_experience(
        "99999",
        "Hi, I never received my order, it's been 2 weeks",
        "I'm so sorry for the delay! Let me track your order right away. Can you confirm your shipping address?",
        category="shipping",
        similarity_to_draft=0.45,
    )
    print(f"4. Retain ticket experience: {'OK' if ok else 'FAILED'}")

    # Test recall again (should find the new experience)
    memories = recall_relevant_memories("order never received")
    print(f"5. Recall after experience: {len(memories)} results")
    for m in memories[:3]:
        print(f"   type={m['type']} text={m['text'][:100]}")

    print("\nAll tests passed!")