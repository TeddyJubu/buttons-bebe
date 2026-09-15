"""Webhook signature validation and event normalization.

Handles Gorgias HTTP-integration payloads where all template values
are rendered as strings (e.g. ``"False"`` instead of ``False``,
``'{"email": ...}'`` instead of a dict).  The parser coerces these
back to native Python types before returning the normalized event.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from .message_content import message_text as retained_message_text
from .config import get_settings
from .logging_utils import get_logger, log_event

logger = get_logger(__name__)

# Max age for a webhook event to prevent replay attacks (seconds)
MAX_EVENT_AGE = 600  # 10 minutes
MAX_FUTURE_SKEW = 300  # tolerate modest clock drift, not future-dated replays
MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807


# ── Helpers ────────────────────────────────────────────────

def _coerce_bool(val: Any) -> bool | None:
    """Coerce a Gorgias template value to a real bool.

    Gorgias renders ``{{message.from_agent}}`` as the *string*
    ``"True"`` or ``"False"``.  ``bool("False")`` is ``True`` in
    Python (non-empty string), so we must parse it explicitly.
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        normalized = val.strip().lower()
        if normalized in ("true", "1", "yes"):
            return True
        if normalized in ("false", "0", "no"):
            return False
    return None


def _coerce_int(val: Any) -> int | None:
    """Coerce a string/int to int, or None if not possible."""
    result: int | None = None
    if isinstance(val, int) and not isinstance(val, bool):
        result = val
    elif isinstance(val, str) and val.strip().isdigit():
        result = int(val.strip())
    if result is None or result <= 0 or result > MAX_SQLITE_INTEGER:
        return None
    return result


def _maybe_json_parse(val: Any) -> Any:
    """If *val* is a JSON-encoded string, parse it; otherwise return as-is.

    Gorgias ``| tojson`` filter renders objects/lists as JSON strings.
    e.g. ``"{{message.sender | tojson}}"`` yields a string like
    ``'{"email": "x@y.com", ...}'``.
    """
    if not isinstance(val, str):
        return val
    stripped = val.strip()
    if not stripped:
        return val
    # Quick check: JSON objects/arrays start with { or [
    if stripped[0] in "[{":
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return val
    return val


def _extract_email(val: Any) -> str | None:
    """Extract an email address from a dict, JSON string, or plain string."""
    if val is None:
        return None
    parsed = _maybe_json_parse(val)
    if isinstance(parsed, dict):
        return parsed.get("email")
    if isinstance(parsed, str):
        # Could be a plain email address or still a JSON string
        if "@" in parsed and "{" not in parsed:
            return parsed.strip()
    return None


def _normalize_timestamp(val: Any) -> str | None:
    """Return a validated, timezone-aware ISO timestamp string."""
    if not isinstance(val, str) or not val.strip():
        return None
    ts = val.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def _normalize_ticket_status(val: Any) -> str | None:
    """Keep a short observed Gorgias ticket status, or None.

    Unknown shapes fail closed to None so the projection keeps 'unknown'
    instead of storing an unbounded or misleading value.
    """
    if not isinstance(val, str):
        return None
    status = val.strip().lower()
    if not status or len(status) > 30:
        return None
    if not all(ch.isalnum() or ch in (" ", "_", "-") for ch in status):
        return None
    return status


def _normalize_ticket_assignee(val: Any) -> str | None:
    """Keep a short observed Gorgias assignee identity, or None.

    The template renders assignee with |tojson, so this may arrive as a dict
    (prefer email, then name), a plain string, or nothing when unassigned.
    Unknown shapes fail closed to None (unassigned), never a guess.
    """
    raw: Any = val
    if isinstance(val, str):
        raw = _maybe_json_parse(val)
    if isinstance(raw, dict):
        for key in ("email", "name"):
            candidate = raw.get(key)
            if isinstance(candidate, str) and candidate.strip():
                raw = candidate
                break
        else:
            return None
    if not isinstance(raw, str):
        return None
    assignee = raw.strip()
    if not assignee or len(assignee) > 120:
        return None
    if not all(ch.isalnum() or ch in (" ", ".", "_", "-", "+", "@") for ch in assignee):
        return None
    return assignee


def _normalize_ticket_tags(val: Any) -> list[str]:
    """Keep bounded observed Gorgias ticket tags, or an empty list.

    The template renders tags with |tojson, so this may arrive as a JSON
    string, a plain list of names, or a list of tag objects with a "name".
    Unknown shapes fail closed to no tags; no tag is ever inferred from
    message text or subject.
    """
    raw: Any = val
    if isinstance(val, str):
        raw = _maybe_json_parse(val)
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            item = item.get("name")
        if not isinstance(item, str):
            continue
        tag = item.strip()
        if not tag or len(tag) > 40:
            continue
        if not all(ch.isalnum() or ch in (" ", "_", "-") for ch in tag):
            continue
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
        if len(tags) >= 12:
            break
    return tags


def _normalize_ticket_priority(val: Any) -> str | None:
    """Keep a short observed Gorgias ticket priority, or None.

    This is Gorgias's own ticket priority, stored separately from the AI
    draft priority in ticket_results.priority. Malformed values fail
    closed to None rather than being guessed or defaulted.
    """
    if not isinstance(val, str):
        return None
    priority = val.strip().lower()
    if not priority or len(priority) > 20:
        return None
    if not all(ch.isalnum() or ch in ("_", "-") for ch in priority):
        return None
    return priority


def _normalize_ticket_spam(val: Any) -> int:
    """Keep an observed Gorgias spam flag as 1/0; malformed fails closed to 0.

    The ticket itself is always kept; only the badge is withheld.
    """
    return 1 if _coerce_bool(val) is True else 0


def _normalize_ticket_trashed(val: Any) -> int:
    """Keep an observed Gorgias trashed flag as 1/0; malformed fails closed to 0.

    A valid trashed_datetime timestamp means trashed. A plain boolean
    trashed alias is accepted for template variants. The ticket itself
    is always kept; only the badge is withheld.
    """
    if isinstance(val, str) and val.strip():
        return 1 if _normalize_timestamp(val) is not None else 0
    return 1 if _coerce_bool(val) is True else 0


def _normalize_ticket_snoozed(val: Any) -> int:
    """Keep an observed Gorgias snoozed flag as 1/0; malformed fails closed to 0.

    A valid snooze_datetime timestamp means a snooze is scheduled. A plain
    boolean snoozed alias is accepted for template variants. A snoozed status
    already badges via the status path; this covers a scheduled snooze while
    the status still reads open. The ticket itself is always kept.
    """
    if isinstance(val, str) and val.strip():
        return 1 if _normalize_timestamp(val) is not None else 0
    return 1 if _coerce_bool(val) is True else 0


# ── Signature verification ─────────────────────────────────

def verify_signature(
    raw_body: bytes,
    signature_header: str | None,
    query_secret: str | None = None,
) -> bool:
    """Validate the authenticity of a Gorgias webhook request.

    Two authentication methods are supported:

    1. HMAC-SHA256 signature (X-Gorgias-Signature header)
       Used by test_webhook.py and any caller that computes HMAC.
       The signature is HMAC-SHA256(raw_body, shared_secret).

    2. Shared secret via query string (?secret=...)
       Used by the Gorgias HTTP Integration, which does not support
       HMAC signing.  The shared secret is appended to the webhook URL
       as a query parameter and compared in constant time.

    If a signature header is present, method 1 is used.
    Otherwise, the query-string secret is checked (method 2).
    """
    settings = get_settings()
    secret = settings.webhook_secret

    if not secret:
        log_event(logger, "ERROR", "WEBHOOK_SECRET not configured — rejecting all webhooks")
        return False

    # ── Method 1: HMAC-SHA256 signature header ─────────────
    if signature_header:
        expected = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature_header):
            log_event(logger, "WARNING", "Invalid webhook signature — rejecting")
            return False
        return True

    # ── Method 2: shared secret via query string ───────────
    if query_secret:
        if hmac.compare_digest(query_secret, secret):
            return True
        log_event(logger, "WARNING", "Invalid webhook query secret — rejecting")
        return False

    log_event(logger, "WARNING", "No signature or query secret provided — rejecting")
    return False


# ── Event parser ───────────────────────────────────────────

def parse_event(raw_body: bytes) -> dict[str, Any] | None:
    """Parse and normalize the Gorgias webhook payload.

    Gorgias HTTP integrations render template values as strings.  This
    parser coerces them back to native Python types.  See the module
    docstring for details.

    Returns a normalized event dict::

        {
            tenant_id: str,            # Gorgias subdomain
            ticket_id: int,
            message_id: int | None,
            event_type: str,           # ticket-message-created | ticket-created | …
            author_type: str,          # customer | agent | system
            author_email: str | None,
            channel: str | None,
            created_at: str,            # ISO 8601 timestamp from the event
            message_text: str | None,
            ticket_subject: str | None,
            ticket_status: str | None,
            ticket_assignee: str | None,
            ticket_tags: list[str],
            ticket_priority: str | None,
            ticket_spam: int,            # 1/0 observed Gorgias spam flag
            ticket_trashed: int,         # 1/0 observed Gorgias trashed flag
            ticket_snoozed: int,         # 1/0 observed Gorgias snooze flag
            customer_email: str | None,
            intents: list[dict],       # parsed Gorgias intent objects
            is_customer_message: bool, # True only for inbound customer messages
            raw: dict,                 # full original payload
        }

    Returns ``None`` if the payload is malformed.
    """
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log_event(logger, "ERROR", "Failed to parse webhook body as JSON")
        return None

    if not isinstance(payload, dict):
        log_event(logger, "WARNING", "Webhook payload must be a JSON object")
        return None

    data = payload.get("data", {})
    if data is None:
        data = {}
    if not isinstance(data, dict):
        log_event(logger, "WARNING", "Webhook data field must be an object")
        return None

    ticket = payload.get("ticket", data.get("ticket", {}))
    message = payload.get("message", data.get("message", {}))
    if ticket is None:
        ticket = {}
    if message is None:
        message = {}
    if not isinstance(ticket, dict) or not isinstance(message, dict):
        log_event(logger, "WARNING", "Webhook ticket/message fields must be objects")
        return None

    if not ticket and not message:
        log_event(logger, "WARNING", "Webhook payload missing ticket/message data")
        return None

    # ── IDs (coerce string → int) ──────────────────────────
    ticket_id = _coerce_int(ticket.get("id")) if ticket else None
    message_id = _coerce_int(message.get("id")) if message else None

    # ── Event type (Gorgias uses "trigger", not "event") ──
    event_type = (
        payload.get("trigger")
        or payload.get("event")
        or payload.get("type")
        or "unknown"
    )

    # ── Author type ────────────────────────────────────────
    # Gorgias renders from_agent as string "True"/"False".
    from_agent = _coerce_bool(message.get("from_agent")) if message else None
    if message and "from_agent" in message and from_agent is None:
        log_event(logger, "WARNING", "Webhook from_agent value is invalid")
        return None
    if from_agent is True:
        author_type = "agent"
    elif from_agent is False:
        author_type = "customer"
    else:
        # Fallback: check sent_datetime (agent outbound messages have it)
        sent_dt = message.get("sent_datetime") if message else None
        author_type = "agent" if sent_dt and str(sent_dt).strip() else "customer"

    # If no message, ticket creation is typically customer-initiated
    if not message and ticket:
        author_type = "customer"

    # ── Sender / author email ─────────────────────────────
    # Gorgias renders sender as a JSON string via | tojson.
    author_email = _extract_email(message.get("sender")) if message else None

    # ── Channel ────────────────────────────────────────────
    channel = None
    if message:
        channel = message.get("channel")
    if not channel and ticket:
        channel = ticket.get("channel")

    # ── Timestamp (Gorgias uses "created_datetime") ───────
    created_at = None
    if message:
        created_at = (
            _normalize_timestamp(message.get("created_datetime"))
            or _normalize_timestamp(message.get("created_at"))
            or _normalize_timestamp(message.get("received_at"))
        )
    if not created_at and ticket:
        created_at = _normalize_timestamp(ticket.get("created_datetime")) \
            or _normalize_timestamp(ticket.get("created_at"))
    if not created_at:
        log_event(logger, "WARNING", "Webhook timestamp is missing or invalid")
        return None

    # ── Message text ───────────────────────────────────────
    message_text = None
    if message:
        message_text = retained_message_text(message)

    ticket_subject = ticket.get("subject") if ticket else None

    # Observed Gorgias ticket status (open/closed/snoozed…). Optional: the
    # template only started sending it recently, so older rows have none.
    ticket_status = _normalize_ticket_status(ticket.get("status")) if ticket else None
    raw_assignee = (ticket.get("assignee") or ticket.get("assignee_user")) if ticket else None
    ticket_assignee = _normalize_ticket_assignee(raw_assignee)
    ticket_tags = _normalize_ticket_tags(ticket.get("tags")) if ticket else []
    ticket_priority = _normalize_ticket_priority(ticket.get("priority")) if ticket else None
    ticket_spam = _normalize_ticket_spam(ticket.get("spam")) if ticket else 0
    ticket_trashed = _normalize_ticket_trashed(ticket.get("trashed_datetime") or ticket.get("trashed")) if ticket else 0
    ticket_snoozed = _normalize_ticket_snoozed(ticket.get("snooze_datetime") or ticket.get("snoozed")) if ticket else 0

    # ── Customer email ─────────────────────────────────────
    customer_email = None
    if ticket:
        customer = ticket.get("customer", {})
        if isinstance(customer, dict):
            customer_email = customer.get("email")
        elif isinstance(customer, str):
            customer_email = _extract_email(customer)

    # ── Intents (Gorgias sends JSON string via | tojson) ──
    intents: list[dict] = []
    if message:
        raw_intents = _maybe_json_parse(message.get("intents"))
        if isinstance(raw_intents, list):
            intents = [i for i in raw_intents if isinstance(i, dict)]

    # ── is_customer_message flag for downstream filtering ─
    is_customer_message = (
        author_type == "customer"
        and message is not None
        and event_type == "ticket-message-created"
    )

    event = {
        "tenant_id": get_settings().gorgias_subdomain,
        "ticket_id": ticket_id,
        "message_id": message_id,
        "event_type": event_type,
        "author_type": author_type,
        "author_email": author_email,
        "channel": channel,
        "created_at": created_at,
        "message_text": message_text,
        "ticket_subject": ticket_subject,
        "ticket_status": ticket_status,
        "ticket_assignee": ticket_assignee,
        "ticket_tags": ticket_tags,
        "ticket_priority": ticket_priority,
        "ticket_spam": ticket_spam,
        "ticket_trashed": ticket_trashed,
        "ticket_snoozed": ticket_snoozed,
        "customer_email": customer_email,
        "intents": intents,
        "is_customer_message": is_customer_message,
        "raw": payload,
    }

    return event


# ── Replay protection ──────────────────────────────────────

def is_event_too_old(created_at: str | None, max_age: int = MAX_EVENT_AGE) -> bool:
    """Check if the webhook event is older than *max_age* seconds."""
    if not created_at:
        return False  # can't determine age, allow it

    try:
        ts_str = created_at.replace("Z", "+00:00") if isinstance(created_at, str) else created_at
        event_time = datetime.fromisoformat(ts_str)
        now = datetime.now(timezone.utc)
        age = (now - event_time).total_seconds()
        return age > max_age
    except (ValueError, TypeError, AttributeError):
        return False  # can't parse, allow it


def is_event_in_future(
    created_at: str | None,
    max_future_skew: int = MAX_FUTURE_SKEW,
) -> bool:
    """Return whether a validated event timestamp is implausibly future-dated."""
    if not created_at:
        return False
    try:
        event_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return (event_time - datetime.now(timezone.utc)).total_seconds() > max_future_skew
    except (ValueError, TypeError, AttributeError):
        return False
