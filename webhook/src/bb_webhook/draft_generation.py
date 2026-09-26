"""Durable, local-only draft generation. No model or provider writes live here.

Generation attempts and human-send reservations serialize on the same SQLite
writer lock. Only a failed candidate may be replaced; a successful candidate or
an initiated human action always wins. Queue transport retries remain separate
from the two delayed retries for transient model failures.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from .db import Database
from .send_intents import ActionConflict, valid_operation

TRANSIENT_ERRORS = frozenset({"timeout", "process_exit", "runtime_error"})
STATES = frozenset({"ready", "needs_review", "no_reply", "failed", "retry_wait", "superseded"})
RESULT_COLUMNS = {
    "generation_state": "TEXT", "generation_error": "TEXT",
    "attempt_count": "INTEGER NOT NULL DEFAULT 0", "next_retry_at": "TEXT",
    "review_required": "INTEGER NOT NULL DEFAULT 0", "staff_next_step": "TEXT",
    "missing_facts": "TEXT", "generation_attempt_id": "INTEGER",
}
QUEUE_COLUMNS = {
    "next_attempt_at": "TEXT", "generation_cycle_attempts": "INTEGER NOT NULL DEFAULT 0",
    "generation_expected_revision": "TEXT",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS draft_generation_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
 ticket_id INTEGER NOT NULL, message_id TEXT NOT NULL,
 expected_revision TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 outcome TEXT NOT NULL DEFAULT 'running', error_code TEXT,
 duration_ms INTEGER, result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_draft_attempt_job ON draft_generation_attempts(job_id,id);
CREATE TABLE IF NOT EXISTS draft_retry_requests (
 operation_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, ticket_id INTEGER NOT NULL,
 message_id TEXT NOT NULL, expected_revision TEXT NOT NULL,
 job_id INTEGER NOT NULL, created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def revision(text):
    return hashlib.sha256(str(text or "").encode()).hexdigest()


def result_state(row):
    """Compatibility is reason-based, never a guess from customer-facing prose."""
    if not row:
        return None
    row = dict(row)
    if row.get("generation_state") in STATES:
        return row["generation_state"]
    reason = str(row.get("reason") or "")
    if reason.startswith(("Hermes invocation failed", "Hermes output failed run-token",
                          "Hermes draft rejected by safety cleaning", "Hermes emitted no valid",
                          "Hermes produced empty output", "Hermes emitted malformed",
                          "Hermes emitted multiple", "Hermes output exceeded",
                          "Hermes verdict failed")):
        return "failed"
    if row.get("action") == "no_draft_needed":
        return "no_reply"
    if not row.get("processed_at") and not row.get("action"):
        return None
    return "ready" if row.get("draft_text") else "needs_review"


def public_result(row):
    row = dict(row)
    row["draft_revision"] = revision(row.get("draft_text"))
    row["generation_state"] = result_state(row)
    if row["generation_state"] in {"failed", "retry_wait", "superseded"}:
        row["draft_text"] = ""
    raw = row.get("missing_facts")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    row["missing_facts"] = raw if isinstance(raw, list) else []
    row["review_required"] = bool(row.get("review_required")) or row["generation_state"] == "needs_review"
    return row


async def migrate(conn):
    for table, fields in (("ticket_results", RESULT_COLUMNS), ("job_queue", QUEUE_COLUMNS)):
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        existing = {r[1] for r in await cursor.fetchall()}
        for name, declaration in fields.items():
            if name not in existing:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    await conn.executescript(SCHEMA)


async def one(conn, sql, params=()):
    async with conn.execute(sql, params) as cursor:
        row = await cursor.fetchone()
        return dict(row) if row else None


async def conflict(conn, ticket_id, message_id):
    latest = await one(conn, """SELECT message_id FROM parsed_messages
        WHERE ticket_id=? AND is_customer_message=1
        ORDER BY COALESCE(NULLIF(created_at,''),received_at) DESC,received_at DESC,message_id DESC LIMIT 1""", (ticket_id,))
    if not latest or latest["message_id"] != message_id:
        return "new_customer_message_refresh_ticket"
    exists = await one(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='console_action_intents'")
    if exists:
        action = await one(conn, """SELECT operation_id FROM console_action_intents
            WHERE ticket_id=? AND (state IN ('pending','uncertain') OR
                 (source_message_id=? AND state IN ('sent','recorded'))) LIMIT 1""", (ticket_id, message_id))
        if action:
            return "human_action_already_initiated"
    return None


async def begin_attempt(job_id, db_path=None, *, priority_context=None):
    async def transaction(conn):
        job = await one(conn, "SELECT * FROM job_queue WHERE id=?", (job_id,))
        if not job or job["status"] != "processing":
            return None
        row = await one(conn, "SELECT * FROM ticket_results WHERE ticket_id=? AND message_id=?",
                        (job["ticket_id"], job["message_id"]))
        problem = await conflict(conn, job["ticket_id"], job["message_id"])
        expected = revision(row.get("draft_text") if row else "")
        if row and result_state(row) not in {"failed", "retry_wait"}:
            return None  # The caller completes a previously committed success.
        if job["generation_expected_revision"] and job["generation_expected_revision"] != expected:
            problem = "draft_changed_refresh_ticket"
        if problem:
            await conn.execute("UPDATE job_queue SET status='skipped',finished_at=?,error=? WHERE id=?",
                               (now(), problem, job_id))
            return None
        active = await one(conn, "SELECT id FROM draft_generation_attempts WHERE job_id=? AND outcome='running'", (job_id,))
        if active:
            return None  # Recovery must finish the abandoned attempt first.
        if job['generation_cycle_attempts'] >= 3 or (row and
                result_state(row) == 'failed' and job['generation_cycle_attempts'] > 0):
            await conn.execute("UPDATE job_queue SET status='done',finished_at=? WHERE id=?", (now(), job_id))
            return None  # Transport/restart recovery must not restart a terminal cycle.
        # Preserve the newest request's business urgency across process death.
        # This context is produced locally by the deterministic classifier.
        context = priority_context or {}
        priority = context.get('priority', 'normal')
        if priority not in {'low', 'normal', 'high', 'critical'}:
            priority = 'normal'
        seed = dict(priority=priority, notify_owner=bool(context.get('notify_owner')),
                    reason=str(context.get('reason') or '')[:500])
        cursor = await conn.execute("""INSERT INTO draft_generation_attempts
            (job_id,ticket_id,message_id,expected_revision,started_at,result_json) VALUES (?,?,?,?,?,?)""",
            (job_id, job["ticket_id"], job["message_id"], expected, now(),
             json.dumps({'priority_context': seed})))
        attempt_id = cursor.lastrowid
        await conn.execute("""UPDATE job_queue SET generation_cycle_attempts=generation_cycle_attempts+1,
            generation_expected_revision=?,next_attempt_at=NULL WHERE id=?""", (expected, job_id))
        return attempt_id
    return await Database(db_path).transaction(transaction, operation="begin_draft_attempt")


async def finish_attempt(payload, db_path=None):
    """Atomically publish a candidate and schedule a failure's delayed retry."""
    async def transaction(conn):
        attempt = await one(conn, "SELECT * FROM draft_generation_attempts WHERE id=?",
                            (payload["generation_attempt_id"],))
        identity = (payload["job_id"], payload["ticket_id"], str(payload["message_id"]))
        if not attempt or (attempt["job_id"], attempt["ticket_id"], attempt["message_id"]) != identity:
            raise ActionConflict("generation_attempt_mismatch")
        if attempt["outcome"] != "running":
            return attempt["outcome"]  # Durable HTTP acknowledgement replay.
        job = await one(conn, "SELECT * FROM job_queue WHERE id=?", (attempt["job_id"],))
        row = await one(conn, "SELECT * FROM ticket_results WHERE ticket_id=? AND message_id=?", identity[1:])
        problem = await conflict(conn, identity[1], identity[2])
        if job["status"] != "processing" or revision(row.get("draft_text") if row else "") != attempt["expected_revision"]:
            problem = "draft_changed_refresh_ticket"
        if row and result_state(row) not in {"failed", "retry_wait"}:
            problem = "successful_draft_already_exists"
        ended = now()
        duration = max(0, int((datetime.fromisoformat(ended) - datetime.fromisoformat(attempt["started_at"])).total_seconds() * 1000))
        if problem:
            await conn.execute("""UPDATE draft_generation_attempts SET outcome='superseded',
                error_code=?,finished_at=?,duration_ms=? WHERE id=?""", (problem, ended, duration, attempt["id"]))
            await conn.execute("UPDATE job_queue SET status='skipped',finished_at=?,error=? WHERE id=?",
                               (ended, problem, job["id"]))
            return "superseded"
        state = payload.get("generation_state") or ("ready" if payload.get("draft_text") else "needs_review")
        if state not in {"ready", "needs_review", "no_reply", "failed"}:
            raise ActionConflict("invalid_generation_state", 400)
        if state == "ready" and not str(payload.get("draft_text") or "").strip():
            raise ActionConflict("ready_draft_required", 400)
        retry_at = None
        error = payload.get("generation_error")
        if state == "failed" and error in TRANSIENT_ERRORS and job["generation_cycle_attempts"] < 3:
            delay = (30, 120)[job["generation_cycle_attempts"] - 1]
            retry_at = (datetime.fromisoformat(ended) + timedelta(seconds=delay)).isoformat()
            state = "retry_wait"
        draft = "" if state in {"failed", "retry_wait", "no_reply"} else str(payload.get("draft_text") or "")
        counted = await one(conn, "SELECT count(*) AS n FROM draft_generation_attempts WHERE job_id=?", (job["id"],))
        values = dict(payload, generation_state=state, draft_text=draft, next_retry_at=retry_at,
                      attempt_count=counted["n"], processed_at=ended)
        values["missing_facts"] = json.dumps(payload.get("missing_facts") or [])
        values["review_required"] = int(bool(payload.get("review_required")) or state == "needs_review")
        values["notify_owner"] = int(bool(payload.get("notify_owner")))
        values["gorgias_priority_set"] = values["note_posted"] = 0
        fields = ["ticket_id", "message_id", "job_id", "priority", "action", "reason", "notify_owner",
                  "gorgias_priority_set", "note_posted", "draft_text", "processed_at", *RESULT_COLUMNS]
        if row:
            # Retain legacy failed text and all metadata before replacing it.
            await conn.execute("UPDATE draft_generation_attempts SET result_json=? WHERE id=?",
                               (json.dumps({"previous": row, "result": values}), attempt["id"]))
            await conn.execute("UPDATE ticket_results SET " + ",".join(f"{f}=?" for f in fields) + " WHERE id=?",
                               (*[values.get(f) for f in fields], row["id"]))
        else:
            await conn.execute("INSERT INTO ticket_results (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")",
                               tuple(values.get(f) for f in fields))
            await conn.execute("UPDATE draft_generation_attempts SET result_json=? WHERE id=?",
                               (json.dumps({"result": values}), attempt["id"]))
        await conn.execute("""UPDATE draft_generation_attempts SET outcome=?,error_code=?,finished_at=?,duration_ms=? WHERE id=?""",
                           (state, error, ended, duration, attempt["id"]))
        await conn.execute("""UPDATE job_queue SET status=?,next_attempt_at=?,generation_expected_revision=?,
            finished_at=?,error=? WHERE id=?""",
            ("pending" if retry_at else "processing", retry_at, revision(draft), None,
             error if state in {"failed", "retry_wait"} else None, job["id"]))
        return state
    return await Database(db_path).transaction(transaction, operation="finish_draft_attempt")


async def recover_attempt(job_id, db_path=None, error='process_exit'):
    """Called only under the singleton lock after cleanup, or between jobs.

    Closing an interrupted attempt uses the same bounded, durable schedule as a
    returned generation failure. A lost HTTP acknowledgement is already closed
    and cannot turn a saved success into a failure.
    """
    rows = await Database(db_path).fetch("""SELECT a.* FROM draft_generation_attempts a
        JOIN job_queue j ON j.id=a.job_id WHERE a.job_id=? AND a.outcome='running'
        AND j.status='processing' ORDER BY a.id DESC LIMIT 1""", (job_id,))
    if not rows:
        return False
    a = dict(rows[0])
    saved = await Database(db_path).fetch('SELECT * FROM ticket_results WHERE job_id=?', (job_id,))
    if saved and result_state(saved[0]) in {'ready','needs_review','no_reply'}:
        # Compatibility with a result committed by the legacy authenticated API.
        # Never turn that success into a failure while recovering its HTTP call.
        await Database(db_path).execute("""UPDATE draft_generation_attempts SET outcome='superseded',
            finished_at=?,duration_ms=?,error_code='result_already_committed'
            WHERE id=? AND outcome='running'""", (now(), max(0,int(
            (datetime.fromisoformat(now())-datetime.fromisoformat(a['started_at'])).total_seconds()*1000)),a['id']))
        return False
    context = json.loads(a.get('result_json') or '{}').get('priority_context') or {}
    priority = context.get('priority', 'normal')
    sensitive = priority in {'high', 'critical'}
    reason = 'Generation interrupted before a result was committed'
    if sensitive and context.get('reason'):
        reason += '; ' + context['reason']
    state = await finish_attempt(dict(generation_attempt_id=a['id'], job_id=job_id,
        ticket_id=a['ticket_id'], message_id=a['message_id'], priority=priority,
        action='sensitive_draft' if sensitive else 'drafted', reason=reason,
        notify_owner=sensitive and bool(context.get('notify_owner')), draft_text='', generation_state='failed', generation_error=error,
        review_required=True, staff_next_step='Retry the AI draft or compose a response.',
        missing_facts=[]), db_path)
    if state == 'failed':
        await Database(db_path).execute("UPDATE job_queue SET status='done',finished_at=? WHERE id=? AND status='processing'",
                                        (now(), job_id))
    return True


async def retry_draft(ticket_id, body, actor_id, db_path=None):
    operation_id = body.get("operation_id")
    source = body.get("source_message_id")
    expected = body.get("draft_revision")
    if not valid_operation(operation_id):
        raise ActionConflict("valid_operation_id_required", 400)
    if not isinstance(source, str) or not source or len(source) > 128:
        raise ActionConflict("source_message_id_required", 400)
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ActionConflict("draft_revision_required", 400)
    async def transaction(conn):
        previous = await one(conn, "SELECT * FROM draft_retry_requests WHERE operation_id=?", (operation_id,))
        if previous:
            if any(previous[k] != v for k, v in {"actor_id": actor_id, "ticket_id": ticket_id,
                    "message_id": source, "expected_revision": expected}.items()):
                raise ActionConflict("operation_id_conflict")
            return {"ok": True, "operation_id": operation_id, "job_id": previous["job_id"], "queued": True}
        problem = await conflict(conn, ticket_id, source)
        if problem:
            raise ActionConflict(problem)
        row = await one(conn, "SELECT * FROM ticket_results WHERE ticket_id=? AND message_id=?", (ticket_id, source))
        if not row or result_state(row) != "failed":
            raise ActionConflict("failed_draft_required")
        if revision(row.get("draft_text")) != expected:
            raise ActionConflict("draft_changed_refresh_ticket")
        job = await one(conn, "SELECT * FROM job_queue WHERE id=? AND ticket_id=? AND message_id=?", (row["job_id"], ticket_id, source))
        if not job or job["status"] in {"pending", "processing"}:
            raise ActionConflict("draft_retry_already_pending")
        await conn.execute("""INSERT INTO draft_retry_requests VALUES (?,?,?,?,?,?,?)""",
                           (operation_id, actor_id, ticket_id, source, expected, job["id"], now()))
        await conn.execute("""UPDATE job_queue SET status='pending',started_at=NULL,finished_at=NULL,
            error=NULL,retry_count=0,generation_cycle_attempts=0,next_attempt_at=NULL,
            generation_expected_revision=? WHERE id=?""", (expected, job["id"]))
        return {"ok": True, "operation_id": operation_id, "job_id": job["id"], "queued": True}
    return await Database(db_path).transaction(transaction, operation="retry_draft")
