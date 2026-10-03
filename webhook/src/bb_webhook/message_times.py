"""Aware UTC message chronology, including historical provider offsets."""
from datetime import datetime, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SOURCE_TIME_SQL = "utc_microseconds(COALESCE(NULLIF(created_at,''),received_at))"
LATEST_CUSTOMER_SQL = f"""SELECT message_id,
    ({SOURCE_TIME_SQL} IS NULL OR utc_microseconds(received_at) IS NULL) AS chronology_invalid
    FROM parsed_messages WHERE ticket_id=? AND is_customer_message=1
    ORDER BY chronology_invalid DESC, {SOURCE_TIME_SQL} DESC,
    utc_microseconds(received_at) DESC, message_id DESC LIMIT 1"""


def parse_utc(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def normalize_timestamp(value):
    parsed = parse_utc(value)
    return parsed.isoformat() if parsed is not None else None


def utc_microseconds(value):
    parsed = parse_utc(value)
    if parsed is None:
        return None
    elapsed = parsed - _EPOCH
    return (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds


def freshness_error(latest, source_message_id):
    if latest and latest['chronology_invalid']:
        return 'message_chronology_unavailable'
    if not latest or latest['message_id'] != source_message_id:
        return 'new_customer_message_refresh_ticket'
    return None
