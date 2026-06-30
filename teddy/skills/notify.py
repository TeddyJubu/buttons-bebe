"""
notify.py — sends Telegram notifications to the shop owner.

Priority-aware: IMMEDIATE gets an urgent alert, HIGH gets a review flag,
LOW gets a quiet confirmation. A failed Telegram never blocks the main flow.
"""

import logging
import os

import requests

log = logging.getLogger('teddy.notify')

_LEVEL_ICON = {
    'IMMEDIATE': '🚨',
    'HIGH':      '⚠️',
    'LOW':       '📝',
}

_LEVEL_LABEL = {
    'IMMEDIATE': '*ACT NOW*',
    'HIGH':      '*Review needed*',
    'LOW':       '*Draft posted*',
}


def notify(
    ticket_id: str,
    intent: str,
    kb_confidence: str,
    priority_level: str,
    priority_reason: str,
    draft_preview: str,
    posted: bool,
) -> dict:
    """
    notify(...) -> {"sent": bool}

    IMMEDIATE — 🚨 ACT NOW — no draft included (human must act, not read a draft)
    HIGH      — ⚠️ Review needed — draft preview included
    LOW       — 📝 Draft ready / ✅ Auto-sent — brief preview
    """
    token   = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

    if not token or not chat_id:
        log.debug("Telegram not configured, skipping")
        return {'sent': False}

    icon  = _LEVEL_ICON.get(priority_level, '📝')
    label = _LEVEL_LABEL.get(priority_level, 'Draft ready')

    if priority_level == 'IMMEDIATE':
        # No draft shown — owner must open ticket and act
        text = (
            f"{icon} {label} — Ticket #{ticket_id}\n"
            f"_{priority_reason}_\n\n"
            f"Open Gorgias and handle this now — time-sensitive."
        )

    elif priority_level == 'HIGH':
        preview = (draft_preview[:180] + '…') if len(draft_preview) > 180 else draft_preview
        preview_block = f"\n\n_{preview}_" if preview else ''
        text = (
            f"{icon} {label} — Ticket #{ticket_id}\n"
            f"_{priority_reason}_"
            f"{preview_block}"
        )

    else:  # LOW — Phase 1: draft posted as internal note for human review
        preview = (draft_preview[:120] + '…') if len(draft_preview) > 120 else draft_preview
        preview_block = f"\n\n_{preview}_" if preview else ''
        text = (
            f"{icon} {label} — Ticket #{ticket_id} ({intent})"
            f"{preview_block}"
        )

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                'chat_id':    chat_id,
                'text':       text,
                'parse_mode': 'Markdown',
            },
            timeout=8,
        )
        if resp.ok:
            return {'sent': True}
        log.warning("Telegram error %s: %s", resp.status_code, resp.text[:100])
        return {'sent': False}

    except Exception as e:
        log.warning("Telegram failed (non-fatal): %s", e)
        return {'sent': False}
