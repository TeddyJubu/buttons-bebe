"""Processor logging — reuses the webhook's structured logging with PII redaction."""

from __future__ import annotations

import logging
import sys

from pathlib import Path

# Import the webhook's logging utilities
_webhook_src = Path(__file__).resolve().parent.parent / "webhook" / "src"
import sys as _sys
if str(_webhook_src) not in _sys.path:
    _sys.path.insert(0, str(_webhook_src))

from bb_webhook.logging_utils import (  # noqa: E402
    JsonFormatter,
    TextFormatter,
    get_logger as _get_logger,
    log_event as _log_event,
)


def setup_logging(log_format: str = "json", log_level: str = "INFO") -> None:
    """Configure root logger."""
    handler = logging.StreamHandler(sys.stderr)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return _get_logger(name)


def log_event(logger: logging.Logger, level: str, message: str, **extra) -> None:
    _log_event(logger, level, message, **extra)