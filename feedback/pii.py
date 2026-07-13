"""pii.py — best-effort PII detection and masking, not a guarantee.

Read this before trusting it:
  * This catches PATTERNS (emails, phones, payment cards, order/tracking numbers,
    postal addresses, and URLs).
  * It does NOT discover unknown names. Call mask_with_known_values() when customer
    names are available; it supports Latin, Hebrew, and other Unicode scripts.
  * The console learning path auto-promotes nightly by architecture decision. It
    uses pattern masking plus the known Gorgias customer name, but remains
    best-effort: operators must not describe the resulting exemplar as guaranteed
    anonymous, and should review/purge it if unexpected PII is discovered.

The kb/tickets/ convention is to generalise identifiers to placeholders like
[order], [tracking], [email]. mask() produces that; findings() lists hits.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# order/tracking numbers: "#123456", "order 123456", "tracking 1Z...", long digit runs
_PATTERNS = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.I), "[email]"),
    ("url", re.compile(r"https?://[^\s)>\]]+", re.I), "[link]"),
    ("tracking", re.compile(r"\b(1Z[0-9A-Z]{16}|\d{12,22})\b"), "[tracking]"),
    ("order_hash", re.compile(r"#\s?\d{3,}"), "[order]"),
    ("order_word", re.compile(r"\b(order|order\s*#|order\s*no\.?)\s*[:#]?\s*\d{3,}\b", re.I), "[order]"),
    ("payment_card", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"), "[payment-card]"),
    ("po_box", re.compile(r"\bP\.?\s*O\.?\s+Box\s+[A-Z0-9-]+\b", re.I), "[address]"),
    ("street", re.compile(
        r"(?<!\w)\d{1,6}\s+(?:[\w.'’\-]+\s+){1,5}"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Way|Terrace|Ter|Place|Pl)\.?"
        r"(?:\s+(?:Apt|Apartment|Unit|Suite|#)\s*[\w-]+)?\b",
        re.I,
    ), "[address]"),
    ("postal_code", re.compile(
        r"\b(?:\d{5}(?:-\d{4})?|[A-Z]\d[A-Z][ -]?\d[A-Z]\d|"
        r"[A-Z]{1,2}\d[A-Z\d]?[ -]?\d[A-Z]{2})\b",
        re.I,
    ), "[postal-code]"),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d[\d\-\.\s()]{7,}\d)(?!\d)"), "[phone]"),
]

_GREETING_NAME = re.compile(
    r"(?im)\b(?P<greeting>hi|hello|dear)\s+"
    r"(?P<name>[\w'’\-]{2,}(?:\s+[\w'’\-]{2,}){0,2})(?=[,!])"
)
_NON_NAME_GREETINGS = frozenset({"all", "everyone", "friend", "team", "there"})


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
    # Console replies commonly greet a customer by first name. Older lesson
    # packets did not always carry the structured customer_name field, so mask
    # greeting-shaped names as a conservative fallback.
    def _mask_greeting(match: re.Match[str]) -> str:
        name = match.group("name")
        if name.strip().casefold() in _NON_NAME_GREETINGS:
            return match.group(0)
        return f"{match.group('greeting')} [name]"

    out = _GREETING_NAME.sub(_mask_greeting, out)
    return out


def mask_with_known_values(
    text: str,
    *,
    customer_names: Iterable[str] = (),
) -> str:
    """Mask patterns plus customer names supplied by an authoritative source.

    Full names are replaced first, followed by non-trivial component tokens.  The
    token pass catches greetings that use only a first name.  Unicode-aware word
    boundaries cover Hebrew and other scripts supported by Python's regex engine.
    """
    out = mask(text)
    names = [str(value).strip() for value in customer_names if str(value).strip()]
    candidates: list[str] = []
    for name in names:
        candidates.append(name)
        candidates.extend(token for token in re.split(r"[\s,]+", name) if len(token) >= 3)
    for value in sorted(set(candidates), key=len, reverse=True):
        out = re.sub(r"(?<!\w)" + re.escape(value) + r"(?!\w)", "[name]", out, flags=re.I)
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
        "warning": (
            "unknown names are NOT detected; use mask_with_known_values when known "
            "customer names are available, and treat all masking as best-effort"
        ),
    }
