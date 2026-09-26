"""Deterministic classifier engine; tables and views live beside it."""

from __future__ import annotations

import re
from typing import Any

from config import get_settings
from logging_setup import get_logger, log_event
from shared.priority import Priority

from . import data as _data
from . import matching as _matching
from . import patterns as _patterns
from . import views as _views
from .request_context import service_issue_view


logger = get_logger("classifier")
IMMEDIATE = "immediate"
HIGH = Priority.HIGH.value
NORMAL = Priority.NORMAL.value


def classify(
    payload: dict[str, Any],
    kb_results: list[dict] | None = None,
    order_data: dict | None = None,
) -> dict[str, Any]:
    # ADR-014 §3 — this screen is escalate-only; sensitive always alerts.
    """Classify a ticket while preserving the legacy three-view contract."""
    raw_subject_text = str(payload.get("ticket_subject") or "")
    raw_message_text = str(payload.get("message_text") or "")
    from draft_cleaner import should_draft
    if not should_draft(raw_message_text, raw_subject_text).ok:
        return {'priority': NORMAL, 'sensitive': False, 'should_notify_owner': False,
                'reason': 'No new request in the latest acknowledgment', 'matched': [],
                'should_draft': False, 'source': 'deterministic'}
    classification_subject = service_issue_view(raw_subject_text)
    classification_message = service_issue_view(raw_message_text)
    main_views = [f"{classification_subject} {classification_message}".lower()]
    folded = _views._fold_smart_quotes(main_views[0])
    if folded != main_views[0]:
        main_views.append(folded)

    raw_message = _views._normalise_text(classification_message)
    raw_subject = _views._normalise_text(classification_subject)
    message_text = raw_message.lower()
    ticket_subject = raw_subject.lower()
    combined_text = f"{ticket_subject} {_views._drop_store_boilerplate(message_text)}"

    raw_intents = payload.get("intents", [])
    intent_names: set[str] = set()
    if isinstance(raw_intents, list):
        for intent in raw_intents:
            if isinstance(intent, dict) and intent.get("name"):
                intent_names.add(intent["name"].lower())
            elif isinstance(intent, str):
                intent_names.add(intent.lower())

    kb_sensitive = any(
        isinstance(result, dict) and result.get("sensitive")
        for result in (kb_results or [])
    )
    exclaiming = _views._is_exclaiming(raw_message)
    shouting = _views._is_shouting(raw_message)

    immediate_matches = _matching._find_matches_any(
        main_views, _data._MAIN_IMMEDIATE_KEYWORDS
    )
    immediate_matches.extend(
        match for match in _matching._find_matches(
            combined_text, _data._PORT_IMMEDIATE_KEYWORDS
        )
        if match not in immediate_matches
    )
    weak_matches = _views._weak_matches(combined_text)
    immediate_matches.extend(
        match for match in weak_matches if match not in immediate_matches
    )
    manager_matches = (
        [] if _patterns._TRADE_ENQUIRY_RE.search(combined_text)
        else _matching._find_matches(combined_text, _data._MANAGER_DEMAND_KEYWORDS)
    )
    if (_patterns._ESCALATE_RE.search(combined_text)
            and not _patterns._ESCALATE_NEGATED_RE.search(combined_text)):
        manager_matches.append("escalate this")

    angry_matches = _matching._find_matches_any(main_views, _data._ANGRY_KEYWORDS)
    immediate_hits = len(immediate_matches)
    angry_hits = (len(angry_matches) + bool(exclaiming)
                  + bool(shouting) + bool(manager_matches))
    sensitive_intent_hit = bool(intent_names & _data._SENSITIVE_INTENTS)
    if immediate_hits > 0 or manager_matches or sensitive_intent_hit or kb_sensitive:
        reason_parts: list[str] = []
        matched = list(immediate_matches)
        if immediate_hits > 0:
            reason_parts.append(f"keyword match ({immediate_hits} sensitive keywords)")
        if weak_matches:
            reason_parts.append(
                f"contextual match ({', '.join(weak_matches)} + order/delivery context)"
            )
        if manager_matches:
            reason_parts.append(
                f"manager/escalation demand ({', '.join(manager_matches)})"
            )
            matched.extend(match for match in manager_matches if match not in matched)
        if sensitive_intent_hit:
            reason_parts.append(f"sensitive intent ({intent_names & _data._SENSITIVE_INTENTS})")
        if kb_sensitive:
            reason_parts.append("KB sensitive flag")
        if exclaiming:
            reason_parts.append("excessive exclamation (!!!)")
            matched.append("!!!")
        if shouting:
            reason_parts.append("shouting (all-caps message)")
            matched.append("ALL CAPS")
        if angry_hits >= 2:
            reason_parts.append(f"angry customer ({angry_hits} angry signals)")
            matched.extend(match for match in angry_matches if match not in matched)
        if order_data:
            try:
                total = float(order_data.get("total_price", 0))
                if total > 200 and immediate_hits > 0:
                    reason_parts.append(f"high order value (${total:.2f})")
            except (ValueError, TypeError):
                pass
        reason = "; ".join(reason_parts)
        log_event(
            logger, "INFO", "Classifier: IMMEDIATE", ticket_id=payload.get("ticket_id"),
            reason=reason, matched=matched,
                  context=[_matching._match_context(raw_message_text, match)
                     for match in matched[:5] if match not in ("!!!", "ALL CAPS")],
        )
        return {
            "priority": IMMEDIATE, "reason": reason, "sensitive": True,
            "should_draft": True, "should_notify_owner": True,
            "source": "deterministic", "matched": matched,
        }

    high_matches = _matching._find_matches_any(
        main_views, _data._MAIN_HIGH_KEYWORDS
    )
    high_matches.extend(
        match for match in _matching._find_matches(
            combined_text, _data._PORT_HIGH_KEYWORDS
        )
        if match not in high_matches
    )
    high_hits = len(high_matches)
    high_intent_hit = bool(intent_names & _data._HIGH_INTENTS)
    followup_match = _matching._search_any(main_views, _patterns._FOLLOWUP_PATTERN)
    if high_hits > 0 or high_intent_hit or followup_match or exclaiming or shouting:
        high_sensitive = bool(intent_names & _data._HIGH_SENSITIVE_INTENTS) or bool(
            _matching._search_any(main_views, _patterns._MAIN_HIGH_SENSITIVE_PATTERN)
        ) or bool(_patterns._HIGH_SENSITIVE_PATTERN.search(combined_text))
        reason_parts = []
        matched = list(high_matches)
        if high_hits > 0:
            reason_parts.append(f"keyword match ({high_hits} urgent keywords)")
        if high_intent_hit:
            reason_parts.append(f"urgent intent ({intent_names & _data._HIGH_INTENTS})")
        if followup_match:
            reason_parts.append("follow-up pattern detected")
            matched.append(" ".join(followup_match.group(0).split()))
        if exclaiming:
            reason_parts.append("excessive exclamation (!!!)")
            matched.append("!!!")
        if shouting:
            reason_parts.append("shouting (all-caps message)")
            matched.append("ALL CAPS")
        reason = "; ".join(reason_parts)
        log_event(
            logger, "INFO", "Classifier: HIGH", ticket_id=payload.get("ticket_id"),
            reason=reason, matched=matched,
        )
        return {
            "priority": HIGH, "reason": reason, "sensitive": high_sensitive,
            "should_draft": True, "should_notify_owner": True,
            "source": "deterministic", "matched": matched,
        }

    log_event(logger, "DEBUG", "Classifier: NORMAL",
              ticket_id=payload.get("ticket_id"))
    return {
        "priority": NORMAL,
        "reason": "no sensitive/urgent keywords or intents detected",
        "sensitive": False,
        "should_draft": True,
        "should_notify_owner": False,
        "source": "deterministic",
        "matched": [],
    }


__all__ = [
    "classify", "IMMEDIATE", "HIGH", "NORMAL", "get_settings", "get_logger",
    "logger", "log_event", "Priority", "Any", "re",
]
