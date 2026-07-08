"""pii.py — a PII HIGHLIGHTER, not a guarantee.

Read this before trusting it:
  * This catches PATTERNS (emails, phones, order/tracking numbers, addresses, URLs).
  * It does NOT reliably catch names — names are unbounded, and Hebrew/other-script
    names never match Latin patterns. Do not believe the "clean" state.
  * Its job is to make the human reviewer FASTER and more vigilant, by masking the
    obvious stuff and listing what it found. The human gate in review_learned.py is
    the real control. Never auto-promote on the strength of this module.

The kb/tickets/ convention is to generalise identifiers to placeholders like
[order], [tracking], [email]. mask() produces that; findings() lists hits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# order/tracking numbers: "#123456", "order 123456", "tracking 1Z...", long digit runs
_PATTERNS = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.I), "[email]"),
    ("url", re.compile(r"https?://[^\s)>\]]+", re.I), "[link]"),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d[\d\-\.\s()]{7,}\d)(?!\d)"), "[phone]"),
    ("tracking", re.compile(r"\b(1Z[0-9A-Z]{16}|\d{12,22})\b"), "[tracking]"),
    ("order_hash", re.compile(r"#\s?\d{3,}"), "[order]"),
    ("order_word", re.compile(r"\b(order|order\s*#|order\s*no\.?)\s*[:#]?\s*\d{3,}\b", re.I), "[order]"),
    ("zip", re.compile(r"\b\d{5}(?:-\d{4})?\b"), "[zip]"),
    ("street", re.compile(
        r"\b\d{1,6}\s+([A-Z][a-z]+\s){1,3}"
        r"(Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Way|Terrace|Ter|Place|Pl)\b",
        re.I,
    ), "[address]"),
]


@dataclass
class Finding:
    kind: str
    text: str


def findings(text: str) -> list[Finding]:
    """List every pattern hit (deduped, order preserved)."""
    seen: set[tuple[str, str]] = set()
    out: list[Finding] = []
    for kind, rx, _ph in _PATTERNS:
        for m in rx.finditer(text or ""):
            key = (kind, m.group(0))
            if key in seen:
                continue
            seen.add(key)
            out.append(Finding(kind=kind, text=m.group(0)))
    return out


def mask(text: str) -> str:
    """Replace obvious identifiers with [placeholders]. Best-effort only."""
    out = text or ""
    # order-by-word before bare digit runs so the longer match wins
    for kind, rx, ph in _PATTERNS:
        out = rx.sub(ph, out)
    return out


def summary(text: str) -> dict:
    f = findings(text)
    by_kind: dict[str, int] = {}
    for item in f:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
    return {
        "total": len(f),
        "by_kind": by_kind,
        "findings": [(x.kind, x.text) for x in f],
        "warning": "names are NOT detected — a human must still read for names/PII",
    }
