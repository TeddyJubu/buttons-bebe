# =============================================================================
#  deploy/vps-patches/draft_cleaner.py
#
#  EXACT COPY of fable/server/app/draft_cleaner.py — DO NOT EDIT HERE.
#  This is the file that ships to the live VPS processor
#  (/root/Buttonsbebe Agent/processor/draft_cleaner.py).
#
#  KEEP IN SYNC: the source of truth is fable/server/app/draft_cleaner.py.
#  If you change the cleaner, re-copy the whole module below (it is stdlib-only,
#  so it drops straight into the processor with no new dependencies).
#  Verified stdlib-only: the only imports are `re` and `dataclasses`.
# =============================================================================
"""Shared draft cleaner — the ONE module both tracks use (feature F2).

Origin: copied verbatim from fable/server/app/draft_cleaner.py @ Fable_buttonsbebe
        (Sprint 2, Stream V / item V2). This is a SHIP COPY for the live VPS
        processor; the source of truth stays in fable/. Do not fork the logic.

Contract (stable — other modules import these exact names):

    clean_draft(text: str) -> CleanResult     # clean an AI-produced draft
    should_draft(message: str) -> ShouldDraft # gate on the CUSTOMER message

Owned by Stream V. Stream B (brains/pipeline) imports it. The VPS patch package
(deploy/vps-patches/) ships a copy of this same file for the live processor.

Fixes the real QA failures:
  QA #01/#04/#10 — the model appends self-commentary ("The response above was
                   complete...") or repeats the entire draft twice (sometimes
                   separated by a blank line, sometimes just a newline).
  QA #19         — an empty customer message got a fabricated reply.

Design notes (why it is built this way):
  * clean_draft() runs on the AI DRAFT, in two conservative passes:
      1. cut trailing self-commentary from the first "self-talk" marker line on;
      2. collapse a draft that is the same content repeated 2x or 3x back to one.
    Both passes are deliberately hard to trigger by accident so a NORMAL reply
    (even one that says the word "complete" or "note" in the middle of a
    sentence) passes through UNCHANGED — see the tests.
  * should_draft() runs on the CUSTOMER MESSAGE and returns ok=False when there
    is simply nothing to answer (empty / whitespace / a bare "thanks" / an
    emoji / punctuation). The pipeline must then create NO draft.

Stdlib only (re, dataclasses) so the VPS copy has zero dependencies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Self-talk markers.
#
# Each pattern is anchored to the START of a (stripped) line. When a line begins
# with one of these, that line and EVERYTHING AFTER IT is treated as the model
# talking to itself / the reviewer and is cut. The patterns require the tell-tale
# phrase at the line start, so ordinary prose that merely contains the words
# "complete" or "note" somewhere in a sentence is never affected.
#
# Extend this list as new leak patterns show up in QA.
# ---------------------------------------------------------------------------
_SELF_TALK_MARKERS = [
    # "The response above was complete..."  (QA #01/#04/#10)
    r"the response above was complete",
    r"the (?:previous|prior) (?:response|reply|draft) (?:was|is) (?:already )?complete",
    # "The above response addresses the question."
    r"the above (?:response|reply|draft) ",
    # "This reply is complete." / "This response above is complete."
    r"this (?:response|reply|draft) (?:above )?(?:is|was) (?:now )?complete",
    # "I have completed the response." / "I have now finished this draft."
    r"i have (?:now )?(?:completed|finished) (?:the|this|my) (?:response|reply|draft)",
    # "Note to the reviewer:" / "Note to the team" (self-addressed hand-off).
    # NB: this is NOT "Internal note for human review" (a legit escalation note)
    # — that starts with "internal"/"notes for" and is left untouched.
    r"note to (?:the )?(?:agent|reviewer|team)\b",
    # "End of response" / "[End of draft]"
    r"\[?end of (?:response|reply|draft)\]?",
    # "As an AI, I cannot ..." style refusals leaking into a draft.
    r"as an ai\b.*\bi (?:cannot|can't|am unable)",
]
_MARKER_RE = re.compile(
    r"^[\s>*#\-]*(?:" + "|".join(_SELF_TALK_MARKERS) + r")",
    re.IGNORECASE,
)

# A repeated block must be at least this many normalised characters before we
# treat it as a genuine duplication. Keeps short, legitimately-repeated content
# (e.g. "Yes.\n\nYes.") from being collapsed.
_MIN_DUP_CHARS = 40


@dataclass
class CleanResult:
    text: str
    no_draft: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class ShouldDraft:
    ok: bool
    reason: str = ""


def _cut_self_talk(text: str) -> tuple[str, bool]:
    """Cut everything from the first self-talk marker line onward.

    Returns (possibly-trimmed text, whether anything was cut).
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _MARKER_RE.match(line.strip()):
            return "\n".join(lines[:i]).rstrip(), True
    return text, False


def _normalise(block: str) -> str:
    """Whitespace-flatten + lowercase, so two copies that differ only in spacing
    or newlines compare equal."""
    return re.sub(r"\s+", " ", block).strip().lower()


def _repeated_unit(norm: str, k: int) -> str | None:
    """If `norm` (already whitespace-normalised) is exactly ``k`` copies of a base
    string — optionally single-space-joined, because normalisation turns any
    separator (blank line, newline, spaces) between the copies into one space —
    return that base string; otherwise None.

    Only meaningful (>= _MIN_DUP_CHARS) bases count, so we never collapse a tiny
    accidental repeat.
    """
    n = len(norm)
    # Prefer the "joined by one space" reading (the usual case), then the
    # "no separator at all" reading.
    for sep in (1, 0):
        if (n - sep * (k - 1)) % k != 0:
            continue
        unit_len = (n - sep * (k - 1)) // k
        if unit_len < _MIN_DUP_CHARS:
            continue
        base = norm[:unit_len]
        if norm == (" " * sep).join([base] * k):
            return base
    return None


def _raw_prefix_for_norm(raw: str, base: str) -> str | None:
    """Return the shortest prefix of `raw` whose normalisation equals `base`.

    We rebuild the normalised form one character at a time and stop at the exact
    raw offset where the first copy ends. That keeps the first copy in its
    ORIGINAL formatting (capitalisation, line breaks) instead of the flattened
    lower-cased form.
    """
    out: list[str] = []
    prev_space = True  # leading whitespace is dropped by _normalise
    for idx, ch in enumerate(raw):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                prev_space = True
        else:
            out.append(ch.lower())
            prev_space = False
            # compare only once we could plausibly have reached `base`
            if len(out) >= len(base):
                cur = "".join(out).rstrip()
                if cur == base:
                    return raw[: idx + 1]
                if len(cur) > len(base):
                    return None
    return None


def _dedupe_repeats(text: str) -> tuple[str, bool]:
    """If the whole draft is the same content repeated 2x or 3x, keep one copy.

    Works no matter how the copies are separated (blank line, single newline, or
    a single space) because it compares the whitespace-normalised whole text.
    Returns (possibly-shortened text, whether a duplicate was removed).
    """
    stripped = text.strip()
    norm = _normalise(stripped)
    if len(norm) < _MIN_DUP_CHARS:
        return stripped, False

    for k in (2, 3):
        base = _repeated_unit(norm, k)
        if base is None:
            continue
        raw_first = _raw_prefix_for_norm(stripped, base)
        if raw_first is not None:
            return raw_first.strip(), True
        # Fallback (should be rare): split on blank lines and keep the first
        # 1/k of the paragraphs when they divide evenly.
        paras = [p for p in re.split(r"\n\s*\n", stripped) if p.strip()]
        if paras and len(paras) % k == 0:
            keep = paras[: len(paras) // k]
            return "\n\n".join(keep).strip(), True

    return stripped, False


def clean_draft(text: str) -> CleanResult:
    """Clean an AI draft before it is shown to a human / posted anywhere."""
    if text is None or not str(text).strip():
        return CleanResult(text="", no_draft=True, reasons=["empty draft"])

    out = str(text)
    reasons: list[str] = []

    out, cut = _cut_self_talk(out)
    if cut:
        reasons.append("stripped model self-commentary")

    out, deduped = _dedupe_repeats(out)
    if deduped:
        reasons.append("removed duplicated draft body")

    out = out.strip()
    if not out:
        return CleanResult(
            text="", no_draft=True,
            reasons=reasons + ["nothing left after cleaning"],
        )
    return CleanResult(text=out, no_draft=False, reasons=reasons)


# --- customer-message gate (QA #19) ----------------------------------------
#
# A message made up ONLY of thanks / acknowledgement / emoji / punctuation has
# nothing to answer. The pattern must match the WHOLE (stripped) message.
#
# The token set is deliberately safe to broaden: the pattern must match the
# WHOLE message and never includes "?" or real content words, so any genuine
# question or request (which contains a "?" or a non-ack word) still returns
# ok=True. Only a message made ENTIRELY of thanks/ack tokens is suppressed.
_SURVEY_PAT = re.compile(
    r"^(?:thanks|thank you|thank u|thx|ty|ok(?:ay)?|k|great|awesome|perfect|"
    r"got it|received|no worries|cheers|much appreciated|appreciate it|"
    r"appreciate|appreciated|so|much|really|everything|the help|your help|"
    r"guys|team|again|all|for|your|the|it|👍|🙏|❤️|😊|\.|!|,|~|\s)+$",
    re.IGNORECASE,
)


def should_draft(message: str) -> ShouldDraft:
    """Return ok=False when there is nothing to answer (empty / whitespace /
    bare thanks / emoji / punctuation-only). The pipeline must then draft NOTHING.
    Any real question or request returns ok=True.
    """
    if message is None:
        return ShouldDraft(False, "empty message")
    m = str(message).strip()
    if not m:
        return ShouldDraft(False, "empty message")
    # Short and not alphanumeric -> punctuation / a lone emoji.
    if len(m) <= 2 and not m.isalnum():
        return ShouldDraft(False, "punctuation-only message")
    if _SURVEY_PAT.match(m):
        return ShouldDraft(False, "no question to answer (thanks/ack only)")
    return ShouldDraft(True)
