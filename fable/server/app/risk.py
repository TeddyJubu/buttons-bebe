"""Deterministic risk classifier (code, not LLM) — API contract §2.

Sensitive if the message text matches any trigger word/phrase (case-insensitive)
or an "angry signal": 3+ consecutive exclamation marks, or >= 6 ALL-CAPS words.
Extend by editing SENSITIVE_WORDS.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

# Word / phrase triggers (case-insensitive substring match).
SENSITIVE_WORDS = [
    "refund",
    "chargeback",
    "charge back",
    "dispute",
    "damaged",
    "broken",
    "wrong item",
    "wrong order",
    "missing",
    "never arrived",
    "didn't arrive",
    "hasn't arrived",
    "lost",
    "scam",
    "fraud",
    "lawyer",
    "attorney",
    "legal action",
    "sue",
]

_EXCLAIM_RE = re.compile(r"!{3,}")
_CAPS_WORD_RE = re.compile(r"\b[A-Z]{2,}\b")


def classify(body_text: str) -> Tuple[str, Optional[str]]:
    """Return (risk, reason). risk in {"low", "sensitive"}."""
    text = body_text or ""
    low = text.lower()

    for word in SENSITIVE_WORDS:
        if word in low:
            return "sensitive", f"mentions '{word}'"

    if _EXCLAIM_RE.search(text):
        return "sensitive", "excessive exclamation (!!!)"

    caps_words = _CAPS_WORD_RE.findall(text)
    # Ignore very short all-caps tokens already excluded by {2,}; count words.
    if len(caps_words) >= 6:
        return "sensitive", "shouting (all-caps message)"

    return "low", None


def is_sensitive(body_text: str) -> bool:
    return classify(body_text)[0] == "sensitive"
