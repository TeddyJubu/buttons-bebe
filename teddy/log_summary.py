"""
log_summary.py — weekly learning report.

Reads log.jsonl and prints a plain-English summary:
  - Priority breakdown (IMMEDIATE / HIGH / LOW)
  - KB confidence distribution
  - Intent frequency
  - Top HIGH escalation reasons
  - Topics with no KB match (→ write more KB articles)
  - Most-used KB files
  - Auto-send stats

Run:
  python3 log_summary.py              # last 7 days
  python3 log_summary.py --days 30    # last 30 days
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

LOG_FILE = Path(__file__).parent / 'log.jsonl'


def load_entries(days: int) -> list:
    if not LOG_FILE.exists():
        return []
    cutoff = time.time() - (days * 86400)
    entries = []
    with open(LOG_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                ts_str = e.get('timestamp', '')
                ts = time.mktime(time.strptime(ts_str, '%Y-%m-%dT%H:%M:%SZ'))
                if ts >= cutoff:
                    entries.append(e)
            except Exception:
                continue
    return entries


def bar(value: float, width: int = 20) -> str:
    filled = round(value / 100 * width)
    return '█' * filled + '░' * (width - filled)


def pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total else "0%"


def summarise(days: int = 7):
    entries = load_entries(days)

    if not entries:
        print(f"No log entries found in the last {days} days.")
        print(f"(Log file: {LOG_FILE})")
        return

    total = len(entries)

    # Priority breakdown
    by_priority = Counter(e.get('priority', 'UNKNOWN') for e in entries)
    n_immediate = by_priority.get('IMMEDIATE', 0)
    n_high      = by_priority.get('HIGH', 0)
    n_low       = by_priority.get('LOW', 0)

    # KB confidence
    by_kb_conf = Counter(e.get('kb_confidence', 'UNKNOWN') for e in entries)

    # Intent breakdown
    intent_counts = Counter(e.get('intent', 'UNKNOWN') for e in entries)

    # Posted / auto-sent
    posted = [e for e in entries if e.get('posted')]

    # HIGH escalation reasons
    high_entries = [e for e in entries if e.get('priority') == 'HIGH']
    high_reasons = Counter(e.get('priority_reason', 'unknown') for e in high_entries)

    # KB gaps (NONE confidence)
    kb_none = [e for e in entries if e.get('kb_confidence') == 'NONE']
    intent_no_kb = Counter(e.get('intent') for e in kb_none)

    # Most-used KB files
    all_files = []
    for e in entries:
        all_files.extend(e.get('files_used', []))
    file_counts = Counter(all_files)

    # ── Print report ──────────────────────────────────────────────────────────
    sep = '─' * 56
    print(f"\n{'═' * 56}")
    print(f"  Teddy Report — last {days} day{'s' if days != 1 else ''}")
    print(f"  {total} tickets processed")
    print(f"{'═' * 56}")

    print(f"\n── Priority breakdown {'─' * 34}")
    for level, icon in [('IMMEDIATE', '🚨'), ('HIGH', '⚠️ '), ('LOW', '📝')]:
        n = by_priority.get(level, 0)
        p = n / total * 100 if total else 0
        print(f"  {icon} {level:<10} {n:>4}  {bar(p)}  {p:.1f}%")

    print(f"\n── KB confidence {'─' * 38}")
    for conf in ('HIGH', 'MEDIUM', 'LOW', 'NONE'):
        n = by_kb_conf.get(conf, 0)
        p = n / total * 100 if total else 0
        print(f"  {conf:<8} {n:>4}  {bar(p)}  {p:.1f}%")

    print(f"\n── Intent breakdown {'─' * 35}")
    for intent, count in intent_counts.most_common():
        p = count / total * 100
        print(f"  {intent:<20} {count:>4}  {bar(p)}  {p:.1f}%")

    if high_entries:
        print(f"\n── Why tickets went HIGH ({len(high_entries)} tickets) {'─' * 22}")
        print(f"  (Fix these to reduce owner interruptions)")
        for reason, count in high_reasons.most_common(10):
            print(f"  [{count:>3}]  {reason}")

    if kb_none:
        print(f"\n── Topics with NO KB match ({len(kb_none)} tickets) {'─' * 20}")
        print(f"  ➜  Write KB articles for these intents:")
        for intent, count in intent_no_kb.most_common():
            print(f"  {intent:<20} {count:>4} tickets without KB coverage")

    if file_counts:
        print(f"\n── Most-used KB files {'─' * 33}")
        for fpath, count in file_counts.most_common(10):
            short = fpath.replace('/app/', '').replace('kb/', 'kb/')
            print(f"  [{count:>3}]  {short}")

    print(f"\n── Auto-send {'─' * 42}")
    print(f"  Auto-posted to Gorgias : {len(posted)} of {total} ({pct(len(posted), total)})")

    print(f"\n── Action items {'─' * 39}")
    if n_immediate > 0:
        imm_pct = n_immediate / total * 100
        print(f"  {'⚠️ ' if imm_pct > 10 else '✅'} {n_immediate} IMMEDIATE tickets "
              f"({imm_pct:.1f}%) — these required owner action NOW")
    if kb_none:
        print(f"  ⚠️  {len(kb_none)} tickets had NO KB match — add articles to kb/")
    if n_high / total > 0.30 if total else False:
        print(f"  ⚠️  HIGH rate is {pct(n_high, total)} — review escalation reasons above")
    low_pct = n_low / total * 100 if total else 0
    if low_pct >= 60:
        print(f"  ✅ {low_pct:.1f}% of tickets are routine LOW — system is automating well")
    elif low_pct >= 40:
        print(f"  📈 {low_pct:.1f}% LOW — consider expanding AUTO_SEND_INTENTS as confidence grows")
    print()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Teddy log summary')
    parser.add_argument('--days', type=int, default=7, help='Days to include (default: 7)')
    args = parser.parse_args()
    summarise(args.days)
