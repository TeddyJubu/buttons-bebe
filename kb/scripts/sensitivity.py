"""Shared sensitivity taxonomy for knowledge-base indexing.

The risk label is deliberately tag-driven and fail-safe: content authors can use
spaces, underscores, or hyphens and receive the same result. Keep this list aligned
with the non-negotiable safety rules in AGENTS.md and the Hermes support skill.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


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


_TRUE_VALUES = frozenset({"1", "true", "yes", "sensitive"})
_FALSE_VALUES = frozenset({"0", "false", "no", "normal"})


def is_sensitive_metadata(metadata: Mapping[str, object] | None) -> bool:
    """Classify a front-matter mapping without silently ignoring safety fields.

    Tags remain the canonical taxonomy, while an explicit ``sensitive: true``
    is a fail-safe override. Invalid shapes abort the rebuild so the existing
    last-known-good index stays live instead of publishing downgraded labels.
    """
    metadata = metadata or {}
    raw_tags = metadata.get("tags", [])
    if isinstance(raw_tags, Mapping) or isinstance(raw_tags, (bytes, bytearray)):
        raise ValueError("tags metadata must be a string or a list of strings")
    if raw_tags is not None and not isinstance(
        raw_tags, (str, list, tuple, set, frozenset)
    ):
        raise ValueError("tags metadata must be a string or a list of strings")
    if not isinstance(raw_tags, str) and raw_tags is not None:
        if any(not isinstance(tag, str) for tag in raw_tags):
            raise ValueError("tags metadata must contain only strings")

    explicit = False
    if "sensitive" in metadata:
        raw_sensitive = metadata["sensitive"]
        if isinstance(raw_sensitive, bool):
            explicit = raw_sensitive
        elif isinstance(raw_sensitive, str):
            normalized = raw_sensitive.strip().lower()
            if normalized in _TRUE_VALUES:
                explicit = True
            elif normalized in _FALSE_VALUES:
                explicit = False
            else:
                raise ValueError("sensitive metadata must be true or false")
        else:
            raise ValueError("sensitive metadata must be true or false")

    return explicit or is_sensitive_tags(raw_tags)
