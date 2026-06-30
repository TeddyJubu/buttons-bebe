"""
learn.py — captures human agent replies and writes them to kb/learned/.

When a human agent replies to a ticket Teddy previously drafted for, compare
the human reply to the AI draft. If the reply is substantially different (the
human added information Teddy didn't have), save it as a new KB article so
future tickets on the same topic can be answered better.

Similarity is measured with Jaccard overlap on word sets. A reply that shares
fewer than 45% of its words with the draft is "different enough" to be worth
keeping. One-liner acknowledgements ("Got it, thanks!") are ignored.

Usage
-----
    from skills.learn import capture_reply

    captured = capture_reply(
        ticket_id    = "12345",
        agent_reply  = "The actual message the agent sent...",
        intent       = "RETURN_REQUEST",
        original_draft = "The draft Teddy generated...",
        kb_dir       = "/app/kb",
    )
    # captured: True if a new KB article was written, False otherwise.
"""

import logging
import re
import time
from pathlib import Path

log = logging.getLogger('teddy.learn')

_MIN_WORDS          = 10    # skip very short replies ("Thanks!", "Got it.")
_SIMILARITY_CEILING = 0.45  # Jaccard above this → reply is too similar to the draft


def _word_set(text: str) -> set:
    return set(re.findall(r'[a-z]{3,}', text.lower()))


def _jaccard(a: str, b: str) -> float:
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def capture_reply(
    ticket_id: str,
    agent_reply: str,
    intent: str,
    original_draft: str,
    kb_dir: str,
) -> bool:
    """
    Save agent_reply to kb/learned/ when it differs substantively from the draft.

    Returns True if a new KB article was written.
    """
    reply = agent_reply.strip()

    if len(reply.split()) < _MIN_WORDS:
        log.debug("Ticket #%s: reply too short to learn from (%d words)", ticket_id, len(reply.split()))
        return False

    similarity = _jaccard(reply, original_draft or '')
    if similarity >= _SIMILARITY_CEILING:
        log.debug(
            "Ticket #%s: reply too similar to draft (jaccard=%.2f) — nothing new to learn",
            ticket_id, similarity,
        )
        return False

    learned_dir = Path(kb_dir) / 'learned'
    learned_dir.mkdir(parents=True, exist_ok=True)

    slug     = f"{intent.lower()}-{ticket_id}"
    out_path = learned_dir / f"{slug}.md"
    date     = time.strftime('%Y-%m-%d', time.gmtime())

    article = (
        f"---\n"
        f"type: learned\n"
        f"title: Learned reply — {intent} (ticket #{ticket_id})\n"
        f"tags: [{intent.lower()}, learned]\n"
        f"timestamp: {date}\n"
        f"---\n\n"
        f"# {intent} — verified agent reply\n\n"
        f"*Captured from ticket #{ticket_id} on {date}. "
        f"Jaccard divergence from draft: {1 - similarity:.2f}*\n\n"
        f"{reply}\n"
    )

    try:
        out_path.write_text(article, encoding='utf-8')
        log.info(
            "Ticket #%s: learned new KB article → %s (jaccard=%.2f)",
            ticket_id, out_path.name, similarity,
        )
        return True
    except Exception as e:
        log.warning("Ticket #%s: could not write KB article: %s", ticket_id, e)
        return False
