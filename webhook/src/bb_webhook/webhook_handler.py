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

from .config import get_settings
from .logging_utils import get_logger, log_event

logger = get_logger(__name__)

# Max age for a webhook event to prevent replay attacks (seconds)
MAX_EVENT_AGE = 600  # 10 minutes


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
        return val.strip().lower() in ("true", "1", "yes")
    return None


def _coerce_int(val: Any) -> int | None:
    """Coerce a string/int to int, or None if not possible."""
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    if isinstance(val, str) and val.strip().isdigit():
        return int(val.strip())
    return None


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
    """Return an ISO timestamp string, accepting Gorgias field names."""
    if isinstance(val, str) and val.strip():
        # Replace trailing 'Z' for fromisoformat compatibility
        ts = val.strip()
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return ts
    return None


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

    ticket = payload.get("ticket") or payload.get("data", {}).get("ticket", {})
    message = payload.get("message") or payload.get("data", {}).get("message", {})

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
        created_at = datetime.now(timezone.utc).isoformat()

    # ── Message text ───────────────────────────────────────
    message_text = None
    if message:
        message_text = (
            message.get("body_text")
            or message.get("stripped_text")
            or message.get("text")
            or ""
        )

    ticket_subject = ticket.get("subject") if ticket else None

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