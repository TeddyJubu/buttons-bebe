"""
scrub_pii.py — replaces PII tokens in text before writing to logs or notifications.

Covers: email addresses, US/international phone numbers, order IDs (when
preceded by "order", "#", or "order #"). Safe to call on any string — never
raises, returns the input unchanged if it's falsy.

Usage
-----
    from skills.scrub_pii import scrub

    clean = scrub("Call us at 555-867-5309 or email alice@example.com")
    # → "Call us at [PHONE] or email [EMAIL]"
"""

import re

_RE_EMAIL = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
)
_RE_PHONE = re.compile(
    r'\b(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b'
)
# Order IDs: match digits when preceded by "order", "#", or "order #"
_RE_ORDER = re.compile(
    r'(?:order\s*#?\s*|#\s*)(\d{4,10})\b',
    re.IGNORECASE,
)


def scrub(text: str) -> str:
    """Replace PII tokens with typed placeholders. Returns input unchanged if falsy."""
    if not text:
        return text
    text = _RE_EMAIL.sub('[EMAIL]', text)
    text = _RE_PHONE.sub('[PHONE]', text)
    text = _RE_ORDER.sub(lambda m: m.group(0).replace(m.group(1), '[ORDER_ID]'), text)
    return text
