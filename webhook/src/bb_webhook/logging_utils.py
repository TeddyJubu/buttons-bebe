"""Structured logging with PII redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

from .config import get_settings

# ── PII redaction patterns ────────────────────────────────
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
# Mask everything except domain for URLs with tokens
_TOKEN_RE = re.compile(r"(shpat_|sk_)[a-zA-Z0-9]{6,}")
# Redact webhook shared secret from logs
_SECRET_RE = re.compile(r"secret=[a-zA-Z0-9\-_]+")

_REDACT_PLACEHOLDER = "[REDACTED]"


def _redact(text: str) -> str:
    """Redact emails, phone numbers, API tokens, and webhook secrets from log text."""
    text = _SECRET_RE.sub("secret=[REDACTED]", text)
    text = _EMAIL_RE.sub(_REDACT_PLACEHOLDER, text)
    text = _PHONE_RE.sub(_REDACT_PLACEHOLDER, text)
    text = _TOKEN_RE.sub(_REDACT_PLACEHOLDER, text)
    return text


class JsonFormatter(logging.Formatter):
    """Single-line JSON log records with PII redaction."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": _redact(str(record.getMessage())),
        }
        # Add structured extra fields if caller passed them
        for key, val in getattr(record, "_extra", {}).items():
            payload[key] = _redact(str(val))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable text format with PII redaction."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        msg = _redact(str(record.getMessage()))
        extras = getattr(record, "_extra", {})
        extra_str = " ".join(f"{k}={_redact(str(v))}" for k, v in extras.items())
        line = f"{ts} {record.levelname:<7} [{record.name}] {msg}"
        if extra_str:
            line += f" | {extra_str}"
        return line


def setup_logging() -> None:
    """Configure root logger from settings."""
    settings = get_settings()
    handler = logging.StreamHandler(sys.stderr)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger that supports structured extra fields."""
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: str,
    message: str,
    **extra,
) -> None:
    """Log an event with structured extra fields."""
    record = logging.LogRecord(
        name=logger.name,
        level=getattr(logging, level.upper()),
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )
    record._extra = extra  # type: ignore[attr-defined]
    logger.handle(record)