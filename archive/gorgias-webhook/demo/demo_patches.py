#!/usr/bin/env python3
"""
demo_patches.py — Apply/restore runtime patches for the demo environment.

Patches gorgias_api.request and Telegram send/getUpdates to route through
the in-memory demo store. Sets demo-specific environment variables and
patches Shopify lookups via qa_v3.fixtures_v3.patch_shopify().
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Dict, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

# Ensure imports resolve
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, "/root")

_patches_applied = False
_originals: Dict[str, Any] = {}


def _demo_env() -> None:
    """Set environment variables for isolated demo operation."""
    os.environ["GORGIAS_BASE_URL"] = "http://127.0.0.1:8081"
    os.environ.setdefault("GORGIAS_USERNAME", "demo@gorgias.local")
    os.environ.setdefault("GORGIAS_API_KEY", "demo-api-key")
    os.environ["FEEDBACK_DB_PATH"] = os.path.join(SCRIPT_DIR, "feedback.db")
    os.environ["HERMES_ALLOW_WRITE"] = "1"
    os.environ["WORKFLOW_A_CONFIRM"] = "1"
    os.environ.setdefault("WORKFLOW_A_TELEGRAM_ALERTS", "1")


def _patched_gorgias_request(method, url, username, api_key, body=None, max_retries=3):
    from demo_gorgias_handler import handle_url

    status, data = handle_url(method, url, body)
    if status >= 400:
        # Match gorgias_api.die() behavior for compatibility
        import gorgias_api
        gorgias_api.die(f"HTTP {status} on {method} {url}\n{json.dumps(data)}")
    return status, data


def _make_telegram_send_patch(bot: str):
    from demo_store import get_store

    def _patched_send(token, chat_id, text, parse_mode=None, *args, **kwargs):
        store = get_store()
        metadata = {"chat_id": chat_id, "parse_mode": parse_mode}
        if kwargs.get("reply_markup"):
            metadata["reply_markup"] = kwargs["reply_markup"]
        if args:
            metadata["extra_args"] = args
        store.append_telegram(bot, text, metadata=metadata)
        msg_id = len(store.get_telegram(bot))
        return {"ok": True, "chat_id": chat_id, "message_id": msg_id}

    return _patched_send


def _patched_priority_get_updates(token, offset=None, timeout=30):
    from demo_store import get_store

    return get_store().poll_replies(offset)


def apply() -> None:
    """Apply all demo patches (idempotent)."""
    global _patches_applied
    if _patches_applied:
        return

    _demo_env()

    # Shopify mock
    try:
        from qa_v3.fixtures_v3 import patch_shopify
        patch_shopify()
    except ImportError:
        pass

    import gorgias_api
    import telegram_notify
    import telegram_priority

    _originals["gorgias_api.request"] = gorgias_api.request
    _originals["telegram_notify._send_to_chat"] = telegram_notify._send_to_chat
    _originals["telegram_priority._send_to_chat"] = telegram_priority._send_to_chat
    _originals["telegram_priority._get_updates"] = telegram_priority._get_updates

    gorgias_api.request = _patched_gorgias_request
    telegram_notify._send_to_chat = _make_telegram_send_patch("notify")
    telegram_priority._send_to_chat = _make_telegram_send_patch("priority")
    telegram_priority._get_updates = _patched_priority_get_updates

    # Initialize demo feedback db
    import feedback_db
    feedback_db.init_db(os.environ["FEEDBACK_DB_PATH"])

    _patches_applied = True


def restore() -> None:
    """Restore patched functions."""
    global _patches_applied
    if not _patches_applied:
        return

    import gorgias_api
    import telegram_notify
    import telegram_priority

    if "gorgias_api.request" in _originals:
        gorgias_api.request = _originals["gorgias_api.request"]
    if "telegram_notify._send_to_chat" in _originals:
        telegram_notify._send_to_chat = _originals["telegram_notify._send_to_chat"]
    if "telegram_priority._send_to_chat" in _originals:
        telegram_priority._send_to_chat = _originals["telegram_priority._send_to_chat"]
    if "telegram_priority._get_updates" in _originals:
        telegram_priority._get_updates = _originals["telegram_priority._get_updates"]

    _originals.clear()
    _patches_applied = False


def is_applied() -> bool:
    return _patches_applied
