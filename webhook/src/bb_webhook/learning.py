"""Capture console lesson candidates without inventing knowledge approval.

Only a delivered public reply with explicit owner learning approval, actor,
operation ID and approved revision can be promoted. Notes and generated rewrites
are never eligible. Legacy packets remain unapproved. Raw packets may contain
PII and are kept mode0600 outside the indexed corpus.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import uuid
import fcntl
import json
import os
import pathlib
import secrets
import sys
import tempfile

_AGENT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

try:
    from feedback import config as _fc  # reuse the same KB folder config
    LEARNED_DIR = _fc.LEARNED_DIR
except Exception:  # pragma: no cover - fallback if feedback pkg unavailable
    LEARNED_DIR = _AGENT_ROOT / "KB" / "learned"

LEDGER = LEARNED_DIR / "_ledger.json"  # underscore => never indexed


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_staged_content(handle, content):
    handle.write(content)
    handle.flush()
    os.fsync(handle.fileno())


def _write_unique_lesson(ticket_id: object, content: str, operation_id="") -> tuple[pathlib.Path, bool]:
    """Publish a complete private packet atomically without replacing a prior one."""
    descriptor, temporary = tempfile.mkstemp(prefix=".capture-", dir=LEARNED_DIR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _write_staged_content(handle, content)
        for _attempt in range(20):
            token = secrets.token_hex(6)
            out = LEARNED_DIR / (f"lesson-action-{operation_id}.md" if operation_id else f"lesson-{ticket_id}-{_now()}-{token}.md")
            try:
                os.link(temporary, out)
            except FileExistsError:
                if operation_id:
                    if out.read_text(encoding="utf-8") != content:
                        raise ValueError("conflicting action lesson")
                    with out.open("rb") as existing:
                        os.fsync(existing.fileno())
                    _sync_directory(LEARNED_DIR)
                    return out, False
                continue
            _sync_directory(LEARNED_DIR)
            return out, True
        raise FileExistsError("could not allocate a unique lesson filename")
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)


def _bump_ledger(kind: str, edited: bool) -> None:
    """Count one ledger write. Dedupe lives in _write_unique_lesson + the
    learning_recorded DB flag, not here (the old unbounded _operations list
    was a weaker second copy)."""
    temp_path: pathlib.Path | None = None
    try:
        LEARNED_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = LEDGER.with_suffix(LEDGER.suffix + ".lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, "r+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            data = {}
            if LEDGER.exists():
                loaded = json.loads(LEDGER.read_text(encoding="utf-8") or "{}")
                if isinstance(loaded, dict):
                    data = loaded
            # Dedupe for operation_id retries lives in _write_unique_lesson's
            # deterministic filename (created=False) and the learning_recorded
            # DB flag — the ledger counts only writes that created a packet.
            data.pop("_operations", None)  # legacy key; never carry it into a rewrite
            data["total"] = data.get("total", 0) + 1
            data[kind] = data.get(kind, 0) + 1
            if kind == "sent":
                key = "edited" if edited else "unchanged"
                data[key] = data.get(key, 0) + 1
            data["updated"] = _now()

            temp_path = LEDGER.with_name(
                f".{LEDGER.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
            )
            fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, LEDGER)
            _sync_directory(LEARNED_DIR)
            temp_path = None
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception as exc:
        logging.getLogger(__name__).error("Learning ledger write failed: %s", type(exc).__name__)
        raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def record_lesson(kind, ticket_id, customer_message, ai_draft, final_text,
                  instruction="", customer_name="", *, operation_id="", review_actor="",
                  learning_approved=False, delivery_status="unknown", approved_at="") -> bool:
    """Write a raw lesson packet to learned/. kind = sent | note | rewrite."""
    try:
        import yaml
        LEARNED_DIR.mkdir(parents=True, exist_ok=True)
        if operation_id and str(uuid.UUID(operation_id)) != operation_id:
            raise ValueError("invalid action id")
        approved = (kind == "sent" and learning_approved is True and delivery_status == "sent"
                    and bool(review_actor) and bool(operation_id) and bool(approved_at))
        edited = bool(final_text and ai_draft
                      and final_text.strip() != ai_draft.strip())
        fm = {
            "schema_version": 2,
            "customer_message": (customer_message or "").strip(),
            "approved_text": (final_text or "").strip(),
            "title": f"lesson {kind} - ticket {ticket_id}",
            "kind": kind,
            "source_ticket_id": ticket_id,
            "edited": edited,
            "customer_name": customer_name or "",
            "captured": approved_at or _now(),
            "review_pending": not approved,
            "learning_approved": approved,
            "delivery_status": delivery_status,
            "review_actor": review_actor if approved else "",
            "approved_at": approved_at if approved else "",
            "operation_id": operation_id,
            "final_text_sha256": hashlib.sha256((final_text or "").strip().encode()).hexdigest(),
            "customer_message_sha256": hashlib.sha256((customer_message or "").strip().encode()).hexdigest(),
        }
        body = (
            "## Customer situation\n\n" + (customer_message or "").strip() + "\n\n"
            "## AI draft\n\n" + (ai_draft or "").strip() + "\n\n"
            + ("## Generated rewrite\n\n" if kind == "rewrite" else "## Human final (" + kind + ")\n\n") + (final_text or "").strip() + "\n"
        )
        if instruction:
            body += "\n## Rewrite instruction\n\n" + instruction.strip() + "\n"
        content = ("---\n"
                   + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
                   + "---\n\n" + body)
        _path, created = _write_unique_lesson(ticket_id, content, operation_id)
        if created:
            # Retry with the same operation_id finds the identical packet and
            # skips the bump — the file layer is the dedupe now.
            _bump_ledger(kind, edited)
        return True
    except Exception as exc:
        logging.getLogger(__name__).error("Learning lesson capture failed: %s", type(exc).__name__)
        return False


def ledger() -> dict:
    try:
        if LEDGER.exists():
            return {key: value for key, value in json.loads(LEDGER.read_text() or "{}").items() if key != "_operations"}
    except Exception:
        pass
    return {}
