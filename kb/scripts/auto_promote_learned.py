#!/usr/bin/env python3
"""Auto-promote console lesson packets into indexed, PII-masked exemplars.

Reads KB/learned/lesson-*.md (written when a human Sends / Notes / Requests-edit
in the console), masks identifiers (emails, phones, orders, addresses, and the
known customer name), and writes a clean 'confirmed' exemplar into KB/tickets/
(which IS indexed). The raw packet is moved to _archive_learned/.

Runs nightly (see learn-nightly.sh); index_kb.py is run afterwards.
"""
from __future__ import annotations
import datetime
import pathlib
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from feedback import config, pii  # noqa: E402


def _parse(path: pathlib.Path):
    raw = path.read_text(encoding="utf-8")
    front, body = {}, raw
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        front = yaml.safe_load(fm) or {}
    sections, cur, buf = {}, None, []
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


def _mask(text: str, name: str = "") -> str:
    t = pii.mask(text or "")
    if name:
        for tok in [name] + str(name).split():
            tok = tok.strip()
            if len(tok) >= 3:
                t = re.sub(r"\b" + re.escape(tok) + r"\b", "[name]", t, flags=re.I)
    return t


def promote_one(path: pathlib.Path) -> bool:
    front, sec = _parse(path)
    kind = front.get("kind", "sent")
    name = front.get("customer_name", "")
    situation = sec.get("Customer situation", "")
    final = ""
    for k, v in sec.items():
        if k.lower().startswith("human final"):
            final = v
            break
    if not (final or "").strip():
        return False
    tid = front.get("source_ticket_id", "x")
    masked_sit = _mask(situation, name)
    masked_reply = _mask(final, name)
    ex_front = {
        "title": f"Approved reply - {(masked_sit[:56] or ('ticket ' + str(tid)))}",
        "category": "tickets",
        "status": "confirmed",
        "source": "learned-auto",
        "source_ticket_id": tid,
        "kind": kind,
        "tags": ["exemplar", "learned", "approved"],
    }
    body = (
        "## Customer situation\n\n" + masked_sit + "\n\n"
        "## Approved reply (how a human answered this)\n\n" + masked_reply + "\n"
    )
    content = ("---\n"
               + yaml.safe_dump(ex_front, sort_keys=False, allow_unicode=True)
               + "---\n\n" + body)
    config.TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(datetime.datetime.utcnow().timestamp())
    out = config.TICKETS_DIR / f"exemplar-learned-{tid}-{ts}.md"
    out.write_text(content, encoding="utf-8")
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path.rename(config.ARCHIVE_DIR / path.name)
    return True


def main() -> int:
    d = config.LEARNED_DIR
    n = 0
    if d.exists():
        for p in sorted(d.glob("lesson-*.md")):
            try:
                if promote_one(p):
                    n += 1
            except Exception as e:
                print("skip", p.name, e)
    print(f"promoted {n} lesson(s) into {config.TICKETS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
