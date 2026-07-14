"""similarity.py — a HINT, never a gate.

The adversary review was blunt: difflib.SequenceMatcher.ratio() is character-level
and language-blind, so it must NOT decide whether we capture or promote anything.
We keep it only to give a human reviewer a rough "how much did the human change the
draft?" signal, and we refuse to produce a band at all when the text isn't the kind
difflib can judge (non-English, or too short).

Bands (display only):
    close   ratio >= 0.75   -> "human barely changed the draft"
    partial 0.4..0.75       -> "human edited meaningfully"
    rewrite ratio < 0.4     -> "human largely rewrote — read carefully"
    n/a                     -> unreliable (non-English / too short) -> manual
"""
from __future__ import annotations

import difflib

from . import language


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


def compare(draft: str, reply: str) -> dict:
    """Return a hint dict. `reliable` False means: ignore the number, go manual."""
    draft = (draft or "").strip()
    reply = (reply or "").strip()
    draft_lang = language.detect(draft)
    reply_lang = language.detect(reply)

    reliable = (
        len(draft) >= 40
        and len(reply) >= 40
        and draft_lang["reliable_char_similarity"]
        and reply_lang["reliable_char_similarity"]
    )

    ratio = round(_ratio(draft.lower(), reply.lower()), 3)

    if not reliable:
        band = "n/a"
    elif ratio >= 0.75:
        band = "close"
    elif ratio >= 0.4:
        band = "partial"
    else:
        band = "rewrite"

    return {
        "ratio": ratio,
        "band": band,
        "reliable": reliable,
        "reply_language": reply_lang["primary"],
        "note": (
            "hint only — not used to gate capture or promotion; "
            "unreliable for non-English or short text"
        ),
    }
