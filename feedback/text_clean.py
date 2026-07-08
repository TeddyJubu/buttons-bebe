"""text_clean.py — tidy raw message text before comparing / storing.

Two jobs:
  1. clean_draft(): strip the glm-5.2 self-commentary tails and de-duplicate the
     repeated-answer blocks (DEV-ISSUES open item #5) so they don't skew the
     similarity hint or pollute what a reviewer reads.
  2. normalize(): light whitespace/quote normalisation shared by both sides.

Deliberately conservative: if unsure, keep text rather than delete it.
"""
from __future__ import annotations

import re

# Markers glm-5.2 tends to append after it has already answered.
_TRAILING_MARKERS = [
    "the response above was complete",
    "the previous response was already complete",
    "the response above is complete",
    "this response is complete",
    "the answer above",
    "let me know if you need anything else from me as the assistant",
]


def normalize(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _cut_at_markers(text: str) -> str:
    low = text.lower()
    cut = len(text)
    for m in _TRAILING_MARKERS:
        i = low.find(m)
        if i != -1:
            cut = min(cut, i)
    return text[:cut].rstrip()


def _dedupe_repeated_block(text: str) -> str:
    """glm sometimes emits the whole answer twice. If the second half is a near-
    copy of the first, keep only the first."""
    t = text.strip()
    n = len(t)
    if n < 80:
        return t
    half = n // 2
    # find a split near the midpoint on a paragraph boundary
    split = t.rfind("\n\n", 0, half + 40)
    if split == -1 or split < half - 60:
        return t
    first, second = t[:split].strip(), t[split:].strip()
    if not second:
        return first
    # crude containment check: is `second` essentially the start of `first` again?
    a = re.sub(r"\s+", " ", first.lower())
    b = re.sub(r"\s+", " ", second.lower())
    shorter = min(len(a), len(b))
    if shorter and (a[:shorter] == b[:shorter] or b in a or a in b):
        return first
    return t


def clean_draft(text: str) -> str:
    """Full cleanup for an AI draft before it is scored or shown."""
    return normalize(_dedupe_repeated_block(_cut_at_markers(normalize(text))))
