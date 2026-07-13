"""notices_lib.py -- the Notice Board.

Owner-posted notices that OVERRIDE all other knowledge-base answers while they
are live. Each notice may carry an optional expiry; an expired notice is ignored
the instant it is read (and a cleanup timer also physically removes it). This
file is the shared contract with the Node `kb-admin` API, which reads and writes
the same JSON, so keep the schema below stable:

    {
      "id":         "n_1752350000000_ab12",   # unique, sortable
      "text":       "Same-day delivery, free shipping on all orders.",
      "created_at": "2026-07-12T20:40:00+00:00",
      "expires_at": "2026-07-14T00:00:00+00:00" | null,   # null = until removed
      "created_by": "owner"
    }

Nothing here ever raises out to the search path: callers wrap use in try/except,
and the readers below already degrade to an empty board on any error, so a
missing or corrupt file can never break customer search.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent      # the KB/ folder
NOTICES_DIR = KB_DIR / "notices"
NOTICES_FILE = NOTICES_DIR / "notices.json"
LOCK_DIR = NOTICES_DIR / ".notices.lock"
LOCK_STALE_SECONDS = 60


class NoticeBusy(RuntimeError):
    """Another writer currently owns the Notice Board transaction."""

# Stamped in front of every notice handed to the agent so it is unmistakable.
OVERRIDE_PREFIX = (
    "[NOTICE BOARD — OWNER OVERRIDE. This is the current truth and OVERRIDES any "
    "conflicting policy, FAQ, or product detail below. Follow it exactly while it "
    "is posted.]"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts) -> datetime | None:
    """Parse an ISO timestamp to an aware UTC datetime; None if empty/invalid."""
    if not ts:
        return None
    try:
        s = str(ts).strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _valid_notice(n: object) -> bool:
    if not isinstance(n, dict):
        return False
    if not all(isinstance(n.get(key), str) and n[key].strip() for key in ("id", "text", "created_at", "created_by")):
        return False
    if _parse(n["created_at"]) is None:
        return False
    return n.get("expires_at") is None or _parse(n.get("expires_at")) is not None


def _read_items(*, strict: bool) -> list[dict]:
    with open(NOTICES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("notice store must be a JSON list")
    valid = [n for n in data if _valid_notice(n)]
    if strict and len(valid) != len(data):
        raise ValueError("notice store contains malformed entries")
    return valid


@contextmanager
def _notice_lock():
    """Acquire a short-lived cross-language lock using atomic directory creation."""
    NOTICES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        LOCK_DIR.mkdir()
    except FileExistsError:
        try:
            stale = time.time() - LOCK_DIR.stat().st_mtime > LOCK_STALE_SECONDS
        except OSError:
            stale = False
        if not stale:
            raise NoticeBusy("notice board write already running")
        try:
            LOCK_DIR.rmdir()
            LOCK_DIR.mkdir()
        except OSError as exc:
            raise NoticeBusy("notice board write already running") from exc
    try:
        yield
    finally:
        try:
            LOCK_DIR.rmdir()
        except OSError:
            pass


def load_all() -> list[dict]:
    """Every notice on file. Empty list on any problem (fail-safe)."""
    try:
        return _read_items(strict=False)
    except Exception:
        return []


def _write(items: list[dict]) -> None:
    """Write atomically (temp file + rename) so a concurrent reader never sees
    a half-written file."""
    NOTICES_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".notices-", suffix=".tmp", dir=NOTICES_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, NOTICES_FILE)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def is_active(n: dict, now: datetime | None = None) -> bool:
    now = now or _now()
    exp = _parse(n.get("expires_at"))
    return exp is None or exp > now


def active_notices(now: datetime | None = None) -> list[dict]:
    now = now or _now()
    return [n for n in load_all() if is_active(n, now)]


def add_notice(text: str, expires_at=None, created_by: str = "owner") -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("notice text is required")
    created_by = str(created_by or "").strip() or "owner"
    exp = _parse(expires_at)
    if expires_at not in (None, "") and exp is None:
        raise ValueError("invalid expires_at")
    notice = {
        "id": f"n_{int(time.time() * 1000)}_{secrets.token_hex(2)}",
        "text": text,
        "created_at": _now().isoformat(),
        "expires_at": exp.isoformat() if exp else None,
        "created_by": created_by,
    }
    with _notice_lock():
        items = _read_items(strict=True) if NOTICES_FILE.exists() else []
        items.append(notice)
        _write(items)
    return notice


def remove_notice(nid: str) -> bool:
    with _notice_lock():
        items = _read_items(strict=True) if NOTICES_FILE.exists() else []
        kept = [n for n in items if n.get("id") != nid]
        if len(kept) == len(items):
            return False
        _write(kept)
        return True


def purge_expired(now: datetime | None = None) -> int:
    """Physically drop expired notices. Returns how many were removed."""
    now = now or _now()
    with _notice_lock():
        items = _read_items(strict=True) if NOTICES_FILE.exists() else []
        kept = [n for n in items if is_active(n, now)]
        removed = len(items) - len(kept)
        if removed:
            _write(kept)
        return removed


def as_search_results(now: datetime | None = None) -> list[dict]:
    """Active notices shaped exactly like `search_kb` results and marked as the
    owner override. A very high score keeps them first if anything re-sorts."""
    results: list[dict] = []
    for n in active_notices(now):
        results.append(
            dict(
                score=999.0,
                file="notices/notices.json",
                title="NOTICE BOARD",
                category="notices",
                status="confirmed",
                sensitive=False,
                heading="Owner override",
                text=f"{OVERRIDE_PREFIX}\n{n['text']}",
            )
        )
    return results
