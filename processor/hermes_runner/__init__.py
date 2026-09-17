"""Compatibility facade for the split, token-authenticated Hermes runner."""

from .constants import _FALLBACK_RESULT
from .runner import build_hermes_command, draft_for_console, process_ticket_with_hermes


__all__ = [
    "build_hermes_command",
    "draft_for_console",
    "process_ticket_with_hermes",
]
