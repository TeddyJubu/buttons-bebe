"""pairing.py — decide what (if anything) is worth learning from a ticket.

This is the module the adversary review hit hardest, so the logic is explicit and
conservative. It answers: for one ticket's message list, is there a trustworthy
(AI draft, human sent reply) pair — and if not, exactly why we skipped.

Gorgias message signals we rely on (confirm in the task-0 spike):
  * from_agent : bool  — staff-authored vs customer
  * public     : bool  — internal note (False) vs customer-facing reply (True)
So:
  * AI draft        = from_agent True  AND public False   (an internal note)
  * human sent reply= from_agent True  AND public True     (outbound to customer)
  * customer message= from_agent False

We NEVER decide capture on text similarity. Similarity is attached later as a hint.
Everything captured is review_pending — the human is the real gate (handles the
"did the human actually derive from the draft?" ambiguity, C2).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config, text_clean

# ------------------------------- field access ------------------------------ #


def body_of(msg: dict) -> str:
    for key in ("stripped_text", "body_text", "text", "body_html"):
        v = msg.get(key)
        if v and str(v).strip():
            return str(v)
    return ""


def sender_email(msg: dict) -> str:
    s = msg.get("sender") or {}
    if isinstance(s, dict):
        return str(s.get("email", "")).lower()
    return ""


def sender_id(msg: dict) -> str:
    s = msg.get("sender") or {}
    if isinstance(s, dict):
        return str(s.get("id", ""))
    return ""


def is_internal_note(msg: dict) -> bool:
    if msg.get("public") is False and msg.get("from_agent"):
        return True
    # fallback if `public` absent: some payloads label the channel
    ch = str(msg.get("channel", "")).lower()
    return msg.get("from_agent") and ("internal" in ch or ch == "internal-note")


def is_public_agent_reply(msg: dict) -> bool:
    return bool(msg.get("from_agent") and msg.get("public") is True)


def is_from_bot(msg: dict) -> bool:
    if config.AGENT_BOT_EMAIL and sender_email(msg) == config.AGENT_BOT_EMAIL:
        return True
    if config.AGENT_BOT_USER_ID and sender_id(msg) == config.AGENT_BOT_USER_ID:
        return True
    return False


def _ts(msg: dict) -> str:
    return str(msg.get("created_datetime") or msg.get("created_at") or "")


# ------------------------------- macro check ------------------------------- #


def _load_macro_signatures() -> list[str]:
    fp = config.MACRO_SIGNATURES_FILE
    if not fp.exists():
        return []
    out = []
    for line in fp.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s.lower())
    return out


def looks_like_macro(msg: dict, text: str) -> bool:
    """Best-effort: Gorgias metadata first, then a configurable signature list."""
    for key in ("macros", "rule_ids", "macro_ids"):
        v = msg.get(key)
        if v:
            return True
    src = msg.get("source") or msg.get("meta") or {}
    if isinstance(src, dict):
        stype = str(src.get("type", "")).lower()
        if stype in ("rule", "macro", "automation"):
            return True
    low = (text or "").lower()
    for sig in _load_macro_signatures():
        if sig and sig in low:
            return True
    return False


# --------------------------------- result ---------------------------------- #


@dataclass
class Pair:
    ticket_id: int
    customer_text: str
    ai_draft_raw: str
    ai_draft_clean: str
    human_reply_text: str
    draft_ts: str
    reply_ts: str
    multi_turn: bool = False
    flags: list[str] = field(default_factory=list)


@dataclass
class Skip:
    ticket_id: int
    reason: str
    detail: str = ""


def evaluate(ticket_id: int, messages: list[dict]) -> Pair | Skip:
    """Return a Pair to capture, or a Skip explaining why not."""
    msgs = sorted(messages or [], key=_ts)

    customer_msgs = [m for m in msgs if not m.get("from_agent")]
    notes = [m for m in msgs if is_internal_note(m)]
    public_replies = [m for m in msgs if is_public_agent_reply(m) and not is_from_bot(m)]

    # the AI draft: prefer the bot's note; else the first internal note
    draft_msg = None
    for m in notes:
        if is_from_bot(m):
            draft_msg = m
            break
    if draft_msg is None and notes:
        draft_msg = notes[0]

    if draft_msg is None:
        return Skip(ticket_id, "no_ai_draft", "no internal-note draft on this ticket")

    draft_raw = body_of(draft_msg)
    draft_clean = text_clean.clean_draft(draft_raw)
    if not draft_clean.strip():
        return Skip(ticket_id, "empty_draft", "AI draft had no usable text")

    # the human reply: first public agent reply AFTER the draft
    reply_msg = None
    for m in public_replies:
        if _ts(m) >= _ts(draft_msg):
            reply_msg = m
            break
    if reply_msg is None:
        return Skip(ticket_id, "no_human_reply", "no human reply sent after the draft")

    reply_text = text_clean.normalize(body_of(reply_msg))
    if not reply_text.strip():
        return Skip(ticket_id, "empty_reply", "human reply had no usable text")

    if looks_like_macro(reply_msg, reply_text):
        return Skip(ticket_id, "macro", "human reply looks like a saved macro/template")

    multi_turn = len(customer_msgs) > 1 or len(public_replies) > 1
    if multi_turn and not config.CAPTURE_MULTI_TURN:
        return Skip(ticket_id, "multi_turn", "multi-message thread — out of v1 scope")

    flags: list[str] = []
    if multi_turn:
        flags.append("multi_turn")
    if draft_msg is notes[0] and not is_from_bot(draft_msg) and not config.AGENT_BOT_EMAIL:
        flags.append("draft_identity_unverified")

    customer_text = text_clean.normalize(body_of(customer_msgs[0])) if customer_msgs else ""

    return Pair(
        ticket_id=ticket_id,
        customer_text=customer_text,
        ai_draft_raw=draft_raw,
        ai_draft_clean=draft_clean,
        human_reply_text=reply_text,
        draft_ts=_ts(draft_msg),
        reply_ts=_ts(reply_msg),
        multi_turn=multi_turn,
        flags=flags,
    )
