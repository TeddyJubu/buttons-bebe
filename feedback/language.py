"""language.py — cheap script detection so non-English replies are handled honestly.

We do NOT try to be a real language classifier. We only answer one question that
the reviewer flagged (C3): "is this reply mostly non-Latin (e.g. Hebrew)?" — in
which case the character-level similarity hint is meaningless and the pair must go
to manual review with the score suppressed.

Buttons Bebe gets Hebrew tickets; Hebrew is U+0590–U+05FF.
"""
from __future__ import annotations

import unicodedata


def _script_counts(text: str) -> dict:
    latin = hebrew = other = 0
    for ch in text:
        if not ch.isalpha():
            continue
        code = ord(ch)
        if 0x0590 <= code <= 0x05FF:
            hebrew += 1
        else:
            name = unicodedata.name(ch, "")
            if name.startswith("LATIN"):
                latin += 1
            else:
                other += 1
    return {"latin": latin, "hebrew": hebrew, "other": other}


def detect(text: str) -> dict:
    """Return {'primary': 'en'|'he'|'other'|'unknown', 'latin_ratio': float,
    'reliable_char_similarity': bool}."""
    counts = _script_counts(text or "")
    total = counts["latin"] + counts["hebrew"] + counts["other"]
    if total == 0:
        return {"primary": "unknown", "latin_ratio": 0.0, "reliable_char_similarity": False}
    latin_ratio = counts["latin"] / total
    if counts["hebrew"] > counts["latin"]:
        primary = "he"
    elif latin_ratio >= 0.6:
        primary = "en"
    else:
        primary = "other"
    # char-level difflib only means anything for mostly-Latin text
    reliable = primary == "en" and latin_ratio >= 0.6
    return {
        "primary": primary,
        "latin_ratio": round(latin_ratio, 3),
        "reliable_char_similarity": reliable,
    }
