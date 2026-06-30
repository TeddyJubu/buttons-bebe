"""
fake_telegram.py — in-memory mock of the Telegram Bot API.

Records every notification that Teddy would send to the owner's Telegram chat.
Use get_inbox() after running queries to inspect what the owner would have seen.

Replaces skills/notify.py in the test runner — called with the same keyword
arguments so the pipeline is unaware it's talking to a fake.
"""

import time

_inbox: list = []  # all messages received

# Emoji mappings (mirrors what real notify.py sends)
_PRIORITY_EMOJI = {
    'IMMEDIATE': '🚨',
    'HIGH':      '⚠️',
    'LOW':       '📝',
}
_KB_EMOJI = {
    'HIGH':   '🟢',
    'MEDIUM': '🟡',
    'LOW':    '🟠',
    'NONE':   '🔴',
}


def send_notification(
    ticket_id: str,
    intent: str,
    kb_confidence: str,
    priority_level: str,
    priority_reason: str,
    draft_preview: str = '',
    posted: bool = False,
) -> dict:
    """
    Simulate sending a Telegram notification to the store owner.
    Formats the message the same way a real Telegram bot would.
    """
    prio_icon = _PRIORITY_EMOJI.get(priority_level, '❓')
    kb_icon   = _KB_EMOJI.get(kb_confidence, '❓')
    auto_tag  = ' 📋 DRAFT' if posted else ''

    lines = [
        f"{prio_icon} {priority_level}{auto_tag}",
        f"Ticket #{ticket_id} | {intent}",
        f"KB: {kb_icon} {kb_confidence}",
        f"Reason: {priority_reason}",
    ]
    if draft_preview:
        preview = draft_preview[:200].replace('\n', ' ')
        lines.append(f"Draft: {preview}")

    text = '\n'.join(lines)

    record = {
        'ticket_id':      str(ticket_id),
        'priority_level': priority_level,
        'intent':         intent,
        'kb_confidence':  kb_confidence,
        'posted':         posted,
        'text':           text,
        'timestamp':      time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    _inbox.append(record)
    return {'ok': True, 'fake': True, 'message_id': len(_inbox)}


def get_inbox() -> list:
    """Return all notifications received so far."""
    return list(_inbox)


def clear():
    """Reset inbox between test runs."""
    _inbox.clear()


def print_inbox(title: str = 'Fake Telegram inbox'):
    sep = '═' * 60
    print(f"\n{sep}")
    print(f"  {title}  ({len(_inbox)} notification{'s' if len(_inbox) != 1 else ''})")
    print(sep)
    if not _inbox:
        print("  (empty)")
        return
    for msg in _inbox:
        print(f"\n  [{msg['timestamp']}]")
        for line in msg['text'].split('\n'):
            print(f"  {line}")
