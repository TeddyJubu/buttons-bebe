"""review.py — shared, safe logic for reviewing + promoting learned packets.

One code path used by BOTH the CLI (kb/scripts/review_learned.py) and the dashboard
API. Tolerant of packet formats (my collector's sections and the older
Hermes-written files), so existing learned/ files show up in the dashboard too.

Promotion always masks PII and writes a `needs_final_edit` exemplar — never live
until a human edits + sets status: confirmed and the KB is reindexed.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

import yaml

from . import config, pii

# section-name aliases -> canonical role
_SITUATION = ("customer situation", "context", "customer question")
_REPLY = ("human reply as sent", "agent's reply", "ideal reply", "agent reply")
_DRAFT = ("ai draft (internal note, cleaned)", "ai draft", "ai draft (for reference)")


def _parse(path: pathlib.Path) -> tuple[dict, dict, str]:
    raw = path.read_text(encoding="utf-8")
    front: dict = {}
    body = raw
    if raw.startswith("---"):
        try:
            _, fm, body = raw.split("---", 2)
            front = yaml.safe_load(fm) or {}
        except ValueError:
            body = raw
    sections: dict[str, str] = {}
    cur, buf = None, []
    for line in body.splitlines():
        if line.startswith("## "):
            if cur is not None:
                sections[cur] = "\n".join(buf).strip()
            cur, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf).strip()
    return front, sections, raw


def _jsonable(front: dict) -> dict:
    """Make front-matter safe for JSON (YAML turns dates into datetime objects)."""
    out = {}
    for k, v in (front or {}).items():
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def _pick(sections: dict, names: tuple) -> str:
    low = {k.lower(): v for k, v in sections.items()}
    for n in names:
        if n in low:
            return low[n]
    return ""


def _roles(sections: dict) -> dict:
    return {
        "situation": _pick(sections, _SITUATION),
        "reply": _pick(sections, _REPLY),
        "draft": _pick(sections, _DRAFT),
    }


def list_pending() -> list[dict]:
    if not config.LEARNED_DIR.exists():
        return []
    out = []
    for p in sorted(config.LEARNED_DIR.glob("ticket-*.md")):
        front, sections, _ = _parse(p)
        if not front.get("review_pending"):
            continue
        roles = _roles(sections)
        reply_pii = pii.summary(roles["reply"] or "")
        out.append({
            "ticket_id": front.get("source_ticket_id") or front.get("ticket_id"),
            "title": front.get("title", p.stem),
            "band": front.get("similarity_band", "n/a"),
            "language": front.get("reply_language", "?"),
            "flags": front.get("flags", []),
            "pii": reply_pii["by_kind"],
            "path": str(p),
        })
    return out


def get_packet(ticket_id) -> dict | None:
    p = config.LEARNED_DIR / f"ticket-{ticket_id}.md"
    if not p.exists():
        return None
    front, sections, raw = _parse(p)
    roles = _roles(sections)
    return {
        "ticket_id": ticket_id,
        "front": _jsonable(front),
        "situation": roles["situation"],
        "reply": roles["reply"],
        "draft": roles["draft"],
        "reply_masked": pii.mask(roles["reply"] or ""),
        "situation_masked": pii.mask(roles["situation"] or ""),
        "pii_reply": pii.summary(roles["reply"] or ""),
        "raw": raw,
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "ticket"


def approve(ticket_id, pii_cleared: bool, note: str = "", why: str = "") -> dict:
    """Promote a packet to kb/tickets/ (PII masked). Refuses without pii_cleared."""
    packet = get_packet(ticket_id)
    if packet is None:
        return {"ok": False, "error": "no such packet"}
    if not pii_cleared:
        return {"ok": False, "error": "pii_not_cleared", "pii": packet["pii_reply"]["by_kind"],
                "warning": packet["pii_reply"]["warning"]}

    config.TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    situation = pii.mask(packet["situation"])
    reply = pii.mask(packet["reply"])
    draft = pii.mask(packet["draft"])
    out = config.TICKETS_DIR / f"exemplar-learned-{ticket_id}-{_slug(situation)}.md"

    front = {
        "title": f"Exemplar (learned) — {situation[:60] or 'ticket ' + str(ticket_id)}",
        "category": "tickets",
        "status": "needs_final_edit",
        "source": "learned-from-ticket",
        "source_ticket_id": ticket_id,
        "tags": ["exemplar", "learned"],
    }
    quoted = (reply or "").replace("\n", "\n> ")
    body = f"""## Customer situation

{situation}

## How it was handled

{note or "(Reviewer: summarise the pattern in one or two sentences.)"}

## Ideal reply

> {quoted}

## Why

{why or "(Reviewer: why is this the right answer? What policy/tone does it show?)"}

<!-- AI draft the human replaced (masked, for reference):
{draft}
-->
"""
    out.write_text("---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
                   + "---\n\n" + body, encoding="utf-8")

    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    src = config.LEARNED_DIR / f"ticket-{ticket_id}.md"
    src.rename(config.ARCHIVE_DIR / src.name)
    return {"ok": True, "exemplar": str(out),
            "next": "edit it, set status: confirmed, then reindex"}


def reject(ticket_id, purge: bool = False) -> dict:
    src = config.LEARNED_DIR / f"ticket-{ticket_id}.md"
    if not src.exists():
        return {"ok": False, "error": "no such packet"}
    if purge:
        src.unlink()
        return {"ok": True, "action": "purged"}
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    src.rename(config.ARCHIVE_DIR / src.name)
    return {"ok": True, "action": "archived", "dir": str(config.ARCHIVE_DIR)}


def reindex() -> dict:
    update = config.KB_ROOT / "update.sh"
    if not update.exists():
        return {"ok": False, "error": f"no update.sh at {update}"}
    rc = subprocess.call(["bash", str(update)], cwd=str(config.KB_ROOT))
    return {"ok": rc == 0, "returncode": rc}
