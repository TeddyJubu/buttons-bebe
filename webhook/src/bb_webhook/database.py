"""SQLite database layer for webhook idempotency and job queue.

Uses WAL journal mode for better concurrent-read performance and
retries on ``database is locked`` to handle write contention when
Gorgias sends bursts of webhook deliveries.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .config import get_settings
from .logging_utils import get_logger

logger = get_logger(__name__)

_LOCK_RETRY_ATTEMPTS = 5
_LOCK_RETRY_DELAY = 0.15  # seconds


# ── Schema ────────────────────────────────────────────────

_SCHEMA = """
-- Webhook events we've already processed (idempotency / dedup)
CREATE TABLE IF NOT EXISTS webhook_events (
    message_id   TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    ticket_id    INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    author_type  TEXT NOT NULL,          -- customer | agent | system
    raw_payload  TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    processed_at TEXT
);

-- Jobs for the orchestrator worker (later stages)
CREATE TABLE IF NOT EXISTS job_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT NOT NULL,
    ticket_id    INTEGER NOT NULL,
    message_id   TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    author_type  TEXT NOT NULL,
    is_customer_message INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | processing | done | failed | skipped
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    error        TEXT,
    retry_count  INTEGER NOT NULL DEFAULT 0
);

-- Parsed message view — clean, denormalized rows for the dashboard
CREATE TABLE IF NOT EXISTS parsed_messages (
    message_id        TEXT PRIMARY KEY,
    ticket_id         INTEGER NOT NULL,
    event_type        TEXT NOT NULL,
    author_type       TEXT NOT NULL,
    author_email      TEXT,
    channel           TEXT,
    customer_email    TEXT,
    ticket_subject    TEXT,
    message_text      TEXT,
    intents           TEXT,             -- JSON array of intent names
    is_customer_message INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT,             -- event timestamp from Gorgias
    received_at       TEXT NOT NULL     -- when our webhook received it
);

-- AI processing results (written by the orchestrator after Hermes runs)
CREATE TABLE IF NOT EXISTS ticket_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id       INTEGER NOT NULL,
    message_id      TEXT NOT NULL,
    job_id           INTEGER,
    priority         TEXT,              -- critical | high | normal | low
    action           TEXT,              -- drafted | sensitive_draft | escalated | no_kb_match
    reason           TEXT,
    notify_owner     INTEGER NOT NULL DEFAULT 0,
    gorgias_priority_set INTEGER NOT NULL DEFAULT 0,
    note_posted      INTEGER NOT NULL DEFAULT 0,
    draft_text       TEXT,              -- the full draft or escalation note
    processed_at     TEXT NOT NULL,
    UNIQUE(ticket_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON job_queue(status);
CREATE INDEX IF NOT EXISTS idx_jobs_tenant ON job_queue(tenant_id);
CREATE INDEX IF NOT EXISTS idx_jobs_customer ON job_queue(is_customer_message);
CREATE INDEX IF NOT EXISTS idx_parsed_received ON parsed_messages(received_at);
CREATE INDEX IF NOT EXISTS idx_parsed_customer ON parsed_messages(is_customer_message);
CREATE INDEX IF NOT EXISTS idx_results_ticket ON ticket_results(ticket_id);
CREATE INDEX IF NOT EXISTS idx_results_message ON ticket_results(message_id);

-- Key-value settings store (dashboard toggles, etc.)
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


async def init_db(db_path: Path | None = None) -> None:
    """Create the database, tables, and enable WAL journal mode."""
    if db_path is None:
        settings = get_settings()
        db_path = settings.db_path_absolute

    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")  # 5 s SQLite-level wait
        await conn.executescript(_SCHEMA)
        await conn.commit()

    logger.info("Database initialized (WAL mode) at %s", db_path)


async def _with_retry(
    operation: str,
    sql: str,
    params: tuple,
    db_path: Path,
    *,
    fetch: bool = False,
) -> Any | None:  # noqa: F401 -- Any imported via aiosqlite.Row
    """Execute *sql* with retry-on-locked."""
    for attempt in range(_LOCK_RETRY_ATTEMPTS):
        try:
            async with aiosqlite.connect(str(db_path)) as conn:
                await conn.execute("PRAGMA busy_timeout=3000")
                if fetch:
                    conn.row_factory = aiosqlite.Row
                    cursor = await conn.execute(sql, params)
                    result = await cursor.fetchall()
                    await cursor.close()
                    return result
                else:
                    cursor = await conn.execute(sql, params)
                    await conn.commit()
                    row = cursor.lastrowid
                    await cursor.close()
                    return row
        except aiosqlite.OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < _LOCK_RETRY_ATTEMPTS - 1:
                logger.warning("DB locked on %s — retry %d/%d", operation, attempt + 1, _LOCK_RETRY_ATTEMPTS)
                await asyncio.sleep(_LOCK_RETRY_DELAY)
                continue
            raise
    return None


async def is_duplicate(message_id: str, db_path: Path | None = None) -> bool:
    """Check if we've already received this message_id."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    rows = await _with_retry(
        "is_duplicate",
        "SELECT 1 FROM webhook_events WHERE message_id = ?",
        (message_id,),
        db_path,
        fetch=True,
    )
    return bool(rows) and len(rows) > 0


async def record_event(
    message_id: str,
    tenant_id: str,
    ticket_id: int,
    event_type: str,
    author_type: str,
    raw_payload: str,
    db_path: Path | None = None,
) -> None:
    """Persist a webhook event for dedup and audit."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    now = datetime.now(timezone.utc).isoformat()

    await _with_retry(
        "record_event",
        """INSERT OR IGNORE INTO webhook_events
           (message_id, tenant_id, ticket_id, event_type,
            author_type, raw_payload, received_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (message_id, tenant_id, ticket_id, event_type,
         author_type, raw_payload, now),
        db_path,
    )


async def enqueue_job(
    tenant_id: str,
    ticket_id: int,
    message_id: str,
    event_type: str,
    author_type: str,
    is_customer_message: bool,
    payload: dict,
    db_path: Path | None = None,
) -> int:
    """Add a job to the queue for the orchestrator worker."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    now = datetime.now(timezone.utc).isoformat()

    job_id = await _with_retry(
        "enqueue_job",
        """INSERT INTO job_queue
           (tenant_id, ticket_id, message_id, event_type,
            author_type, is_customer_message, status, payload, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tenant_id, ticket_id, message_id, event_type,
         author_type, int(is_customer_message), "pending", json.dumps(payload), now),
        db_path,
    )

    return job_id or 0


async def get_pending_jobs(
    limit: int = 10,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch pending customer-message jobs for the orchestrator."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    rows = await _with_retry(
        "get_pending_jobs",
        """SELECT * FROM job_queue
           WHERE status = 'pending' AND is_customer_message = 1
           ORDER BY created_at ASC
           LIMIT ?""",
        (limit,),
        db_path,
        fetch=True,
    )

    return [dict(row) for row in (rows or [])]


async def get_pending_agent_jobs(
    limit: int = 10,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch pending agent-message jobs for the feedback loop."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    rows = await _with_retry(
        "get_pending_agent_jobs",
        """SELECT * FROM job_queue
           WHERE status = 'pending' AND is_customer_message = 0
           ORDER BY created_at ASC
           LIMIT ?""",
        (limit,),
        db_path,
        fetch=True,
    )

    return [dict(row) for row in (rows or [])]


async def claim_job(
    job_id: int,
    db_path: Path | None = None,
) -> bool:
    """Atomically claim a job: pending → processing.

    Returns True if the job was successfully claimed (was pending),
    False if another worker already claimed it.
    """
    if db_path is None:
        db_path = get_settings().db_path_absolute

    now = datetime.now(timezone.utc).isoformat()
    result = await _with_retry(
        "claim_job",
        """UPDATE job_queue
           SET status = 'processing', started_at = ?
           WHERE id = ? AND status = 'pending'""",
        (now, job_id),
        db_path,
    )
    # _with_retry returns lastrowid for non-fetch; we need rowsaffected
    # Check by re-reading the job
    rows = await _with_retry(
        "claim_job_verify",
        "SELECT status FROM job_queue WHERE id = ?",
        (job_id,),
        db_path,
        fetch=True,
    )
    if rows and rows[0]["status"] == "processing":
        return True
    return False


async def complete_job(
    job_id: int,
    result_data: dict | None = None,
    db_path: Path | None = None,
) -> None:
    """Mark a job as done with optional result metadata."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    now = datetime.now(timezone.utc).isoformat()
    await _with_retry(
        "complete_job",
        """UPDATE job_queue
           SET status = 'done', finished_at = ?, error = NULL
           WHERE id = ?""",
        (now, job_id),
        db_path,
    )


async def fail_job(
    job_id: int,
    error: str,
    db_path: Path | None = None,
) -> None:
    """Mark a job as failed with an error message."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    now = datetime.now(timezone.utc).isoformat()
    await _with_retry(
        "fail_job",
        """UPDATE job_queue
           SET status = 'failed', finished_at = ?, error = ?
           WHERE id = ?""",
        (now, error[:2000], job_id),
        db_path,
    )


async def requeue_stale_jobs(
    max_age_minutes: int = 10,
    db_path: Path | None = None,
) -> int:
    """Reclaim jobs stuck in 'processing' for too long.

    Returns the number of jobs reclaimed.
    Called at processor startup to recover from crashes.
    """
    if db_path is None:
        db_path = get_settings().db_path_absolute

    now = datetime.now(timezone.utc).isoformat()
    rows = await _with_retry(
        "requeue_stale_jobs_select",
        """SELECT id FROM job_queue
           WHERE status = 'processing'
             AND started_at < ?""",
        (now,),
        db_path,
        fetch=True,
    )
    count = 0
    for row in (rows or []):
        # Check age
        try:
            started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - started).total_seconds() / 60
            if age_min > max_age_minutes:
                await _with_retry(
                    "requeue_stale_job",
                    """UPDATE job_queue
                       SET status = 'pending', started_at = NULL,
                           retry_count = retry_count + 1
                       WHERE id = ?""",
                    (row["id"],),
                    db_path,
                )
                count += 1
        except (ValueError, TypeError):
            continue
    return count


async def requeue_failed_job(
    job_id: int,
    db_path: Path | None = None,
) -> None:
    """Requeue a failed job for retry (up to max retries)."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    await _with_retry(
        "requeue_failed_job",
        """UPDATE job_queue
           SET status = 'pending', started_at = NULL, finished_at = NULL,
               error = NULL, retry_count = retry_count + 1
           WHERE id = ? AND retry_count < 3""",
        (job_id,),
        db_path,
    )


async def get_job_stats(db_path: Path | None = None) -> dict:
    """Return job queue stats for monitoring."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    rows = await _with_retry(
        "get_job_stats",
        """SELECT status, COUNT(*) as cnt FROM job_queue GROUP BY status""",
        (),
        db_path,
        fetch=True,
    )
    stats = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    for row in (rows or []):
        stats[row["status"]] = row["cnt"]
    return stats


async def record_parsed_message(
    message_id: str,
    ticket_id: int,
    event_type: str,
    author_type: str,
    author_email: str | None,
    channel: str | None,
    customer_email: str | None,
    ticket_subject: str | None,
    message_text: str | None,
    intents: list[dict],
    is_customer_message: bool,
    created_at: str | None,
    db_path: Path | None = None,
) -> None:
    """Insert or replace a parsed message row for the dashboard."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    now = datetime.now(timezone.utc).isoformat()
    intent_names = json.dumps([i.get("name") for i in intents if isinstance(i, dict) and i.get("name")])

    await _with_retry(
        "record_parsed_message",
        """INSERT OR REPLACE INTO parsed_messages
           (message_id, ticket_id, event_type, author_type,
            author_email, channel, customer_email, ticket_subject,
            message_text, intents, is_customer_message, created_at, received_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (message_id, ticket_id, event_type, author_type,
         author_email, channel, customer_email, ticket_subject,
         message_text, intent_names, int(is_customer_message), created_at, now),
        db_path,
    )


async def get_parsed_messages(
    limit: int = 50,
    offset: int = 0,
    customer_only: bool = False,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch parsed messages for the dashboard, newest first."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    where = "WHERE is_customer_message = 1" if customer_only else ""
    sql = f"""SELECT * FROM parsed_messages {where}
             ORDER BY received_at DESC LIMIT ? OFFSET ?"""

    rows = await _with_retry(
        "get_parsed_messages",
        sql,
        (limit, offset),
        db_path,
        fetch=True,
    )

    return [dict(row) for row in (rows or [])]


async def record_ticket_result(
    ticket_id: int,
    message_id: str,
    job_id: int | None,
    priority: str,
    action: str,
    reason: str,
    notify_owner: bool,
    gorgias_priority_set: bool,
    note_posted: bool,
    draft_text: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Store the Hermes processing result for a ticket message."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    now = datetime.now(timezone.utc).isoformat()
    await _with_retry(
        "record_ticket_result",
        """INSERT OR REPLACE INTO ticket_results
           (ticket_id, message_id, job_id, priority, action, reason,
            notify_owner, gorgias_priority_set, note_posted, draft_text, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ticket_id, message_id, job_id, priority, action, reason,
         int(notify_owner), int(gorgias_priority_set), int(note_posted),
         draft_text, now),
        db_path,
    )


async def get_ticket_results(
    limit: int = 50,
    offset: int = 0,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch ticket processing results, newest first."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    rows = await _with_retry(
        "get_ticket_results",
        """SELECT * FROM ticket_results ORDER BY processed_at DESC LIMIT ? OFFSET ?""",
        (limit, offset),
        db_path,
        fetch=True,
    )
    return [dict(row) for row in (rows or [])]


async def get_dashboard_tickets(
    limit: int = 50,
    offset: int = 0,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch customer messages joined with AI processing results.

    Returns one row per customer message, with:
    - The original customer message (subject, text, email, intents, received_at)
    - The job status (pending/processing/done/failed)
    - The AI result (priority, action, reason, draft_text, note_posted, etc.)
    """
    if db_path is None:
        db_path = get_settings().db_path_absolute

    sql = """SELECT
        pm.message_id,
        pm.ticket_id,
        pm.customer_email,
        pm.ticket_subject,
        pm.message_text,
        pm.intents,
        pm.channel,
        pm.received_at,
        pm.created_at,
        j.status      AS job_status,
        j.id          AS job_id,
        j.started_at  AS job_started_at,
        j.finished_at AS job_finished_at,
        tr.priority,
        tr.action,
        tr.reason,
        tr.notify_owner,
        tr.gorgias_priority_set,
        tr.note_posted,
        tr.draft_text,
        tr.processed_at
    FROM parsed_messages pm
    LEFT JOIN job_queue j ON pm.message_id = j.message_id
    LEFT JOIN ticket_results tr ON pm.message_id = tr.message_id
    WHERE pm.is_customer_message = 1
    ORDER BY pm.received_at DESC
    LIMIT ? OFFSET ?"""

    rows = await _with_retry(
        "get_dashboard_tickets",
        sql,
        (limit, offset),
        db_path,
        fetch=True,
    )
    return [dict(row) for row in (rows or [])]


async def get_result_stats(db_path: Path | None = None) -> dict:
    """Return aggregate stats for the dashboard."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    # Job stats
    job_rows = await _with_retry(
        "get_result_stats_jobs",
        "SELECT status, COUNT(*) as cnt FROM job_queue WHERE is_customer_message=1 GROUP BY status",
        (),
        db_path,
        fetch=True,
    )
    job_stats = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    for row in (job_rows or []):
        job_stats[row["status"]] = row["cnt"]

    # Result stats
    result_rows = await _with_retry(
        "get_result_stats_results",
        """SELECT
             COUNT(*) as total,
             SUM(CASE WHEN action = 'drafted' THEN 1 ELSE 0 END) as drafted,
             SUM(CASE WHEN action = 'escalated' THEN 1 ELSE 0 END) as escalated,
             SUM(CASE WHEN action = 'sensitive_draft' THEN 1 ELSE 0 END) as sensitive_draft,
             SUM(CASE WHEN action = 'no_kb_match' THEN 1 ELSE 0 END) as no_kb_match,
             SUM(CASE WHEN priority = 'critical' THEN 1 ELSE 0 END) as critical,
             SUM(CASE WHEN priority = 'high' THEN 1 ELSE 0 END) as high,
             SUM(CASE WHEN priority = 'normal' THEN 1 ELSE 0 END) as normal,
             SUM(CASE WHEN priority = 'low' THEN 1 ELSE 0 END) as low
           FROM ticket_results""",
        (),
        db_path,
        fetch=True,
    )
    if result_rows:
        row = result_rows[0]
        result_stats = {
            "total": row["total"] or 0,
            "drafted": row["drafted"] or 0,
            "escalated": row["escalated"] or 0,
            "sensitive_draft": row["sensitive_draft"] or 0,
            "no_kb_match": row["no_kb_match"] or 0,
            "critical": row["critical"] or 0,
            "high": row["high"] or 0,
            "normal": row["normal"] or 0,
            "low": row["low"] or 0,
        }
    else:
        result_stats = {"total": 0, "drafted": 0, "escalated": 0, "sensitive_draft": 0,
                        "no_kb_match": 0, "critical": 0, "high": 0, "normal": 0, "low": 0}

    return {**job_stats, **result_stats}


async def get_setting(key: str, default: str = "", db_path: Path | None = None) -> str:
    """Get a setting value from the app_settings table."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    rows = await _with_retry(
        "get_setting",
        "SELECT value FROM app_settings WHERE key = ?",
        (key,),
        db_path,
        fetch=True,
    )
    if rows:
        return rows[0]["value"]
    return default


async def set_setting(key: str, value: str, db_path: Path | None = None) -> None:
    """Set a setting value in the app_settings table."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    now = datetime.now(timezone.utc).isoformat()
    await _with_retry(
        "set_setting",
        """INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)""",
        (key, value, now),
        db_path,
    )


async def get_all_settings(db_path: Path | None = None) -> dict:
    """Get all settings as a dict."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    rows = await _with_retry(
        "get_all_settings",
        "SELECT key, value FROM app_settings",
        (),
        db_path,
        fetch=True,
    )
    return {row["key"]: row["value"] for row in (rows or [])}


async def get_parsed_stats(db_path: Path | None = None) -> dict:
    """Return aggregate stats for the dashboard."""
    if db_path is None:
        db_path = get_settings().db_path_absolute

    rows = await _with_retry(
        "get_parsed_stats",
        """SELECT
             COUNT(*) as total,
             SUM(is_customer_message) as customer_count,
             SUM(CASE WHEN is_customer_message = 0 THEN 1 ELSE 0 END) as agent_count
           FROM parsed_messages""",
        (),
        db_path,
        fetch=True,
    )

    if rows:
        row = rows[0]
        return {
            "total": row["total"] or 0,
            "customer": row["customer_count"] or 0,
            "agent": row["agent_count"] or 0,
        }
    return {"total": 0, "customer": 0, "agent": 0}