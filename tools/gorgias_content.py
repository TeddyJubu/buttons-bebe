"""Add explicit retained-content hints; never fetch TicketMessage.body_url."""

from __future__ import annotations

import sys
from pathlib import Path


def _load_intake():
    try:
        from intake.message_content import CONTRACT_KEYS, normalize_message
        return CONTRACT_KEYS, normalize_message
    except ImportError:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from intake.message_content import CONTRACT_KEYS, normalize_message
        return CONTRACT_KEYS, normalize_message


CONTRACT_KEYS, normalize_message = _load_intake()
_PREFERRED_FIELDS = ("stripped_text", "stripped_html", "body_text", "body_html")


def _preferred(message):
    return next(
        (
            (key, message[key])
            for key in _PREFERRED_FIELDS
            if isinstance(message.get(key), str) and message[key].strip()
        ),
        None,
    )


def _with_contract(result, message):
    normalized = normalize_message(message)
    for key in CONTRACT_KEYS:
        result[key] = normalized[key]
    return result


def curate_message(message):
    if not isinstance(message, dict):
        return message
    result = dict(message)
    preferred = _preferred(message)
    result["preferred_content"] = preferred[1] if preferred else ""
    result["preferred_content_field"] = preferred[0] if preferred else None
    result["content_unavailable"] = preferred is None
    return _with_contract(result, message)


def curate_messages(response):
    if isinstance(response, list):
        return [curate_message(message) for message in response]
    if isinstance(response, dict) and isinstance(response.get("data"), list):
        return {**response, "data": [curate_message(message) for message in response["data"]]}
    return response


def curate_ticket(response):
    result = curate_summary(response)
    if isinstance(result, dict) and isinstance(result.get("messages"), list):
        result["messages"] = [curate_message(message) for message in result["messages"]]
    return result


def curate_summary(ticket):
    """Clean one list-row excerpt. The row is not a full message."""
    if not isinstance(ticket, dict):
        return ticket
    result = dict(ticket)
    excerpt = ticket.get("excerpt")
    source = {"excerpt": excerpt} if isinstance(excerpt, str) else {}
    if isinstance(ticket.get("body_url"), str):
        source["body_url"] = ticket["body_url"]
    normalized = normalize_message(source)
    for key in CONTRACT_KEYS:
        result[key] = normalized[key]
    if isinstance(excerpt, str) and excerpt.strip():
        result["excerpt"] = normalized["display_text"]
    return result


def curate_summaries(response):
    if isinstance(response, list):
        return [curate_summary(ticket) for ticket in response]
    if isinstance(response, dict) and isinstance(response.get("data"), list):
        return {**response, "data": [curate_summary(ticket) for ticket in response["data"]]}
    return response
