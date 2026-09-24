"""
gap_review.py — interactive CLI for answering KB gaps.

When Teddy can't find a KB match for a customer question, it logs the gap to
kb/gaps.jsonl. Run this script to review those gaps and write your answer.
Each answer is saved as a new KB article in kb/learned/ that Teddy will use
automatically for future tickets on the same topic.

Usage
-----
    python3 tools/gap_review.py            # review all unanswered gaps
    python3 tools/gap_review.py --intent RETURN_REQUEST  # filter by intent

Commands during review
-----------------------
    [Enter]  Save your typed answer as a KB article
    s        Skip this gap (keep it unanswered)
    q        Quit
"""

import argparse
import json
import sys
import time
from pathlib import Path

KB_DIR      = Path(__file__).parent.parent / 'kb'
GAP_FILE    = KB_DIR / 'gaps.jsonl'
LEARNED_DIR = KB_DIR / 'learned'


def load_gaps(intent_filter: str = '') -> list:
    if not GAP_FILE.exists():
        return []
    gaps = []
    with open(GAP_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get('answered'):
                    continue
                if intent_filter and rec.get('intent') != intent_filter.upper():
                    continue
                gaps.append(rec)
            except Exception:
                pass
    return gaps


def mark_answered(gap: dict):
    """Rewrite gaps.jsonl with this specific gap marked answered."""
    if not GAP_FILE.exists():
        return
    lines = GAP_FILE.read_text(encoding='utf-8').splitlines()
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
            if (
                rec.get('ticket_id') == gap.get('ticket_id')
                and rec.get('timestamp') == gap.get('timestamp')
                and not rec.get('answered')
            ):
                rec['answered'] = True
                line = json.dumps(rec)
        except Exception:
            pass
        out.append(line)
    GAP_FILE.write_text('\n'.join(out) + '\n', encoding='utf-8')


def save_answer(gap: dict, answer: str):
    LEARNED_DIR.mkdir(parents=True, exist_ok=True)
    intent = (gap.get('intent') or 'unknown').lower()
    ts     = time.strftime('%Y%m%d%H%M%S', time.gmtime())
    date   = time.strftime('%Y-%m-%d', time.gmtime())
    slug   = f"{intent}-gap-{ts}"
    out    = LEARNED_DIR / f"{slug}.md"

    article = (
        f"---\n"
        f"type: learned\n"
        f"title: KB gap answer — {intent.upper()}\n"
        f"tags: [{intent}, learned, gap-answer]\n"
        f"timestamp: {date}\n"
        f"---\n\n"
        f"# {intent.upper()} — gap answer\n\n"
        f"**Customer asked:** {gap.get('message', '')}\n\n"
        f"**Answer:**\n\n{answer}\n"
    )
    out.write_text(article, encoding='utf-8')
    print(f"  Saved → {out.name}")


def main():
    parser = argparse.ArgumentParser(description='Review KB gaps')
    parser.add_argument('--intent', default='', help='Filter by intent (e.g. RETURN_REQUEST)')
    args = parser.parse_args()

    gaps = load_gaps(args.intent)
    if not gaps:
        label = f" for intent={args.intent}" if args.intent else ''
        print(f"No unanswered KB gaps{label}.")
        return

    print(f"\n{'=' * 56}")
    print(f"  KB Gap Review — {len(gaps)} unanswered gaps")
    print(f"  [Enter] save answer  |  s = skip  |  q = quit")
    print(f"{'=' * 56}\n")

    for i, gap in enumerate(gaps, 1):
        print(f"[{i}/{len(gaps)}]  Intent: {gap.get('intent')}  |  Ticket: #{gap.get('ticket_id')}")
        print(f"  Customer said: {gap.get('message', '')}")
        print()
        try:
            answer = input("  Your answer (or 's' to skip, 'q' to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            break

        if answer.lower() == 'q':
            break
        if answer.lower() in ('s', ''):
            print("  Skipped.\n")
            continue

        save_answer(gap, answer)
        mark_answered(gap)
        print()

    print("Done.")


if __name__ == '__main__':
    main()
