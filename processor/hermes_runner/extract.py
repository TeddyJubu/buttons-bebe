"""Strict, token-authenticated extraction of Hermes output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from logging_setup import get_logger, log_event
from shared.priority import RANK

from .constants import (
    _ACTION_SEVERITY,
    _ALLOWED_ACTIONS,
    _MAX_JSON_BLOCK,
    _MAX_REASON,
    _MAX_VERDICT_CANDIDATES,
    _UNKNOWN_ACTION_SEVERITY,
    _token_failure_result,
)


logger = get_logger(__name__)
_NEVER = re.compile(r"(?!)")


# ADR-015 §2.1 — only exact per-run tokens authenticate drafts and verdicts.
def _json_marker_re(token: str | None) -> re.Pattern[str]:
    """Return only an exact-token JSON marker pattern.

    Empty tokens intentionally produce a never-match pattern. There is no
    untagged compatibility regex because untagged output is unauthenticated.
    """

    if not token:
        return _NEVER
    return re.compile(
        r"JSON_RESULT\[" + re.escape(str(token)) + r"\]:\s*(\{)"
    )


def _draft_tag_re(token: str | None) -> re.Pattern[str]:
    """Return only an exact-token draft tag pattern."""

    if not token:
        return _NEVER
    escaped = re.escape(str(token))
    return re.compile(
        r"<DRAFT:" + escaped
        + r">((?:(?!</?DRAFT(?::|>)).)*?)</DRAFT:" + escaped + r">",
        re.DOTALL,
    )


def _extract_json_block(text: str, start_pos: int) -> str | None:
    """Extract one bounded balanced JSON object from a marker.

    Slices to the size bound first, so a truncated block raises
    ``JSONDecodeError`` in the caller and the candidate is skipped —
    the same fail-closed path as before.
    """

    decoder = json.JSONDecoder()
    try:
        parsed, end_index = decoder.raw_decode(text, start_pos)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if end_index - start_pos > _MAX_JSON_BLOCK:
        return None
    return text[start_pos:end_index]


def _as_bool(value: Any) -> bool:
    """Coerce model output without treating the string ``false`` as true."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _valid_verdicts(
    output: str,
    customer_text: str | None = None,
    token: str | None = None,
) -> tuple[list[tuple[re.Match[str], dict[str, Any]]], int, int]:
    """Collect valid JSON verdicts carrying the exact expected run token.

    ``customer_text`` remains in the transitional signature, but is ignored:
    token ownership replaces echo heuristics completely.
    """

    del customer_text
    if not token:
        return [], 0, 0
    text = str(output or "")
    candidates = list(_json_marker_re(token).finditer(text))
    marker_count = len(candidates)
    if marker_count > _MAX_VERDICT_CANDIDATES:
        log_event(
            logger,
            "WARNING",
            "Absurd number of tokenized JSON_RESULT markers - failing closed",
            markers=marker_count,
            limit=_MAX_VERDICT_CANDIDATES,
        )
        return [], marker_count, 0

    required = {"priority", "reason", "action", "notify_owner"}
    blocks: list[tuple[re.Match[str], dict[str, Any]]] = []
    for candidate in candidates:
        raw_json = _extract_json_block(text, candidate.start(1))
        if not raw_json:
            continue
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict) or not required.issubset(parsed):
            continue
        if str(parsed["priority"]).lower().strip() not in RANK:
            continue
        if not isinstance(parsed.get("reason"), str):
            continue
        blocks.append((candidate, parsed))
    return blocks, marker_count, 0


def _merge_verdicts(
    blocks: list[tuple[re.Match[str], dict[str, Any]]]
) -> dict[str, Any]:
    """Merge trusted verdicts by taking the most cautious interpretation."""

    best = max(
        blocks,
        key=lambda block: RANK.get(
            str(block[1]["priority"]).lower().strip(), -1
        ),
    )[1]
    merged: dict[str, Any] = {
        "priority": best.get("priority"),
        "reason": best.get("reason"),
        "action": best.get("action"),
        "notify_owner": best.get("notify_owner"),
    }
    merged["notify_owner"] = any(
        _as_bool(payload.get("notify_owner")) for _match, payload in blocks
    )

    def severity(action: str) -> int:
        return _ACTION_SEVERITY.get(action, _UNKNOWN_ACTION_SEVERITY)

    actions = [
        str(payload.get("action", "")).lower().strip()
        for _match, payload in blocks
    ]
    merged["action"] = max(actions, key=severity)
    if len(blocks) > 1:
        merged["reason"] = (
            f"{len(blocks)} conflicting verdicts in the model output — "
            "merged to the most cautious; review this ticket by hand"
        )
    return merged


@dataclass(frozen=True)
class DraftExtraction:
    text: str | None
    ambiguous: bool
    marker_count: int
    overflow: bool = False
    malformed: bool = False


def _extract_draft_details(
    output: str,
    customer_text: str | None = None,
    token: str | None = None,
) -> DraftExtraction:
    """Extract complete exact-token draft blocks and report auth anomalies."""

    del customer_text
    if not token:
        return DraftExtraction(None, False, 0)
    text = str(output or "")
    matches = list(_draft_tag_re(token).finditer(text))
    token_pattern = re.escape(str(token))
    open_count = len(list(re.finditer(r"<DRAFT:" + token_pattern + r">", text)))
    close_count = len(list(re.finditer(r"</DRAFT:" + token_pattern + r">", text)))
    marker_count = len(matches)
    overflow = max(open_count, close_count, marker_count) > _MAX_VERDICT_CANDIDATES
    malformed = open_count != marker_count or close_count != marker_count
    if overflow:
        log_event(
            logger,
            "WARNING",
            "Absurd number of tokenized DRAFT markers - failing closed",
            markers=max(open_count, close_count, marker_count),
            limit=_MAX_VERDICT_CANDIDATES,
        )
        return DraftExtraction(None, True, marker_count, True, malformed)

    survivors = [
        match.group(1).strip()
        for match in matches
        if match.group(1).strip()
    ]
    if not survivors:
        return DraftExtraction(None, False, marker_count, False, malformed)
    return DraftExtraction(
        survivors[0],
        len(survivors) > 1,
        marker_count,
        False,
        malformed,
    )


def _extract_draft(
    output: str,
    customer_text: str | None = None,
    token: str | None = None,
) -> tuple[str | None, bool]:
    """Compatibility wrapper around strict tokenized draft extraction."""

    details = _extract_draft_details(output, customer_text, token)
    return details.text, details.ambiguous


def _parse_json_result(
    output: str,
    customer_text: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Parse and normalize a verdict only when its exact token authenticates it."""

    blocks, marker_count, _echoes = _valid_verdicts(output, customer_text, token)
    if not token or not marker_count or not blocks or len(blocks) != marker_count:
        log_event(
            logger,
            "WARNING",
            "No fully valid tokenized JSON_RESULT in Hermes output",
            candidates=marker_count,
        )
        return _token_failure_result()
    if len(blocks) > 1:
        log_event(
            logger,
            "WARNING",
            "Multiple tokenized verdicts in Hermes output - merging conservatively",
            count=len(blocks),
        )

    result = _merge_verdicts(blocks)
    try:
        result["priority"] = str(result["priority"]).lower().strip()
        result["reason"] = " ".join(str(result.get("reason", "")).split())[:_MAX_REASON]
        raw_action = result.get("action")
        action = raw_action.lower().strip() if isinstance(raw_action, str) else ""
        if action not in _ALLOWED_ACTIONS:
            log_event(
                logger,
                "WARNING",
                "Invalid action in tokenized JSON_RESULT - treating as sensitive",
                action=str(raw_action)[:40],
            )
            action = "sensitive_draft"
        result["action"] = action
        result["notify_owner"] = _as_bool(result.get("notify_owner"))
        # Hermes is strictly read-only; model claims cannot turn these into true.
        result["gorgias_priority_set"] = False
        result["note_posted"] = False
        result.pop("no_draft", None)
        return result
    except Exception as exc:  # malformed trusted payload still fails closed
        log_event(logger, "ERROR", f"Failed to normalize tokenized verdict: {exc}")
        return _token_failure_result()
