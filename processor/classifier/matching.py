"""Bounded, log-safe matching helpers."""

from __future__ import annotations

import re


_MAX_CONTEXT_SCAN = 100_000


def _find_matches(text: str, patterns: list[str]) -> list[str]:
    text_lower = text.lower()
    found: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            hit = " ".join(match.group(0).split())
            if hit and hit not in found:
                found.append(hit)
    return found


def _find_matches_any(views: list[str], patterns: list[str]) -> list[str]:
    found: list[str] = []
    for view in views:
        for hit in _find_matches(view, patterns):
            if hit not in found:
                found.append(hit)
    return found


def _search_any(views: list[str], pattern: re.Pattern) -> re.Match | None:
    for view in views:
        match = pattern.search(view)
        if match:
            return match
    return None


def _find_collapsed(text: str, phrase: str) -> tuple[int, int]:
    if not phrase or not text:
        return -1, -1
    hay = text[:_MAX_CONTEXT_SCAN].lower()
    needle = phrase.lower()
    index = hay.find(needle)
    if index >= 0:
        return index, index + len(needle)

    chars: list[str] = []
    offsets: list[int] = []
    previous_space = True
    for index, char in enumerate(hay):
        if char.isspace():
            if previous_space:
                continue
            chars.append(" ")
            offsets.append(index)
            previous_space = True
        else:
            chars.append(char)
            offsets.append(index)
            previous_space = False
    index = "".join(chars).find(needle)
    if index < 0:
        return -1, -1
    last = offsets[min(index + len(needle), len(offsets)) - 1]
    return offsets[index], last + 1


def _match_context(text: str, phrase: str, window: int = 30) -> str:
    index, stop = _find_collapsed(text, phrase)
    if index < 0:
        return phrase
    start = max(0, index - window)
    end = min(len(text), stop + window)
    excerpt = " ".join(text[start:end].split())
    return f"...{excerpt}..." if (start or end < len(text)) else excerpt


__all__ = [
    "_MAX_CONTEXT_SCAN", "_find_matches", "_find_matches_any", "_search_any",
    "_find_collapsed", "_match_context",
]
