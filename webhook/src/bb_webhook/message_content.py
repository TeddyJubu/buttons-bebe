"""Read retained Gorgias content without following archived body URLs."""

from __future__ import annotations

import sys
from pathlib import Path


def _load_intake():
    try:
        from intake.message_content import CLEANUP_VERSION, CONTRACT_KEYS, normalize_message
        return normalize_message, CLEANUP_VERSION, CONTRACT_KEYS
    except ImportError:
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from intake.message_content import CLEANUP_VERSION, CONTRACT_KEYS, normalize_message
        return normalize_message, CLEANUP_VERSION, CONTRACT_KEYS


normalize_message, CLEANUP_VERSION, CONTRACT_KEYS = _load_intake()


def message_text(message):
    """Current customer text for AI intake. Full history stays on normalize_message."""
    if not isinstance(message, dict):
        return ""
    return normalize_message(message)["current_text"]
