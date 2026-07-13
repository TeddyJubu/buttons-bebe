"""collector.py — capture step: turn a resolved ticket into a review packet.

For one ticket it: guards sensitive tickets, finds the (AI draft, human reply)
pair (pairing.py), attaches a similarity HINT (similarity.py), runs the PII
highlighter (pii.py), and writes kb/learned/ticket-<id>.md marked review_pending.

It writes NOTHING to the indexed KB. Promotion into kb/tickets/ is a separate,
human-gated step (kb/scripts/review_learned.py).
"""
from __future__ import annotations

import datetime
import os
import pathlib

import yaml

from . import config, gorgias_read, pairing, pii, similarity, store

# Best-effort sensitive-ticket guard. These usually escalate (no AI draft) so they
# rarely reach here, but we belt-and-braces it. Not a substitute for the human gate.
_SENSITIVE_TAGS = {"escalation", "escalated", "sensitive", "refund", "chargeback", "dispute"}
_SENSITIVE_WORDS = ("chargeback", "dispute", "fraud", "lawsuit", "attorney")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _is_sensitive(ticket: dict | None) -> bool:
    if not ticket:
        return False
    for t in ticket.get("tags", []) or []:
        name = (t.get("name") if isinstance(t, dict) else str(t)).lower()
        if name in _SENSITIVE_TAGS:
            return True
    subject = str(ticket.get("subject", "")).lower()
    return any(w in subject for w in _SENSITIVE_WORDS)


def _learned_path(ticket_id: int) -> pathlib.Path:
    return config.LEARNED_DIR / f"ticket-{ticket_id}.md"


def write_packet(pair: pairing.Pair) -> dict:
    """Write the review packet file. Returns {'path', 'similarity'}."""
    config.LEARNED_DIR.mkdir(parents=True, exist_ok=True)

    hint = similarity.compare(pair.ai_draft_clean, pair.human_reply_text)
    pii_reply = pii.summary(pair.human_reply_text)
    pii_customer = pii.summary(pair.customer_text)

    front = {
        "title": f"Learned draft-vs-reply — ticket {pair.ticket_id}",
        "category": "learned",
        "status": "review_pending",
        "source_type": "agent_reply",
        "source_ticket_id": pair.ticket_id,
        "created_at": _now(),
        "review_pending": True,
        "similarity_hint": hint["ratio"],
        "similarity_band": hint["band"],
        "similarity_reliable": hint["reliable"],
        "reply_language": hint["reply_language"],
        "flags": pair.flags,
        "pii_findings_reply": pii_reply["by_kind"],
    }

    body = f"""## Customer situation

{pair.customer_text or "(no customer text captured)"}

## AI draft (internal note, cleaned)

{pair.ai_draft_clean}

## Human reply as sent

{pair.human_reply_text}

## Similarity hint (display only — NOT a gate)

- ratio: {hint['ratio']}  band: **{hint['band']}**  reliable: {hint['reliable']}
- {hint['note']}

## PII highlighter (best-effort — a human MUST still read for names)

- reply findings: {pii_reply['by_kind'] or 'none detected'}
- customer findings: {pii_customer['by_kind'] or 'none detected'}
- {pii_reply['warning']}

## Reviewer checklist (before promoting to kb/tickets/)

- [ ] This reply teaches something (not a macro, not "ok thanks").
- [ ] All PII removed / replaced with [order] [tracking] [email] [address] placeholders.
- [ ] Reply is correct and on-policy (no invented prices, refunds stay escalation-only).
- [ ] Generalise to a reusable pattern; fill in "Why".
"""

    text = "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True) + "---\n\n" + body
    path = _learned_path(pair.ticket_id)
    path.write_text(text, encoding="utf-8")
    return {"path": str(path), "similarity": hint["ratio"]}


def process_ticket(ticket_id: int, messages: list[dict], ticket: dict | None = None) -> dict:
    """Evaluate + (maybe) capture one ticket. Records the outcome in the ledger."""
    if store.already_processed(ticket_id):
        return {"ticket_id": ticket_id, "outcome": "skipped", "reason": "already_processed"}

    if _is_sensitive(ticket):
        store.mark_processed(ticket_id, "skipped", "sensitive")
        return {"ticket_id": ticket_id, "outcome": "skipped", "reason": "sensitive"}

    result = pairing.evaluate(ticket_id, messages)
    if isinstance(result, pairing.Skip):
        store.mark_processed(ticket_id, "skipped", result.reason)
        return {"ticket_id": ticket_id, "outcome": "skipped", "reason": result.reason,
                "detail": result.detail}

    packet = write_packet(result)
    store.mark_processed(ticket_id, "captured", "ok", packet["similarity"])
    return {"ticket_id": ticket_id, "outcome": "captured", "reason": "ok", **packet}


def run_poll(limit: int = 50) -> dict:
    """One poll pass: pull tickets updated since the cursor, process new ones,
    advance the cursor. Read-only against Gorgias; writes only local files/ledger."""
    if os.environ.get(config.LEGACY_OPT_IN_ENV) != "1":
        return {
            "scanned": 0,
            "captured": 0,
            "cursor": None,
            "outcomes": [],
            "disabled": True,
            "reason": "legacy_feedback_disabled",
        }

    cursor = store.get_cursor()
    if cursor and config.POLL_OVERLAP_SECONDS:
        # step the cursor back a little so boundary tickets are never missed
        try:
            dt = datetime.datetime.fromisoformat(cursor)
            dt -= datetime.timedelta(seconds=config.POLL_OVERLAP_SECONDS)
            cursor = dt.isoformat()
        except ValueError:
            pass

    tickets = gorgias_read.list_tickets_updated_since(cursor, limit=limit)
    outcomes = []
    high = store.get_cursor()
    for t in tickets:
        tid = int(t.get("id"))
        updated = str(t.get("updated_datetime", ""))
        if updated > high:
            high = updated
        if store.already_processed(tid):
            continue
        try:
            msgs = gorgias_read.get_messages(tid)
            outcomes.append(process_ticket(tid, msgs, ticket=t))
        except Exception as e:  # never let one bad ticket stop the pass
            outcomes.append({"ticket_id": tid, "outcome": "error", "reason": repr(e)[:120]})

    store.set_cursor(high)
    captured = sum(1 for o in outcomes if o.get("outcome") == "captured")
    return {"scanned": len(tickets), "captured": captured, "cursor": high, "outcomes": outcomes}


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "poll":
        print(json.dumps(run_poll(), indent=2))
    else:
        print("usage: python -m feedback.collector poll")
        print("state:", json.dumps(store.stats(), indent=2))
