"""Canonical message cleanup shared by webhook, Gorgias reads, and projection."""

from intake.message_content import CLEANUP_VERSION, CONTRACT_KEYS, normalize_message

__all__ = ["CLEANUP_VERSION", "CONTRACT_KEYS", "normalize_message"]
