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
  - Periodic stale job recovery (reclaims crashed 'processing' jobs)
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
    claim_owner_alert,
    finish_owner_alert,
    get_job_result,
    complete_job,
    fail_job,
    get_pending_job_window,
    get_job_stats,
    init_db,
    requeue_stale_jobs,
)

from bb_webhook.result_auth import configured_secret
from config import get_settings  # noqa: E402
from classifier import classify as deterministic_classify, IMMEDIATE, HIGH, NORMAL  # noqa: E402
from demo_safety import demo_mode_enabled, demo_url_allowed  # noqa: E402
from hermes_runner import draft_for_console, process_ticket_with_hermes  # noqa: E402
from logging_setup import get_logger, setup_logging, log_event  # noqa: E402
from shared.priority import Priority, at_least, normalize
from shared.review_policy import final_review_result  # noqa: E402
from whatsapp_notifier import send_whatsapp  # noqa: E402

logger = get_logger(__name__)

# Bounded lookahead for sensitive-ticket priority. A sensitive customer message
# may jump ahead of at most this many older pending jobs, preserving FIFO
# fairness when a burst floods the queue.
_PRIORITY_WINDOW_LIMIT = 25
_CLASSIFICATION_CACHE_LIMIT = 512
_classification_cache: dict[str, dict[str, Any]] = {}


# ── Priority-aware selection ────────────────────────────────
def _remember_classification(message_id: str, result: dict[str, Any]) -> None:
    if not message_id:
        return
    if len(_classification_cache) >= _CLASSIFICATION_CACHE_LIMIT:
        _classification_cache.pop(next(iter(_classification_cache)))
    _classification_cache[message_id] = result


def _classify_for_selection(job: dict[str, Any]) -> dict[str, Any] | None:
    """Classify a pending job for window ordering without stalling the queue.

    Parse/classify failures stay uncached so the claimed job still fails
    inside _process_one_job instead of taking down the processor loop.
    """
    message_id = str(job.get("message_id") or "")
    if message_id:
        cached = _classification_cache.get(message_id)
        if cached is not None:
            return cached
    try:
        payload = json.loads(job["payload"])
        if not isinstance(payload, dict):
            raise ValueError("job payload must be an object")
        result = deterministic_classify(payload)
    except Exception as exc:
        log_event(
            logger,
            "WARNING",
            "Skipping unclassifiable job during priority selection",
            job_id=job.get("id"),
            message_id=message_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None
    _remember_classification(message_id, result)
    return result


def _select_next_job(window: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the next job from a bounded pending window.

    Oldest customer message first, exactly as before. When several customer jobs
    are pending, run the deterministic classifier over the window once and take
    the first IMMEDIATE/HIGH-sensitive customer job, so a chargeback or dispute
    does not wait behind a long FIFO backlog during a burst. Classification
    results are cached so the processor does not repeat the work when the
    selected job is processed. Agent jobs always stay behind customer jobs.
    """
    if not window:
        return None
    if len(window) == 1:
        return window[0]

    customer_jobs = [job for job in window if job.get("is_customer_message")]
    if len(customer_jobs) < 2:
        return window[0]

    for job in customer_jobs:
        result = _classify_for_selection(job)
        if result is None:
            continue
        if result.get("priority") in (IMMEDIATE, HIGH) and result.get("sensitive"):
            return job
    return window[0]


# ── Result persistence ──────────────────────────────────────
def _save_result_to_webhook(
    ticket_id: int,
    message_id: str,
    job_id: int,
    hermes_result: dict,
    draft_text: str = "",
) -> None:
    """POST the Hermes result to the webhook API so the dashboard can show it.

    Fail closed on transport or acknowledgement errors. Queue completion also
    verifies the committed row through the shared database, since even a valid
    HTTP acknowledgement is not proof of the exact job's persisted result.
    """
    import urllib.request

    url = os.environ.get(
        "DASHBOARD_RESULT_URL",
        "http://127.0.0.1:8000/dashboard/api/results",
    ).strip()
    if demo_mode_enabled() and not demo_url_allowed(
        url,
        port=8100,
        exact_path="/dashboard/api/results",
    ):
        log_event(
            logger,
            "ERROR",
            "Demo result persistence blocked: destination is not the local demo webhook",
            ticket_id=ticket_id,
        )
        raise RuntimeError("Demo result persistence destination is blocked")
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.port != (8100 if demo_mode_enabled() else 8000)
            or parsed.path != "/dashboard/api/results" or parsed.query or parsed.fragment
            or parsed.username or parsed.password):
        raise RuntimeError("Result persistence destination must be the local result API")
    secret = configured_secret(get_settings())
    if not secret:
        raise RuntimeError("Result persistence credential is not configured")
    payload = json.dumps({
        "ticket_id": ticket_id,
        "message_id": str(message_id),
        "job_id": job_id,
        "priority": hermes_result.get("priority", ""),
        "action": hermes_result.get("action", ""),
        "reason": hermes_result.get("reason", ""),
        "notify_owner": hermes_result.get("notify_owner", False),
        # Processor/Hermes never perform Gorgias writes. Only authenticated,
        # human-triggered console endpoints can make either side effect.
        "gorgias_priority_set": False,
        "note_posted": False,
        "draft_text": draft_text,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret}, method="POST",
    )
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=10) as resp:
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"Result persistence HTTP status {resp.status}")
        acknowledgement = json.loads(resp.read(4097))
        if not isinstance(acknowledgement, dict) or acknowledgement.get("status") != "ok":
            raise RuntimeError("Result persistence acknowledgement missing")
    log_event(logger, "DEBUG", "Result acknowledged by dashboard API", ticket_id=ticket_id)


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
    4. Recommend a review priority in its returned result
    5. Draft a reply (always draft — sensitive topics get [SENSITIVE] tag)
    6. Return the draft for the console Ticket feed; perform no Gorgias write
    7. Return JSON_RESULT

    This function persists the draft. The job wrapper verifies that committed
    result, attempts any required owner alert once, and then completes the job.
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
    message_id_for_cache = str(job.get("message_id") or "")
    cached_classification = _classification_cache.get(message_id_for_cache) if message_id_for_cache else None
    if cached_classification is not None:
        det_result = cached_classification
    else:
        det_result = deterministic_classify(payload)
        _remember_classification(message_id_for_cache, det_result)
    log_event(logger, "INFO", "Deterministic classifier result",
              ticket_id=ticket_id,
              det_priority=det_result["priority"],
              det_sensitive=det_result["sensitive"],
              det_reason=det_result["reason"])

    # Invoke Hermes headlessly
    hermes_result = process_ticket_with_hermes(
        ticket_id=ticket_id,
        message_text=message_text,
        ticket_subject=ticket_subject,
        customer_email=customer_email,
        intents=intent_names,
    )

    result["priority"] = normalize(
        hermes_result.get("priority", Priority.HIGH.value),
        default=Priority.HIGH,
    )
    hermes_result["priority"] = result["priority"]
    result["action"] = hermes_result.get("action", "sensitive_draft")
    notify_owner = hermes_result.get("notify_owner", False)

    # ── Deterministic classifier enforcement (escalate-only) ──────
    # If the deterministic classifier (which ran before Hermes) flagged
    # this ticket as IMMEDIATE or HIGH, escalate the final result to
    # match — even if the LLM said NORMAL. This catches LLM
    # misclassifications of sensitive tickets. The classifier can NEVER
    # de-escalate the LLM's assessment (escalate-only).
    if det_result["priority"] in (IMMEDIATE, HIGH):
        det_priority_map = {
            IMMEDIATE: Priority.CRITICAL.value,
            HIGH: Priority.HIGH.value,
        }
        enforced = det_priority_map[det_result["priority"]]

        current = result["priority"]
        # Only escalate, never de-escalate.
        if not at_least(current, enforced):
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

    # Apply the shared final review contract after every model/classifier change.
    # Unknown KB facts, explicit warnings and elevated priority cannot persist as
    # an ordinary draft merely because the model chose a contradictory action.
    hermes_result = final_review_result(hermes_result)
    result["priority"] = hermes_result["priority"]
    result["action"] = hermes_result.get("action", "sensitive_draft")

    # gorgias_priority_set and note_posted are always false here: the processor
    # and Hermes are strictly read-only. Human console actions are separate.

    # Save result before any alert or queue completion; failures must propagate.
    # Never substitute the customer's message for a failed AI draft. An empty
    # draft makes the console show a human-action-required state with no send or
    # internal-note buttons, while the high-priority fallback reason remains.
    message_id = payload.get("message_id", "")
    draft_text = draft_for_console(hermes_result)
    _save_result_to_webhook(
        ticket_id=ticket_id,
        message_id=str(message_id),
        job_id=job_id,
        hermes_result=hermes_result,
        draft_text=draft_text,
    )

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
    stale_count = await requeue_stale_jobs(
        settings.stale_job_minutes, settings.db_path_absolute, max_retries=settings.max_retries,
    )
    if stale_count > 0:
        log_event(logger, "INFO", "Resolved abandoned claims; exhausted retries marked failed",
                  count=stale_count,
                  max_age_minutes=settings.stale_job_minutes)

    # 4. Log startup stats
    stats = await get_job_stats(settings.db_path_absolute)
    log_event(logger, "INFO", "Queue stats at startup", **stats)

    # Gorgias webhooks remain the primary intake. This independent read-only
    # sweep recovers customer messages when the provider stops delivering them.
    # Demo mode must never inspect the production Gorgias MCP endpoint.
    tenant = getattr(settings, "gorgias_subdomain", None)
    reconcile_task = None
    if tenant and not getattr(settings, "demo_mode", False):
        from gorgias_reconcile import reconcile_loop
        reconcile_task = asyncio.create_task(reconcile_loop(settings.db_path_absolute, tenant))

    # 5. Main loop
    consecutive_errors = 0
    max_consecutive_errors = 10
    # Monotonic timestamp of the last "still alive" line. Starts at -inf so the
    # first idle pass logs immediately, proving liveness right after startup.
    last_idle_heartbeat = float("-inf")
    last_recovery = time.monotonic()
    # Sweep between jobs under the singleton lock, never in a background task
    # that could reclaim this process's still-running job. Newly abandoned
    # claims after a quick restart will age out without a second restart.
    recovery_interval = 60.0

    while not _shutdown:
        try:
            if time.monotonic() - last_recovery >= recovery_interval:
                recovered = await requeue_stale_jobs(
                    settings.stale_job_minutes, settings.db_path_absolute,
                    max_retries=settings.max_retries,
                )
                last_recovery = time.monotonic()
                if recovered:
                    log_event(logger, "WARNING", "Resolved abandoned claims; exhausted retries marked failed",
                              count=recovered,
                              max_age_minutes=settings.stale_job_minutes)
            # One bounded window query per pass, with customer messages ordered
            # ahead of agent feedback work. Within that window the processor
            # may promote a sensitive customer job (IMMEDIATE/HIGH per the
            # deterministic classifier) ahead of older normal customer jobs; the
            # window bound keeps that reordering fair during bursts. Claiming
            # remains the atomic claim_job transition.
            window = await get_pending_job_window(
                db_path=settings.db_path_absolute,
                limit=_PRIORITY_WINDOW_LIMIT,
            )
            job = _select_next_job(window)

            if job:
                await _process_one_job(
                    job,
                    is_customer=bool(job.get("is_customer_message")),
                    settings=settings,
                )
                consecutive_errors = 0
                continue

            # No jobs — emit a periodic "still alive" line, then sleep.
            # processor/heartbeat.sh reads the journal and treats total silence
            # over PROCESSOR_STALE_MINUTES as "the loop is wedged". Without this
            # line a genuinely quiet night would look identical to a hang.
            consecutive_errors = 0
            heartbeat_every = getattr(settings, "heartbeat_seconds", 120.0)
            if heartbeat_every > 0:
                now = time.monotonic()
                if now - last_idle_heartbeat >= heartbeat_every:
                    last_idle_heartbeat = now
                    log_event(logger, "INFO", "Processor idle heartbeat",
                              pid=os.getpid())
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
    if reconcile_task is not None:
        reconcile_task.cancel()
        try:
            await reconcile_task
        except asyncio.CancelledError:
            pass
    log_event(logger, "INFO", "Job processor shutting down")
    _release_lock()
    return 0


async def _notify_owner_once(job: dict, saved: dict, db_path: Path) -> None:
    """Avoid repeat transport after a crash or unknown acknowledgement.

    An uncertain attempt remains operator-visible in dashboard data/stats.
    It is not automatically retried: the bridge has no end-to-end idempotency
    guarantee, so the system cannot prove a timed-out message was not sent.
    """
    if not await claim_owner_alert(job["id"], db_path):
        log_event(logger, "WARNING", "Owner alert already attempted; inspect delivery state",
                  job_id=job["id"], ticket_id=job["ticket_id"])
        return
    payload = json.loads(job["payload"])
    accepted = False
    try:
        accepted = send_whatsapp(
            ticket_id=job["ticket_id"], subject=payload.get("ticket_subject", ""),
            customer_email=payload.get("customer_email", ""),
            message_summary=str(payload.get("message_text") or "")[:300],
            reason=saved.get("reason", "Priority notification"),
            max_retries=0,
        ) is True
    finally:
        await finish_owner_alert(job["id"], accepted, db_path)
    log_event(logger, "INFO" if accepted else "ERROR",
              "Owner alert accepted by bridge" if accepted else "Owner alert uncertain; operator review required",
              job_id=job["id"], ticket_id=job["ticket_id"])


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
        saved = await get_job_result(job_id, settings.db_path_absolute) if is_customer else None
        if saved is not None:
            # A prior attempt may have committed successfully before losing its
            # HTTP response or crashing. Keep that reviewed draft and do not run
            # Hermes again or create another owner-alert attempt.
            result = saved
        elif is_customer:
            result = await _run_with_timeout(
                process_customer_message(job), timeout=settings.job_timeout, job_id=job_id,
            )
        else:
            result = await _run_with_timeout(
                process_agent_message(job), timeout=settings.job_timeout, job_id=job_id,
            )

        if is_customer:
            saved = await get_job_result(job_id, settings.db_path_absolute)
            if saved is None:
                raise RuntimeError("No committed result for this job; completion refused")
            if saved.get("notify_owner"):
                await _notify_owner_once(job, saved, settings.db_path_absolute)

        completed = await complete_job(
            job_id, db_path=settings.db_path_absolute, require_result=is_customer,
        )
        if not completed:
            raise RuntimeError("Job completion rejected: claim or durable result missing")
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
            await requeue_failed_job(job_id, settings.db_path_absolute, max_retries=settings.max_retries)
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
            await requeue_failed_job(job_id, settings.db_path_absolute, max_retries=settings.max_retries)
            log_event(logger, "INFO", "Job requeued for retry",
                      job_id=job_id, retry_count=retry_count + 1)


def main() -> int:
    """Entry point for the job processor."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    return asyncio.run(run_processor())


if __name__ == "__main__":
    sys.exit(main())
