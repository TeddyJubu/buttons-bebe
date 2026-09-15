"""SQLite database layer for webhook idempotency and job queue.

Uses WAL journal mode for better concurrent-read performance and
retries on ``database is locked`` to handle write contention when
Gorgias sends bursts of webhook deliveries.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .config import get_settings
from .db import Database
from .logging_utils import get_logger

logger = get_logger(__name__)

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
    ticket_status     TEXT,
    ticket_assignee   TEXT,
    ticket_tags       TEXT,             -- JSON array of bounded tag names
    ticket_priority   TEXT,             -- observed Gorgias priority; AI priority lives in ticket_results
    ticket_spam       INTEGER NOT NULL DEFAULT 0,  -- observed Gorgias spam flag; badges only, never drops
    ticket_trashed    INTEGER NOT NULL DEFAULT 0,  -- observed Gorgias trashed flag; badges only, never drops
    ticket_snoozed    INTEGER NOT NULL DEFAULT 0,  -- observed Gorgias snooze flag; badges only, never drops
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
    action           TEXT,              -- drafted | sensitive_draft | escalated | no_kb_match | no_draft_needed
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
CREATE INDEX IF NOT EXISTS idx_jobs_message ON job_queue(message_id);
CREATE INDEX IF NOT EXISTS idx_jobs_customer ON job_queue(is_customer_message);
CREATE INDEX IF NOT EXISTS idx_parsed_received ON parsed_messages(received_at);
CREATE INDEX IF NOT EXISTS idx_parsed_customer ON parsed_messages(is_customer_message);
CREATE INDEX IF NOT EXISTS idx_results_ticket ON ticket_results(ticket_id);
CREATE INDEX IF NOT EXISTS idx_results_message ON ticket_results(message_id);

-- One durable owner-alert attempt per processing job. A claimed attempt with
-- no recorded success is uncertain and must never be automatically resent.
CREATE TABLE IF NOT EXISTS owner_alert_attempts (
    job_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('attempting', 'accepted', 'uncertain')),
    attempted_at TEXT NOT NULL,
    finished_at TEXT
);

-- Key-value settings store (dashboard toggles, etc.)
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


async def init_db(db_path: Path | None = None) -> None:
    """Create the database, tables, and enable WAL journal mode."""
    db_path = Database(db_path).path

    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")  # 5 s SQLite-level wait
        await conn.executescript(_SCHEMA)
        # Additive migration for pre-existing databases: older webhook DBs
        # lack ticket_status (account-side template started sending it later).
        # Runs at every startup; a no-op once the column exists.
        cursor = await conn.execute("PRAGMA table_info(parsed_messages)")
        columns = [row[1] for row in await cursor.fetchall()]
        for column in ("ticket_status", "ticket_assignee", "ticket_tags", "ticket_priority"):
            if column not in columns:
                await conn.execute(f"ALTER TABLE parsed_messages ADD COLUMN {column} TEXT")
        for column in ("ticket_spam", "ticket_trashed", "ticket_snoozed"):
            if column not in columns:
                await conn.execute(f"ALTER TABLE parsed_messages ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        await conn.commit()

    logger.info("Database initialized (WAL mode) at %s", db_path)


async def _with_retry(
    operation: str,
    sql: str,
    params: tuple,
    db_path: Path,
    *,
    fetch: bool = False,
    return_rowcount: bool = False,
) -> Any | None:  # noqa: F401 -- Any imported via aiosqlite.Row
    """Compatibility seam for callers that used the old module helper."""
    return await Database(db_path).execute(
        sql,
        params,
        operation=operation,
        fetch=fetch,
        return_rowcount=return_rowcount,
    )


async def is_duplicate(message_id: str, db_path: Path | None = None) -> bool:
    """Check if we've already received this message_id."""
    db = Database(db_path)
    rows = await db.fetch(
        "SELECT 1 FROM webhook_events WHERE message_id = ?",
        (message_id,),
        operation="is_duplicate",
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
) -> bool:
    """Persist a webhook event for dedup and audit.

    Returns ``True`` only for the request that inserted the idempotency row.
    Concurrent duplicate deliveries therefore have one unambiguous winner.
    """
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()

    inserted = await db.execute(
        """INSERT OR IGNORE INTO webhook_events
           (message_id, tenant_id, ticket_id, event_type,
            author_type, raw_payload, received_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (message_id, tenant_id, ticket_id, event_type,
         author_type, raw_payload, now),
        operation="record_event",
        return_rowcount=True,
    )
    return inserted == 1


async def ingest_event(
    event: dict,
    raw_payload: str,
    db_path: Path | None = None,
) -> int | None:
    """Atomically persist accepted intake and queue work.

    Return the new job ID, or None for an already committed event. The event
    primary key is the intake uniqueness gate; both concurrent duplicates and
    retries after an interrupted commit use the same gate. Historical partial
    events are deliberately not replayed automatically.
    """
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()
    message_id = str(event["message_id"])
    customer = bool(event.get("is_customer_message", False))
    payload = {key: event.get(key) for key in (
        "tenant_id", "ticket_id", "message_id", "event_type", "author_type",
        "author_email", "channel", "customer_email", "ticket_subject",
        "message_text", "created_at",
    )}
    payload["intents"] = event.get("intents", [])
    intent_names = json.dumps([i.get("name") for i in payload["intents"]
                              if isinstance(i, dict) and i.get("name")])

    async def persist(conn: aiosqlite.Connection) -> int | None:
        async with conn.execute(
            """INSERT INTO webhook_events
               (message_id, tenant_id, ticket_id, event_type, author_type,
                raw_payload, received_at) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO NOTHING""",
            (message_id, event["tenant_id"], event["ticket_id"], event["event_type"],
             event["author_type"], raw_payload, now),
        ) as cursor:
            if cursor.rowcount == 0:
                return None
        await conn.execute(
            """INSERT INTO parsed_messages
               (message_id, ticket_id, event_type, author_type, author_email,
                channel, customer_email, ticket_subject, ticket_status, ticket_assignee, ticket_tags, ticket_priority,
                ticket_spam, ticket_trashed, ticket_snoozed, message_text, intents,
                is_customer_message, created_at, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, event["ticket_id"], event["event_type"], event["author_type"],
             event.get("author_email"), event.get("channel"), event.get("customer_email"),
             event.get("ticket_subject"), event.get("ticket_status"), event.get("ticket_assignee"),
             json.dumps(event.get("ticket_tags") or []), event.get("ticket_priority"),
             int(event.get("ticket_spam") or 0), int(event.get("ticket_trashed") or 0), int(event.get("ticket_snoozed") or 0),
             event.get("message_text"), intent_names,
             int(customer), event.get("created_at"), now),
        )
        # Preserve the legacy enqueue helper's dedupe if an operator previously
        # created a job independently of webhook_events. Never enqueue twice.
        await conn.execute(
            """INSERT INTO job_queue
               (tenant_id, ticket_id, message_id, event_type, author_type,
                is_customer_message, status, payload, created_at)
               SELECT ?, ?, ?, ?, ?, ?, 'pending', ?, ?
               WHERE NOT EXISTS (SELECT 1 FROM job_queue WHERE message_id = ?)""",
            (event["tenant_id"], event["ticket_id"], message_id, event["event_type"],
             event["author_type"], int(customer), json.dumps(payload), now, message_id),
        )
        async with conn.execute(
            "SELECT id FROM job_queue WHERE message_id = ? ORDER BY id LIMIT 1",
            (message_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Intake did not create queue work")
        return int(row["id"])

    return await db.transaction(persist, operation="ingest_event")


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
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()

    job_id = await db.execute(
        """INSERT INTO job_queue
           (tenant_id, ticket_id, message_id, event_type,
            author_type, is_customer_message, status, payload, created_at)
           SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
           WHERE NOT EXISTS (
               SELECT 1 FROM job_queue WHERE message_id = ?
           )""",
        (tenant_id, ticket_id, message_id, event_type,
         author_type, int(is_customer_message), "pending", json.dumps(payload), now,
         message_id),
        operation="enqueue_job",
    )
    if job_id:
        return int(job_id)

    rows = await db.fetch(
        "SELECT id FROM job_queue WHERE message_id = ? ORDER BY id LIMIT 1",
        (message_id,),
        operation="enqueue_job_existing",
    )
    return int(rows[0]["id"]) if rows else 0


async def get_intake_integrity_stats(db_path: Path | None = None) -> list[dict]:
    """Count historical partial intake by event category without exposing PII.

    This is diagnostic only: never automatically replay old events or infer
    that missing work should be sent. An operator must establish whether each
    historical category intentionally skipped processing before repair.
    """
    rows = await Database(db_path).fetch(
        """SELECT e.event_type, e.author_type, COUNT(*) AS events,
                  SUM(NOT EXISTS (SELECT 1 FROM parsed_messages p
                                  WHERE p.message_id=e.message_id)) AS missing_parsed,
                  SUM(NOT EXISTS (SELECT 1 FROM job_queue j
                                  WHERE j.message_id=e.message_id)) AS missing_jobs
           FROM webhook_events e
           WHERE NOT EXISTS (SELECT 1 FROM parsed_messages p WHERE p.message_id=e.message_id)
              OR NOT EXISTS (SELECT 1 FROM job_queue j WHERE j.message_id=e.message_id)
           GROUP BY e.event_type, e.author_type""",
        operation="intake_integrity_stats",
    )
    return [dict(row) for row in rows]


async def get_pending_jobs(
    limit: int = 10,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch pending customer-message jobs for the orchestrator."""
    db = Database(db_path)
    rows = await db.fetch(
        """SELECT * FROM job_queue
           WHERE status = 'pending' AND is_customer_message = 1
           ORDER BY created_at ASC
           LIMIT ?""",
        (limit,),
        operation="get_pending_jobs",
    )

    return [dict(row) for row in (rows or [])]


async def get_pending_agent_jobs(
    limit: int = 10,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch pending agent-message jobs for the feedback loop."""
    db = Database(db_path)
    rows = await db.fetch(
        """SELECT * FROM job_queue
           WHERE status = 'pending' AND is_customer_message = 0
           ORDER BY created_at ASC
           LIMIT ?""",
        (limit,),
        operation="get_pending_agent_jobs",
    )

    return [dict(row) for row in (rows or [])]


async def get_next_pending_job(db_path: Path | None = None) -> dict | None:
    """Fetch one pending job, preferring customer messages over agent work."""
    db = Database(db_path)
    rows = await db.fetch(
        """SELECT * FROM job_queue
           WHERE status = 'pending'
           ORDER BY is_customer_message DESC, created_at ASC
           LIMIT 1""",
        operation="get_next_pending_job",
    )
    return dict(rows[0]) if rows else None


async def get_pending_job_window(
    limit: int = 25,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch a bounded pending-job window for processor-side priority selection.

    Ordering matches get_next_pending_job: customer messages first, then oldest
    first. The processor uses this window only to pick which already-pending job
    to claim next; claiming is still the atomic claim_job transition, so races
    with another worker remain impossible.
    """
    if limit <= 0:
        raise ValueError("pending job window limit must be positive")
    db = Database(db_path)
    rows = await db.fetch(
        """SELECT * FROM job_queue
           WHERE status = 'pending'
           ORDER BY is_customer_message DESC, created_at ASC
           LIMIT ?""",
        (limit,),
        operation="get_pending_job_window",
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
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()
    affected = await db.execute(
        """UPDATE job_queue
           SET status = 'processing', started_at = ?
           WHERE id = ? AND status = 'pending'""",
        (now, job_id),
        operation="claim_job",
        return_rowcount=True,
    )
    return affected == 1


async def complete_job(
    job_id: int,
    result_data: dict | None = None,
    db_path: Path | None = None,
    *,
    require_result: bool = False,
) -> bool:
    """Complete a claim, optionally requiring this exact job's durable result."""
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()
    affected = await db.execute(
        """UPDATE job_queue
           SET status = 'done', finished_at = ?, error = NULL
           WHERE id = ? AND status = 'processing'
             AND (? = 0 OR EXISTS (
                 SELECT 1 FROM ticket_results r WHERE r.job_id = job_queue.id
                 AND r.ticket_id = job_queue.ticket_id
                 AND r.message_id = job_queue.message_id
             ))""",
        (now, job_id, int(require_result)),
        operation="complete_job",
        return_rowcount=True,
    )
    return affected == 1


async def fail_job(
    job_id: int,
    error: str,
    db_path: Path | None = None,
) -> None:
    """Mark a job as failed with an error message."""
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE job_queue
           SET status = 'failed', finished_at = ?, error = ?
           WHERE id = ?""",
        (now, error[:2000], job_id),
        operation="fail_job",
    )


async def requeue_stale_jobs(
    max_age_minutes: int = 10,
    db_path: Path | None = None,
    *,
    max_retries: int = 3,
) -> int:
    """Reclaim up to 100 abandoned claims per singleton-loop sweep.

    Call only between jobs while holding the processor singleton lock. This
    is not a multi-worker lease implementation. SQLite compares timestamps
    (including legacy Z offsets) and changes status in one atomic statement.
    Missing/invalid claim timestamps are also abandoned, rather than silently
    remaining processing forever. Exhausted claims become failed with an
    operator-visible reason. The return count includes both retry and failure.
    """
    if max_age_minutes < 0 or max_retries < 0:
        raise ValueError("Recovery age and retry limit must be nonnegative")
    db = Database(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    affected = await db.execute(
        """UPDATE job_queue
           SET status = CASE WHEN retry_count < ? THEN 'pending' ELSE 'failed' END,
               started_at = NULL,
               finished_at = CASE WHEN retry_count < ? THEN NULL ELSE ? END,
               error = CASE WHEN retry_count < ? THEN NULL
                       ELSE 'Abandoned job exhausted recovery retries; operator review required' END,
               retry_count = CASE WHEN retry_count < ? THEN retry_count + 1 ELSE retry_count END
           WHERE id IN (
               SELECT id FROM job_queue WHERE status = 'processing'
                 AND (julianday(started_at) IS NULL OR
                      julianday(started_at) < julianday(?))
               ORDER BY id LIMIT 100
           ) AND status = 'processing'""",
        (max_retries, max_retries, datetime.now(timezone.utc).isoformat(),
         max_retries, max_retries, cutoff),
        operation="requeue_stale_jobs",
        return_rowcount=True,
    )
    if affected:
        logger.warning("Resolved %d abandoned claims (retry limit %d); exhausted claims are failed",
                       affected, max_retries)
    return int(affected or 0)


async def requeue_failed_job(
    job_id: int,
    db_path: Path | None = None,
    *,
    max_retries: int = 3,
) -> None:
    """Requeue a failed job for retry (up to max retries)."""
    db = Database(db_path)
    await db.execute(
        """UPDATE job_queue
           SET status = 'pending', started_at = NULL, finished_at = NULL,
               error = NULL, retry_count = retry_count + 1
           WHERE id = ? AND status = 'failed' AND retry_count < ?""",
        (job_id, max(0, max_retries)),
        operation="requeue_failed_job",
    )


async def get_job_stats(db_path: Path | None = None) -> dict:
    """Return job queue stats for monitoring."""
    db = Database(db_path)
    rows = await db.fetch(
        """SELECT status, COUNT(*) as cnt FROM job_queue GROUP BY status""",
        (),
        operation="get_job_stats",
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
    ticket_status: str | None = None,
    ticket_assignee: str | None = None,
    ticket_tags: list[str] | None = None,
    ticket_priority: str | None = None,
    ticket_spam: int = 0,
    ticket_trashed: int = 0,
    ticket_snoozed: int = 0,
    db_path: Path | None = None,
) -> None:
    """Insert or replace a parsed message row for the dashboard."""
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()
    intent_names = json.dumps([i.get("name") for i in intents if isinstance(i, dict) and i.get("name")])

    await db.execute(
            """INSERT OR REPLACE INTO parsed_messages
           (message_id, ticket_id, event_type, author_type,
            author_email, channel, customer_email, ticket_subject, ticket_status, ticket_assignee, ticket_tags, ticket_priority,
            ticket_spam, ticket_trashed, ticket_snoozed,
            message_text, intents, is_customer_message, created_at, received_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (message_id, ticket_id, event_type, author_type,
         author_email, channel, customer_email, ticket_subject, ticket_status, ticket_assignee,
         json.dumps(ticket_tags or []),
         ticket_priority,
         int(ticket_spam or 0), int(ticket_trashed or 0), int(ticket_snoozed or 0),
          message_text, intent_names, int(is_customer_message), created_at, now),
        operation="record_parsed_message",
    )


async def get_parsed_messages(
    limit: int = 50,
    offset: int = 0,
    customer_only: bool = False,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch parsed messages for the dashboard, newest first."""
    db = Database(db_path)
    where = "WHERE is_customer_message = 1" if customer_only else ""
    sql = f"""SELECT * FROM parsed_messages {where}
             ORDER BY received_at DESC LIMIT ? OFFSET ?"""

    rows = await db.fetch(
        sql,
        (limit, offset),
        operation="get_parsed_messages",
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
    """Store the first committed result; replay cannot replace a reviewed draft."""
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO ticket_results
           (ticket_id, message_id, job_id, priority, action, reason,
            notify_owner, gorgias_priority_set, note_posted, draft_text, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(ticket_id, message_id) DO NOTHING""",
        (ticket_id, message_id, job_id, priority, action, reason,
         int(notify_owner), int(gorgias_priority_set), int(note_posted),
         draft_text, now),
        operation="record_ticket_result",
    )


async def get_job_result(job_id: int, db_path: Path | None = None) -> dict | None:
    """Return only a durable result matching the claimed job's full identity."""
    rows = await Database(db_path).fetch(
        """SELECT r.* FROM ticket_results r JOIN job_queue j
           ON r.job_id=j.id AND r.ticket_id=j.ticket_id AND r.message_id=j.message_id
           WHERE j.id=?""", (job_id,), operation="get_job_result",
    )
    return dict(rows[0]) if rows else None


async def claim_owner_alert(job_id: int, db_path: Path | None = None) -> bool:
    """Persist one attempt before transport; interrupted attempts are uncertain."""
    affected = await Database(db_path).execute(
        """INSERT INTO owner_alert_attempts(job_id, status, attempted_at)
           SELECT ?, 'attempting', ? WHERE EXISTS (
               SELECT 1 FROM ticket_results r JOIN job_queue j ON r.job_id=j.id
               AND r.ticket_id=j.ticket_id AND r.message_id=j.message_id
               WHERE j.id=? AND r.notify_owner=1)
           ON CONFLICT(job_id) DO NOTHING""",
        (job_id, datetime.now(timezone.utc).isoformat(), job_id),
        operation="claim_owner_alert", return_rowcount=True,
    )
    return affected == 1


async def finish_owner_alert(job_id: int, accepted: bool, db_path: Path | None = None) -> None:
    await Database(db_path).execute(
        """UPDATE owner_alert_attempts SET status=?, finished_at=?
           WHERE job_id=? AND status='attempting'""",
        ("accepted" if accepted else "uncertain", datetime.now(timezone.utc).isoformat(), job_id),
        operation="finish_owner_alert",
    )


async def get_ticket_results(
    limit: int = 50,
    offset: int = 0,
    db_path: Path | None = None,
) -> list[dict]:
    """Fetch ticket processing results, newest first."""
    db = Database(db_path)
    rows = await db.fetch(
        """SELECT * FROM ticket_results ORDER BY processed_at DESC LIMIT ? OFFSET ?""",
        (limit, offset),
        operation="get_ticket_results",
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
    db = Database(db_path)
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
        tr.processed_at,
        CASE WHEN oa.status = 'attempting' THEN 'uncertain'
             ELSE oa.status END AS owner_alert_status
    FROM parsed_messages pm
    LEFT JOIN job_queue j ON pm.message_id = j.message_id
    LEFT JOIN ticket_results tr ON pm.message_id = tr.message_id
    LEFT JOIN owner_alert_attempts oa ON oa.job_id = j.id
    WHERE pm.is_customer_message = 1
    ORDER BY pm.received_at DESC
    LIMIT ? OFFSET ?"""

    rows = await db.fetch(
        sql,
        (limit, offset),
        operation="get_dashboard_tickets",
    )
    return [dict(row) for row in (rows or [])]


async def dashboard_ticket_exists(
    ticket_id: int,
    db_path: Path | None = None,
) -> bool:
    """Return whether a customer ticket is present in the review console."""
    db = Database(db_path)
    rows = await db.fetch(
        """SELECT 1 FROM parsed_messages
           WHERE ticket_id = ? AND is_customer_message = 1 LIMIT 1""",
        (ticket_id,),
        operation="dashboard_ticket_exists",
    )
    return bool(rows)


async def get_result_stats(db_path: Path | None = None) -> dict:
    """Return aggregate stats for the dashboard."""
    db = Database(db_path)

    # Job stats
    job_rows = await db.fetch(
        "SELECT status, COUNT(*) as cnt FROM job_queue WHERE is_customer_message=1 GROUP BY status",
        (),
        operation="get_result_stats_jobs",
    )
    job_stats = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    for row in (job_rows or []):
        job_stats[row["status"]] = row["cnt"]

    # Result stats
    result_rows = await db.fetch(
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
        operation="get_result_stats_results",
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

    alert_rows = await db.fetch(
        "SELECT COUNT(*) AS count FROM owner_alert_attempts WHERE status != 'accepted'",
        operation="owner_alert_attention_count",
    )
    return {**job_stats, **result_stats,
            "owner_alerts_need_attention": alert_rows[0]["count"]}


async def get_setting(key: str, default: str = "", db_path: Path | None = None) -> str:
    """Get a setting value from the app_settings table."""
    db = Database(db_path)
    rows = await db.fetch(
        "SELECT value FROM app_settings WHERE key = ?",
        (key,),
        operation="get_setting",
    )
    if rows:
        return rows[0]["value"]
    return default


async def set_setting(key: str, value: str, db_path: Path | None = None) -> None:
    """Set a setting value in the app_settings table."""
    db = Database(db_path)
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)""",
        (key, value, now),
        operation="set_setting",
    )


async def get_all_settings(db_path: Path | None = None) -> dict:
    """Get all settings as a dict."""
    db = Database(db_path)
    rows = await db.fetch(
        "SELECT key, value FROM app_settings",
        (),
        operation="get_all_settings",
    )
    return {row["key"]: row["value"] for row in (rows or [])}


async def get_parsed_stats(db_path: Path | None = None) -> dict:
    """Return aggregate stats for the dashboard."""
    db = Database(db_path)
    rows = await db.fetch(
        """SELECT
             COUNT(*) as total,
             SUM(is_customer_message) as customer_count,
             SUM(CASE WHEN is_customer_message = 0 THEN 1 ELSE 0 END) as agent_count
           FROM parsed_messages""",
        (),
        operation="get_parsed_stats",
    )

    if rows:
        row = rows[0]
        return {
            "total": row["total"] or 0,
            "customer": row["customer_count"] or 0,
            "agent": row["agent_count"] or 0,
        }
    return {"total": 0, "customer": 0, "agent": 0}
