#!/usr/bin/env python3
"""Auto-promote console lesson packets into indexed, PII-masked exemplars.

Reads KB/learned/lesson-*.md (written when a human Sends / Notes / Requests-edit
in the console), masks identifiers (emails, phones, orders, addresses, and the
known customer name), and writes a clean 'confirmed' exemplar into KB/tickets/
(which IS indexed). The raw packet is moved to _archive_learned/.

Runs nightly (see learn-nightly.sh); index_kb.py is run afterwards.
"""
from __future__ import annotations
import hashlib
import os
import pathlib
import re
import shutil
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
    names = [name] if str(name).strip() else []
    return pii.mask_with_known_values(text or "", customer_names=names)


def _safe_component(value: object, fallback: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-")
    return cleaned[:80] or fallback


def _write_idempotent(path: pathlib.Path, content: str) -> bool:
    """Create a deterministic exemplar, or accept an identical prior write.

    Returns True only when this call created the file. A different file at the
    deterministic path is treated as corruption rather than silently duplicated.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if path.read_text(encoding="utf-8") == content:
            return False
        raise FileExistsError(f"conflicting promoted exemplar: {path.name}")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _archive_without_replacing(path: pathlib.Path) -> pathlib.Path:
    """Move a raw lesson into the archive without replacing an earlier packet."""
    candidate = config.ARCHIVE_DIR / path.name
    for number in range(1, 1000):
        if not candidate.exists():
            return pathlib.Path(shutil.move(str(path), str(candidate)))
        candidate = config.ARCHIVE_DIR / f"{path.stem}-{number + 1}{path.suffix}"
    raise FileExistsError(f"could not allocate archive path for {path.name}")


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
    masked_sit = _mask(situation, name)
    masked_reply = _mask(final, name)
    ex_front = {
        "title": f"Approved reply - {(masked_sit[:56] or 'support example')}",
        "category": "tickets",
        "status": "confirmed",
        "source": "learned-auto",
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
    source_id = path.stem
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]
    out = config.TICKETS_DIR / (
        f"exemplar-learned-{_safe_component(kind, 'sent')}-{digest}.md"
    )
    created = _write_idempotent(out, content)
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _archive_without_replacing(path)
    except Exception:
        # Keep lesson + exemplar as an all-or-nothing pair. On retry, an
        # identical pre-existing exemplar is recognized without duplication.
        if created:
            out.unlink(missing_ok=True)
        raise
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
