"""
kb_gap.py — tracks customer questions that had no KB coverage.

When Teddy processes a ticket with confidence=NONE, record_gap() appends a
record to kb/gaps.jsonl. This builds an audit trail of missing knowledge so
the owner can prioritise what to write next.

Review and answer gaps interactively with:
    python3 tools/gap_review.py

Answered gaps are saved to kb/learned/ and marked in gaps.jsonl.
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger('teddy.kb_gap')

_GAP_FILENAME = 'gaps.jsonl'


def record_gap(ticket_id: str, intent: str, message: str, kb_dir: str):
    """Append one KB gap record to kb/gaps.jsonl."""
    gap_file = Path(kb_dir) / _GAP_FILENAME
    record = {
        'ticket_id': ticket_id,
        'intent':    intent,
        'message':   message[:300],
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'answered':  False,
    }
    try:
        with open(gap_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')
        log.info("Ticket #%s: KB gap recorded (intent=%s)", ticket_id, intent)
    except Exception as e:
        log.warning("Could not record KB gap: %s", e)


def pending_gaps(kb_dir: str) -> list:
    """Return all unanswered gap records from kb/gaps.jsonl."""
    gap_file = Path(kb_dir) / _GAP_FILENAME
    if not gap_file.exists():
        return []
    gaps = []
    with open(gap_file, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if not rec.get('answered'):
                    gaps.append(rec)
            except Exception:
                continue
    return gaps
