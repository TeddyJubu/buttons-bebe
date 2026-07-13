"""Job processor orchestrator — the heart of the automated pipeline.

Polls the job_queue for pending jobs and processes them:
  - Customer messages → invoke Hermes headless → classify, draft for console review
  - Agent messages → invoke Hermes headless → feedback/learning loop

Hermes handles read-only KB search, context lookup, priority classification, and
draft generation using its skills (ticket-processor, gorgias, support-agent).
and tools (search_kb).

The processor handles: job lifecycle, Hermes invocation, output parsing,
WhatsApp notification, retry, timeout, and error recovery.

Risk mitigations:
  - Singleton lock (only one processor instance can run)
  - Stale job recovery on startup (reclaims crashed 'processing' jobs)
  - Per-job timeout (prevents hung Hermes calls from blocking the queue)
  - Retry with backoff (up to 3 retries for transient failures)
  - Graceful shutdown (finishes current job, then exits)
  - DB lock retry (WAL mode + busy_timeout + retry-on-locked)
  - All failures logged with context for debugging
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# Add webhook src to path for database imports
_webhook_src = Path(__file__).resolve().parent.parent / "webhook" / "src"
if str(_webhook_src) not in sys.path:
    sys.path.insert(0, str(_webhook_src))

from bb_webhook.database import (  # noqa: E402
    claim_job,
    complete_job,
    fail_job,
    get_pending_agent_jobs,
    get_pending_jobs,
    get_job_stats,
    init_db,
    requeue_stale_jobs,
)

from config import get_settings  # noqa: E402
from classifier import classify as deterministic_classify, IMMEDIATE, HIGH, NORMAL  # noqa: E402
from hermes_runner import process_ticket_with_hermes  # noqa: E402
from logging_setup import get_logger, setup_logging, log_event  # noqa: E402
from whatsapp_notifier import send_whatsapp  # noqa: E402

logger = get_logger(__name__)

# ── Result persistence ──────────────────────────────────────

def _save_result_to_webhook(
    ticket_id: int,
    message_id: str,
    job_id: int,
    hermes_result: dict,
    draft_text: str = "",
) -> None:
    """POST the Hermes result to the webhook API so the dashboard can show it.

    Fail-soft: logs a warning on error but never raises.
    """
    import urllib.request

    url = "http://127.0.0.1:8000/dashboard/api/results"
    payload = json.dumps({
        "ticket_id": ticket_id,
        "message_id": str(message_id),
        "job_id": job_id,
        "priority": hermes_result.get("priority", ""),
        "action": hermes_result.get("action", ""),
        "reason": hermes_result.get("reason", ""),
        "notify_owner": hermes_result.get("notify_owner", False),
        "gorgias_priority_set": hermes_result.get("gorgias_priority_set", False),
        "note_posted": hermes_result.get("note_posted", False),
        "draft_text": draft_text,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 300:
                log_event(logger, "DEBUG", "Result saved to dashboard API",
                          ticket_id=ticket_id, status=resp.status)
            else:
                log_event(logger, "WARNING", "Dashboard API returned non-2xx",
                          ticket_id=ticket_id, status=resp.status)
    except Exception as exc:
        log_event(logger, "WARNING", f"Failed to save result to dashboard API: {exc}",
                  ticket_id=ticket_id)


def _check_gorgias_writes_enabled() -> bool:
    """Check the gorgias_writes_enabled setting from the webhook API.

    Returns True if Gorgias writes are enabled, False if disabled (read-only mode).
    Defaults to False (disabled) for safety during testing.
    """
    import urllib.request
    url = "http://127.0.0.1:8000/dashboard/api/settings/gorgias_writes_enabled"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            value = data.get("value", "false").strip().lower()
            return value == "true"
    except Exception as exc:
        log_event(logger, "WARNING",
                  f"Failed to check gorgias_writes_enabled setting — defaulting to False: {exc}")
        return False

# ── Globals ────────────────────────────────────────────────
_shutdown = False
_lock_fd: int | None = None


def _handle_signal(signum: int, frame: Any) -> None:
    """Graceful shutdown on SIGTERM/SIGINT."""
    global _shutdown
    _shutdown = True
    log_event(logger, "INFO", "Shutdown signal received — finishing current job")


def _acquire_singleton_lock() -> bool:
    """Ensure only one processor instance runs at a time."""
    global _lock_fd
    lock_path = Path(__file__).resolve().parent / ".processor.lock"
    try:
        _lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(_lock_fd, f"{os.getpid()}\n".encode())
        return True
    except (OSError, IOError):
        if _lock_fd is not None:
            os.close(_lock_fd)
            _lock_fd = None
        return False


def _release_lock() -> None:
    """Release the singleton lock."""
    global _lock_fd
    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            os.close(_lock_fd)
        except (OSError, IOError):
            pass
        _lock_fd = None


# ── Customer message processing ────────────────────────────

async def process_customer_message(job: dict[str, Any]) -> dict[str, Any]:
    """Process a customer message by invoking Hermes headlessly.

    Hermes will:
    1. Read the ticket from Gorgias
    2. Search the KB
    3. Classify priority (CRITICAL/HIGH/NORMAL/LOW)
    4. Set Gorgias priority
    5. Draft a reply (always draft — sensitive topics get [SENSITIVE] tag)
    6. Return the draft for the console Ticket feed; perform no Gorgias write
    7. Return JSON_RESULT

    The processor then:
    - If CRITICAL or HIGH → sends WhatsApp notification to owner
    - Marks the job as done
    """
    payload = json.loads(job["payload"])
    ticket_id = payload.get("ticket_id")
    message_text = payload.get("message_text", "")
    ticket_subject = payload.get("ticket_subject", "")
    customer_email = payload.get("customer_email", "")
    job_id = job["id"]

    # Extract intent names from payload
    raw_intents = payload.get("intents", [])
    if isinstance(raw_intents, list):
        intent_names = [i.get("name") for i in raw_intents if isinstance(i, dict) and i.get("name")]
    else:
        intent_names = []

    result: dict[str, Any] = {
        "job_id": job_id,
        "ticket_id": ticket_id,
        "action": "unknown",
        "priority": "unknown",
    }

    # ── Deterministic first-pass classification (defense-in-depth) ──
    # Runs BEFORE Hermes as an advisory safety net. If the deterministic
    # classifier flags the ticket as sensitive/urgent, we honor that even
    # if the LLM later misclassifies it. The classifier can only ESCALATE
    # (NORMAL → HIGH/IMMEDIATE), never de-escalate.
    det_result = deterministic_classify(payload)
    log_event(logger, "INFO", "Deterministic classifier result",
              ticket_id=ticket_id,
              det_priority=det_result["priority"],
              det_sensitive=det_result["sensitive"],
              det_reason=det_result["reason"])

    # Check if Gorgias writes are enabled (dashboard toggle)
    gorgias_writes_enabled = _check_gorgias_writes_enabled()

    # Invoke Hermes headlessly
    hermes_result = process_ticket_with_hermes(
        ticket_id=ticket_id,
        message_text=message_text,
        ticket_subject=ticket_subject,
        customer_email=customer_email,
        intents=intent_names,
        gorgias_writes_enabled=gorgias_writes_enabled,
    )

    result["priority"] = hermes_result.get("priority", "high")
    result["action"] = hermes_result.get("action", "sensitive_draft")
    notify_owner = hermes_result.get("notify_owner", False)

    # ── Deterministic priority enforcement (defense-in-depth) ──────
    # The spec requires sensitive topics to be at least HIGH with owner
    # notification.  Hermes (the LLM) sometimes misclassifies sensitive
    # tickets as NORMAL — this gate catches that regardless of what the
    # LLM outputs.  The Gorgias write toggle must NOT affect this.
    action = result["action"]
    priority = result["priority"]

    if action == "sensitive_draft":
        # Sensitive topics (refunds, damaged/wrong items, disputes, etc.)
        # must always be at least HIGH and must always notify the owner.
        if priority not in ("critical", "high"):
            log_event(logger, "WARNING",
                      "Overriding LLM priority for sensitive topic",
                      ticket_id=ticket_id,
                      llm_priority=priority,
                      enforced_priority="high")
            result["priority"] = "high"
            hermes_result["priority"] = "high"
        if not notify_owner:
            log_event(logger, "WARNING",
                      "Forcing notify_owner for sensitive topic",
                      ticket_id=ticket_id,
                      llm_notify=False,
                      enforced_notify=True)
            notify_owner = True
            hermes_result["notify_owner"] = True

    # ── Deterministic classifier enforcement (escalate-only) ──────
    # If the deterministic classifier (which ran before Hermes) flagged
    # this ticket as IMMEDIATE or HIGH, escalate the final result to
    # match — even if the LLM said NORMAL. This catches LLM
    # misclassifications of sensitive tickets. The classifier can NEVER
    # de-escalate the LLM's assessment (escalate-only).
    if det_result["priority"] in (IMMEDIATE, HIGH):
        det_priority_map = {IMMEDIATE: "critical", HIGH: "high"}
        enforced = det_priority_map[det_result["priority"]]

        current = result["priority"]
        # Only escalate, never de-escalate
        escalate_order = {"low": 0, "normal": 1, "high": 2, "critical": 3}
        if escalate_order.get(enforced, 0) > escalate_order.get(current, 0):
            log_event(logger, "WARNING",
                      "Deterministic classifier escalating priority",
                      ticket_id=ticket_id,
                      llm_priority=current,
                      det_priority=det_result["priority"],
                      enforced_priority=enforced,
                      det_reason=det_result["reason"])
            result["priority"] = enforced
            hermes_result["priority"] = enforced

        # If classifier says sensitive, force sensitive action
        if det_result["sensitive"] and result["action"] != "sensitive_draft":
            log_event(logger, "WARNING",
                      "Deterministic classifier forcing sensitive action",
                      ticket_id=ticket_id,
                      llm_action=result["action"],
                      det_sensitive=True,
                      det_reason=det_result["reason"])
            result["action"] = "sensitive_draft"
            hermes_result["action"] = "sensitive_draft"

        # If classifier says notify, force notify
        if det_result["should_notify_owner"] and not notify_owner:
            log_event(logger, "WARNING",
                      "Deterministic classifier forcing notify_owner",
                      ticket_id=ticket_id,
                      llm_notify=False,
                      det_notify=True)
            notify_owner = True
            hermes_result["notify_owner"] = True

    # gorgias_priority_set and note_posted are informational — they
    # reflect whether the write actually happened, not the urgency.
    # When writes are disabled these are false, which is correct.
    # notify_owner is independent and based on ticket urgency alone.

    # Save result to the dashboard API (fail-soft)
    # When writes are disabled, use the draft extracted from Hermes output
    message_id = payload.get("message_id", "")
    draft_text = hermes_result.get("draft_text") or message_text
    _save_result_to_webhook(
        ticket_id=ticket_id,
        message_id=str(message_id),
        job_id=job_id,
        hermes_result=hermes_result,
        draft_text=draft_text,
    )

    # Send WhatsApp notification if CRITICAL or HIGH
    if notify_owner:
        send_whatsapp(
            ticket_id=ticket_id,
            subject=ticket_subject,
            customer_email=customer_email,
            message_summary=message_text[:300],
            reason=hermes_result.get("reason", "Priority notification"),
        )
        log_event(logger, "INFO", "Owner notification sent",
                  ticket_id=ticket_id,
                  priority=result["priority"],
                  reason=hermes_result.get("reason"))

    log_event(logger, "INFO", "Customer message processed",
              job_id=job_id,
              ticket_id=ticket_id,
              priority=result["priority"],
              action=result["action"],
              gorgias_priority_set=hermes_result.get("gorgias_priority_set"),
              note_posted=hermes_result.get("note_posted"))

    return result


# ── Agent message processing (feedback loop) ───────────────

async def process_agent_message(job: dict[str, Any]) -> dict[str, Any]:
    """Process an agent message — skip Hermes invocation (2026-07-08).

    Previously this invoked Hermes headlessly (~50s per agent reply) for the
    feedback/learning loop. That loop is now superseded by the console-action
    capture system (webhook/src/bb_webhook/learning.py records lessons when
    a human Sends/Notes/Requests-edit from the console, and
    KB/scripts/auto_promote_learned.py promotes them nightly).

    Agent messages are still received by the webhook and enqueued for audit,
    but no LLM processing is needed. We just mark the job complete.
    """
    payload = json.loads(job["payload"])
    ticket_id = payload.get("ticket_id")
    job_id = job["id"]

    log_event(logger, "INFO", "Agent message skipped (learning via console)",
              job_id=job_id,
              ticket_id=ticket_id,
              action="skipped_learning_via_console")

    return {
        "job_id": job_id,
        "ticket_id": ticket_id,
        "action": "skipped_learning_via_console",
    }


# ── Per-job timeout wrapper ────────────────────────────────

async def _run_with_timeout(coro: Any, timeout: int, job_id: int) -> Any:
    """Run a coroutine with a timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        log_event(logger, "ERROR", "Job timed out",
                  job_id=job_id, timeout=timeout)
        raise


# ── Main processing loop ───────────────────────────────────

async def run_processor() -> int:
    """Main entry point — starts the job processor loop."""
    global _shutdown

    settings = get_settings()
    setup_logging(settings.log_format, settings.log_level)

    # 1. Singleton lock
    if not _acquire_singleton_lock():
        print("ERROR: Another processor instance is already running.", file=sys.stderr)
        return 1

    log_event(logger, "INFO", "Job processor starting (Hermes-powered)",
              pid=os.getpid(),
              poll_interval=settings.poll_interval,
              job_timeout=settings.job_timeout,
              max_retries=settings.max_retries)

    # 2. Initialize DB
    await init_db(settings.db_path_absolute)

    # 3. Recover stale jobs
    stale_count = await requeue_stale_jobs(settings.stale_job_minutes, settings.db_path_absolute)
    if stale_count > 0:
        log_event(logger, "INFO", "Recovered stale jobs",
                  count=stale_count,
                  max_age_minutes=settings.stale_job_minutes)

    # 4. Log startup stats
    stats = await get_job_stats(settings.db_path_absolute)
    log_event(logger, "INFO", "Queue stats at startup", **stats)

    # 5. Main loop
    consecutive_errors = 0
    max_consecutive_errors = 10

    while not _shutdown:
        try:
            # Customer messages first (higher priority)
            customer_jobs = await get_pending_jobs(limit=1, db_path=settings.db_path_absolute)

            if customer_jobs:
                job = customer_jobs[0]
                await _process_one_job(
                    job, is_customer=True,
                    settings=settings,
                )
                consecutive_errors = 0
                continue

            # Then agent messages (feedback loop)
            agent_jobs = await get_pending_agent_jobs(limit=1, db_path=settings.db_path_absolute)

            if agent_jobs:
                job = agent_jobs[0]
                await _process_one_job(
                    job, is_customer=False,
                    settings=settings,
                )
                consecutive_errors = 0
                continue

            # No jobs — sleep
            consecutive_errors = 0
            await asyncio.sleep(settings.poll_interval)

        except Exception as exc:
            consecutive_errors += 1
            log_event(logger, "ERROR", f"Processor loop error: {exc}",
                      consecutive_errors=consecutive_errors,
                      traceback=traceback.format_exc()[:500])

            if consecutive_errors >= max_consecutive_errors:
                log_event(logger, "CRITICAL",
                          "Too many consecutive errors — shutting down",
                          count=consecutive_errors)
                break

            backoff = min(2 ** consecutive_errors, 60)
            await asyncio.sleep(backoff)

    # 6. Cleanup
    log_event(logger, "INFO", "Job processor shutting down")
    _release_lock()
    return 0


async def _process_one_job(
    job: dict[str, Any],
    is_customer: bool,
    settings: Any,
) -> None:
    """Process a single job with claim, timeout, and error handling."""
    job_id = job["id"]
    retry_count = job.get("retry_count", 0)

    # Claim the job atomically
    claimed = await claim_job(job_id, settings.db_path_absolute)
    if not claimed:
        return

    log_event(logger, "INFO", "Processing job",
              job_id=job_id,
              ticket_id=job["ticket_id"],
              is_customer=is_customer,
              retry_count=retry_count)

    try:
        if is_customer:
            result = await _run_with_timeout(
                process_customer_message(job),
                timeout=settings.job_timeout,
                job_id=job_id,
            )
        else:
            result = await _run_with_timeout(
                process_agent_message(job),
                timeout=settings.job_timeout,
                job_id=job_id,
            )

        await complete_job(job_id, db_path=settings.db_path_absolute)
        log_event(logger, "INFO", "Job completed",
                  job_id=job_id,
                  action=result.get("action"),
                  priority=result.get("priority"))

    except asyncio.TimeoutError:
        error_msg = f"Job timed out after {settings.job_timeout}s"
        await fail_job(job_id, error_msg, settings.db_path_absolute)
        log_event(logger, "ERROR", "Job failed — timeout",
                  job_id=job_id, error=error_msg)

        if retry_count < settings.max_retries:
            from bb_webhook.database import requeue_failed_job
            await requeue_failed_job(job_id, settings.db_path_absolute)
            log_event(logger, "INFO", "Job requeued for retry",
                      job_id=job_id, retry_count=retry_count + 1)

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        await fail_job(job_id, error_msg, settings.db_path_absolute)
        log_event(logger, "ERROR", "Job failed — exception",
                  job_id=job_id,
                  error=error_msg,
                  traceback=traceback.format_exc()[:500])

        if retry_count < settings.max_retries:
            from bb_webhook.database import requeue_failed_job
            await requeue_failed_job(job_id, settings.db_path_absolute)
            log_event(logger, "INFO", "Job requeued for retry",
                      job_id=job_id, retry_count=retry_count + 1)


def main() -> int:
    """Entry point for the job processor."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    return asyncio.run(run_processor())


if __name__ == "__main__":
    sys.exit(main())
