"""Shared sensitivity taxonomy for knowledge-base indexing.

The risk label is deliberately tag-driven and fail-safe: content authors can use
spaces, underscores, or hyphens and receive the same result. Keep this list aligned
with the non-negotiable safety rules in AGENTS.md and the Hermes support skill.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


SENSITIVE_TAGS = frozenset(
    {
        "sensitive",
        "escalation",
        "escalate",
        "immediate",
        "refund",
        "refund-window",
        "chargeback",
        "dispute",
        "damaged",
        "defect",
        "wrong-item",
        "missing-item",
        "missing-accessory",
        "angry-customer",
        "upset-customer",
        "manager-request",
        "privacy",
        "cancel",
        "cancellation",
        "cancellations",
        "address-change",
    }
)


def _normalize_tag(tag: object) -> str:
    return re.sub(r"[\s_]+", "-", str(tag).strip().lower()).strip("-")


def normalize_tags(tags: Iterable[object] | str | None) -> set[str]:
    """Return canonical lowercase, hyphenated tags.

    A comma-delimited scalar is accepted because malformed front matter should not
    silently bypass a safety classification.
    """
    if tags is None:
        return set()
    if isinstance(tags, str):
        values = tags.split(",")
    else:
        try:
            iter(tags)
        except TypeError:
            values = (tags,)
        else:
            values = tags
    return {normalized for value in values if (normalized := _normalize_tag(value))}


def is_sensitive_tags(tags: Iterable[object] | str | None) -> bool:
    return bool(normalize_tags(tags) & SENSITIVE_TAGS)
