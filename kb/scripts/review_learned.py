#!/usr/bin/env python3
"""review_learned.py — the human gate: turn captured packets into live exemplars.

This is the ONLY path from kb/learned/ (a holding pen the search engine ignores)
into kb/tickets/ (indexed, and required to be PII-free). It is deliberately manual.

Commands:
    list                       show pending review packets
    show <ticket_id>           print one packet (situation, draft, reply, PII, hint)
    approve <ticket_id> --pii-cleared   promote to kb/tickets/ (refuses without the flag)
    reject  <ticket_id> [--purge]       archive (or delete) a packet
    reindex                    run the KB re-index ONCE after a batch of approvals
    stats                      capture ledger summary

Safety by design:
  * approve REFUSES unless you pass --pii-cleared, and it prints the PII findings so
    you actually look. The regex highlighter is an aid, not a guarantee — you are the
    control, especially for names, which patterns cannot catch.
  * approve writes a DRAFT exemplar (status: needs_final_edit) with identifiers
    masked to placeholders. Edit it, set status: confirmed, THEN reindex.
  * reindex is separate and batched, so the index is never half-built per approval.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from feedback import config, pii  # noqa: E402


def _packet_path(ticket_id) -> pathlib.Path:
    return config.LEARNED_DIR / f"ticket-{ticket_id}.md"


def _parse(path: pathlib.Path) -> tuple[dict, dict]:
    """Return (front_matter, {section_title: body})."""
    raw = path.read_text(encoding="utf-8")
    front: dict = {}
    body = raw
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        front = yaml.safe_load(fm) or {}
    sections: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if cur is not None:
                sections[cur] = "\n".join(buf).strip()
            cur = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf).strip()
    return front, sections


def _pending() -> list[pathlib.Path]:
    if not config.LEARNED_DIR.exists():
        return []
    out = []
    for p in sorted(config.LEARNED_DIR.glob("ticket-*.md")):
        front, _ = _parse(p)
        if front.get("review_pending"):
            out.append(p)
    return out


def cmd_list(_args) -> int:
    packets = _pending()
    if not packets:
        print("No pending review packets in", config.LEARNED_DIR)
        return 0
    print(f"{len(packets)} pending packet(s):\n")
    for p in packets:
        front, _ = _parse(p)
        pii_kinds = front.get("pii_findings_reply") or {}
        print(f"  ticket {front.get('source_ticket_id')}  "
              f"band={front.get('similarity_band')}  "
              f"lang={front.get('reply_language')}  "
              f"flags={front.get('flags') or []}  "
              f"pii={pii_kinds or 'none'}")
    print("\nReview one with:  review_learned.py show <ticket_id>")
    return 0


def cmd_show(args) -> int:
    path = _packet_path(args.ticket_id)
    if not path.exists():
        print("No packet for ticket", args.ticket_id)
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "ticket"


def cmd_approve(args) -> int:
    path = _packet_path(args.ticket_id)
    if not path.exists():
        print("No packet for ticket", args.ticket_id)
        return 1
    front, sections = _parse(path)

    situation = sections.get("Customer situation", "")
    reply = sections.get("Human reply as sent", "")
    draft = sections.get("AI draft (internal note, cleaned)", "")

    # Always show what the highlighter found before letting anyone approve.
    reply_pii = pii.summary(reply)
    print(f"PII highlighter on the reply: {reply_pii['by_kind'] or 'none detected'}")
    print(f"  {reply_pii['warning']}")
    if not args.pii_cleared:
        print("\nREFUSED: re-run with --pii-cleared once you've personally confirmed "
              "there are NO names/addresses/order numbers left. The regex does not "
              "catch names.")
        return 2

    tickets_dir = config.TICKETS_DIR
    tickets_dir.mkdir(parents=True, exist_ok=True)
    masked_situation = pii.mask(situation)
    slug = _slugify(f"ticket-{args.ticket_id}")
    out = tickets_dir / f"exemplar-learned-{args.ticket_id}-{slug}.md"

    ex_front = {
        "title": f"Exemplar (learned) — {masked_situation[:60] or 'ticket ' + str(args.ticket_id)}",
        "category": "tickets",
        "status": "needs_final_edit",   # human sets 'confirmed' after editing
        "source": "learned-from-ticket",
        "source_ticket_id": args.ticket_id,
        "tags": ["exemplar", "learned"],
    }
    ex_body = f"""## Customer situation

{pii.mask(situation)}

## How it was handled

(Reviewer: summarise the pattern in one or two sentences. Flags on capture: {front.get('flags') or []}.)

## Ideal reply

> {pii.mask(reply).replace(chr(10), chr(10) + '> ')}

## Why

(Reviewer: why is this the right answer? What policy/tone does it show?)

<!-- For reference, the AI draft the human replaced (masked):
{pii.mask(draft)}
-->
"""
    content = "---\n" + yaml.safe_dump(ex_front, sort_keys=False, allow_unicode=True) + "---\n\n" + ex_body

    if args.dry_run:
        print("--- DRY RUN: would write", out, "---\n")
        print(content)
        return 0

    out.write_text(content, encoding="utf-8")
    # retire the packet
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path.rename(config.ARCHIVE_DIR / path.name)
    print(f"Wrote draft exemplar: {out}")
    print("Next: edit it (fill 'How it was handled' + 'Why', remove the AI-draft comment,")
    print("set status: confirmed), then run:  review_learned.py reindex")
    return 0


def cmd_reject(args) -> int:
    path = _packet_path(args.ticket_id)
    if not path.exists():
        print("No packet for ticket", args.ticket_id)
        return 1
    if args.purge:
        path.unlink()
        print("Purged packet for ticket", args.ticket_id)
    else:
        config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        path.rename(config.ARCHIVE_DIR / path.name)
        print("Archived to", config.ARCHIVE_DIR, "(underscore folder — never indexed).")
        print("Note: archived packets still contain raw text. Use --purge to delete.")
    return 0


def cmd_reindex(_args) -> int:
    update = config.KB_ROOT / "update.sh"
    if not update.exists():
        print("No update.sh at", update, "— run your KB re-index manually.")
        return 1
    print("Running KB re-index:", update)
    return subprocess.call(["bash", str(update)], cwd=str(config.KB_ROOT))


def cmd_stats(_args) -> int:
    from feedback import store
    import json
    print(json.dumps(store.stats(), indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Review + promote learned feedback packets.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    sp = sub.add_parser("show"); sp.add_argument("ticket_id"); sp.set_defaults(func=cmd_show)
    ap = sub.add_parser("approve")
    ap.add_argument("ticket_id")
    ap.add_argument("--pii-cleared", action="store_true", help="assert you read it for PII")
    ap.add_argument("--dry-run", action="store_true")
    ap.set_defaults(func=cmd_approve)
    rp = sub.add_parser("reject"); rp.add_argument("ticket_id")
    rp.add_argument("--purge", action="store_true"); rp.set_defaults(func=cmd_reject)
    sub.add_parser("reindex").set_defaults(func=cmd_reindex)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
