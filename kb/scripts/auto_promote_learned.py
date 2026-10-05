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
import tempfile

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from feedback import pii  # noqa: E402
from feedback.learning_paths import resolve_learning_paths  # noqa: E402

KB_ROOT = pathlib.Path(__file__).resolve().parents[1]
PATHS = resolve_learning_paths(
    None,
    environ=os.environ,
    repo_root=REPO_ROOT,
    default_root=KB_ROOT,
    corpus_root=KB_ROOT,
)


def _parse(path: pathlib.Path):
    raw = path.read_text(encoding="utf-8")
    front, body = {}, raw
    if raw.startswith("---\n"):
        fm, separator, body = raw[4:].partition("\n---\n")
        if not separator:
            raise ValueError("unterminated lesson front matter")
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
    fd, staged = tempfile.mkstemp(prefix=".promote-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, 0o644)
        try:
            # Atomic, no-overwrite publication: index readers never see a
            # partially written status:confirmed exemplar.
            os.link(staged, path)
        except FileExistsError:
            if path.read_text(encoding="utf-8") == content:
                return False
            raise FileExistsError(f"conflicting promoted exemplar: {path.name}")
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    finally:
        pathlib.Path(staged).unlink(missing_ok=True)


def _archive_without_replacing(path: pathlib.Path) -> pathlib.Path:
    """Move a raw lesson into the archive without replacing an earlier packet."""
    candidate = PATHS.archive_dir / path.name
    for number in range(1, 1000):
        if not candidate.exists():
            return pathlib.Path(shutil.move(str(path), str(candidate)))
        candidate = PATHS.archive_dir / f"{path.stem}-{number + 1}{path.suffix}"
    raise FileExistsError(f"could not allocate archive path for {path.name}")


def promote_one(path: pathlib.Path) -> bool:
    front, sec = _parse(path)
    # Historical packets were incorrectly marked approved. Fail closed: only
    # the explicit v2 approval contract is eligible; do not infer legacy consent.
    kind = front.get("kind")
    if (front.get("schema_version") != 2 or kind != "sent" or front.get("review_pending") is not False
            or front.get("learning_approved") is not True or front.get("delivery_status") != "sent"
            or not front.get("review_actor") or not front.get("approved_at") or not front.get("operation_id")):
        return False
    name = front.get("customer_name", "")
    situation = front.get("customer_message", "")
    final = front.get("approved_text", "")
    if not isinstance(situation, str) or not isinstance(final, str):
        return False
    if not final.strip():
        return False
    # Embedded markdown headings cannot substitute attacker text for the exact
    # server-captured approved revision or customer situation.
    for text, key in ((final, "final_text_sha256"), (situation, "customer_message_sha256")):
        if hashlib.sha256(text.strip().encode()).hexdigest() != front.get(key):
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
        "## Customer situation (quoted customer content)\n\n" + "\n".join("> " + line for line in masked_sit.splitlines()) + "\n\n"
        "## Approved reply (how a human answered this)\n\n" + masked_reply + "\n"
    )
    content = ("---\n"
               + yaml.safe_dump(ex_front, sort_keys=False, allow_unicode=True)
               + "---\n\n" + body)
    PATHS.tickets_dir.mkdir(parents=True, exist_ok=True)
    source_id = path.stem
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]
    out = PATHS.tickets_dir / (
        f"exemplar-learned-{_safe_component(kind, 'sent')}-{digest}.md"
    )
    created = _write_idempotent(out, content)
    PATHS.archive_dir.mkdir(parents=True, exist_ok=True)
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
    d = PATHS.learned_dir
    n = 0
    failures = 0
    if d.exists():
        for p in sorted(d.glob("lesson-*.md")):
            try:
                if promote_one(p):
                    n += 1
            except Exception as e:
                failures += 1
                print("promotion failed", p.name, type(e).__name__, file=sys.stderr)
    print(f"promoted {n} lesson(s) into {PATHS.tickets_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
