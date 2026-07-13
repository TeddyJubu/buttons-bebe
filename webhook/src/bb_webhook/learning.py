"""Capture human-approved console actions as learning lessons for the KB.

When a human Sends a reply, posts an internal Note, or Requests an edit from the
console, we record a 'lesson' (the customer situation, the AI's draft, and the
human's final text) into the KB 'learned/' holding pen. A nightly job masks these
and promotes them into the indexed 'tickets/' exemplars, so over time the agent
mirrors the answers a human actually approved.

Raw packets in learned/ may contain PII; the folder is not indexed and files are
written 0600. Masking happens at promotion (auto_promote_learned.py).
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import secrets
import sys

_AGENT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

try:
    from feedback import config as _fc  # reuse the same KB folder config
    LEARNED_DIR = _fc.LEARNED_DIR
except Exception:  # pragma: no cover - fallback if feedback pkg unavailable
    LEARNED_DIR = _AGENT_ROOT / "KB" / "learned"

LEDGER = LEARNED_DIR / "_ledger.json"  # underscore => never indexed


def _now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")


def _write_unique_lesson(ticket_id: object, content: str) -> pathlib.Path:
    """Create a lesson without ever replacing another console action.

    A ticket can receive multiple actions inside one second (for example an
    internal note followed immediately by a public send).  The old timestamp-only
    name silently replaced the first action.  An exclusive create plus a random
    suffix makes the no-overwrite guarantee hold across threads and processes.
    """
    for _attempt in range(20):
        token = secrets.token_hex(6)
        out = LEARNED_DIR / f"lesson-{ticket_id}-{_now()}-{token}.md"
        try:
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # practically impossible, but never overwrite
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        return out
    raise FileExistsError("could not allocate a unique lesson filename")


def _bump_ledger(kind: str, edited: bool) -> None:
    try:
        LEARNED_DIR.mkdir(parents=True, exist_ok=True)
        data = {}
        if LEDGER.exists():
            data = json.loads(LEDGER.read_text() or "{}")
        data["total"] = data.get("total", 0) + 1
        data[kind] = data.get(kind, 0) + 1
        if kind == "sent":
            data["edited" if edited else "unchanged"] = (
                data.get("edited" if edited else "unchanged", 0) + 1
            )
        data["updated"] = _now()
        LEDGER.write_text(json.dumps(data))
    except Exception:
        pass


def record_lesson(kind, ticket_id, customer_message, ai_draft, final_text,
                  instruction="", customer_name="") -> bool:
    """Write a raw lesson packet to learned/. kind = sent | note | rewrite."""
    try:
        import yaml
        LEARNED_DIR.mkdir(parents=True, exist_ok=True)
        edited = bool(final_text and ai_draft
                      and final_text.strip() != ai_draft.strip())
        fm = {
            "title": f"lesson {kind} - ticket {ticket_id}",
            "kind": kind,
            "source_ticket_id": ticket_id,
            "edited": edited,
            "customer_name": customer_name or "",
            "captured": _now(),
            "review_pending": False,
        }
        body = (
            "## Customer situation\n\n" + (customer_message or "").strip() + "\n\n"
            "## AI draft\n\n" + (ai_draft or "").strip() + "\n\n"
            "## Human final (" + kind + ")\n\n" + (final_text or "").strip() + "\n"
        )
        if instruction:
            body += "\n## Rewrite instruction\n\n" + instruction.strip() + "\n"
        content = ("---\n"
                   + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
                   + "---\n\n" + body)
        _write_unique_lesson(ticket_id, content)
        _bump_ledger(kind, edited)
        return True
    except Exception:
        return False


def ledger() -> dict:
    try:
        if LEDGER.exists():
            return json.loads(LEDGER.read_text() or "{}")
    except Exception:
        pass
    return {}
