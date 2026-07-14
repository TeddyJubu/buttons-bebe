"""
fake_gorgias.py — in-memory mock of the Gorgias API.

Records every message that Teddy would post to a real Gorgias account.
Use get_inbox() after running queries to inspect what would have been sent.

Replaces skills/post_reply.py in the test runner — same return shape so
the rest of the pipeline sees no difference.
"""

import time

_inbox: list = []  # all messages received


def post_message(ticket_id: str, body: str, mode: str = 'internal_note') -> dict:
    """
    Simulate posting a reply to a Gorgias ticket.

    mode: 'internal_note' | 'public_reply'
    """
    record = {
        'ticket_id': str(ticket_id),
        'mode':      mode,
        'body':      body,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    _inbox.append(record)
    return {'posted': True, 'fake': True, 'id': f'fake-msg-{len(_inbox)}'}


def get_inbox() -> list:
    """Return all messages received so far (in arrival order)."""
    return list(_inbox)


def clear():
    """Reset inbox between test runs."""
    _inbox.clear()


def print_inbox(title: str = 'Fake Gorgias inbox'):
    sep = '═' * 60
    print(f"\n{sep}")
    print(f"  {title}  ({len(_inbox)} message{'s' if len(_inbox) != 1 else ''})")
    print(sep)
    if not _inbox:
        print("  (empty)")
        return
    for msg in _inbox:
        mode_label = '📤 PUBLIC REPLY' if msg['mode'] == 'public_reply' else '📋 INTERNAL NOTE'
        print(f"\n  Ticket #{msg['ticket_id']}  [{mode_label}]")
        print(f"  {'-' * 56}")
        for line in msg['body'].split('\n'):
            print(f"  {line}")
