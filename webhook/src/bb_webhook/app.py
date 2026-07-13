"""FastAPI application — Gorgias webhook receiver.

Endpoints:
  POST /webhook/gorgias/{tenant_id}   — webhook from Gorgias HTTP integration
  GET  /health                        — health check
  GET  /ready                         — readiness check (DB + Gorgias connectivity)
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path
import sys as _sys
_AGENT_ROOT = str(Path(__file__).resolve().parents[3])
if _AGENT_ROOT not in _sys.path:
    _sys.path.insert(0, _AGENT_ROOT)

from .config import get_settings
from .database import (
    enqueue_job,
    get_all_settings,
    get_dashboard_tickets,
    get_parsed_messages,
    get_parsed_stats,
    get_result_stats,
    get_setting,
    init_db,
    is_duplicate,
    record_event,
    record_parsed_message,
    record_ticket_result,
    set_setting,
)
from .logging_utils import get_logger, log_event, setup_logging
from .webhook_handler import (
    is_event_too_old,
    parse_event,
    verify_signature,
)

logger = get_logger(__name__)

# ── Rate limiter (simple sliding-window per IP) ───────────
_MAX_REQUESTS_PER_MINUTE = 60
_rate_window: deque[tuple[float, str]] = deque()


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if the request is within the rate limit."""
    now = time.monotonic()
    cutoff = now - 60.0  # 1-minute window
    # Evict old entries
    while _rate_window and _rate_window[0][0] < cutoff:
        _rate_window.popleft()
    # Count requests from this IP in the window
    count = sum(1 for ts, ip in _rate_window if ip == client_ip)
    if count >= _MAX_REQUESTS_PER_MINUTE:
        return False
    _rate_window.append((now, client_ip))
    return True


# ── Lifespan: init DB on startup ───────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    log_event(logger, "INFO", "Starting webhook receiver",
              host=settings.webhook_host,
              port=settings.webhook_port,
              tenant=settings.gorgias_subdomain)
    await init_db()
    yield
    log_event(logger, "INFO", "Shutting down webhook receiver")


# ── App ──────────────────────────────────────────────────
app = FastAPI(
    title="Buttons Bebe Webhook Receiver",
    description="Receives Gorgias ticket-message webhooks, validates signature, "
                "dedupes, and enqueues jobs for the orchestrator.",
    version="0.2.0",
    lifespan=lifespan,
)


# ── Routes ────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — always returns 200 if the process is alive."""
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks DB exists and Gorgias credentials are set."""
    settings = get_settings()
    checks: dict[str, Any] = {}

    # DB check
    try:
        db_path = settings.db_path_absolute
        checks["db"] = "ok" if db_path.parent.exists() else "missing"
    except Exception as exc:
        checks["db"] = f"error: {exc}"

    # Gorgias credentials check (without making API call)
    checks["gorgias_configured"] = bool(settings.gorgias_auth)

    all_ok = all(v == "ok" or v is True for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "not_ready", "checks": checks},
    )


# ── Console API (serves the management console at /console) ───
# The console is a static SPA at /var/www/console/index.html (served by Caddy).
# It calls /console/api/* which Caddy rewrites to /dashboard/api/* below.
# The old /dashboard HTML route has been removed — the console replaces it.


@app.get("/dashboard/api/messages")
async def dashboard_messages(
    limit: int = 50,
    offset: int = 0,
    customer_only: bool = False,
) -> JSONResponse:
    """API endpoint for the dashboard — returns parsed messages as JSON."""
    msgs = await get_parsed_messages(
        limit=min(limit, 200),
        offset=offset,
        customer_only=customer_only,
    )
    return JSONResponse(content=msgs)


@app.get("/dashboard/api/stats")
async def dashboard_stats() -> JSONResponse:
    """API endpoint for the dashboard — returns aggregate stats."""
    stats = await get_result_stats()
    return JSONResponse(content=stats)


@app.get("/dashboard/api/tickets")
async def dashboard_tickets_api(
    limit: int = 100,
    offset: int = 0,
) -> JSONResponse:
    """API endpoint — customer messages joined with AI processing results."""
    tickets = await get_dashboard_tickets(
        limit=min(limit, 500),
        offset=offset,
    )
    return JSONResponse(content=tickets)


@app.post("/dashboard/api/results")
async def record_result_api(request: Request) -> JSONResponse:
    """Internal endpoint for the processor to record a Hermes result.

    Called by the orchestrator after Hermes finishes processing a ticket.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    required = {"ticket_id", "message_id", "priority", "action"}
    if not required.issubset(body.keys()):
        return JSONResponse(
            status_code=400,
            content={"error": "missing_fields", "required": list(required)},
        )

    await record_ticket_result(
        ticket_id=body["ticket_id"],
        message_id=str(body["message_id"]),
        job_id=body.get("job_id"),
        priority=body.get("priority", ""),
        action=body.get("action", ""),
        reason=body.get("reason", ""),
        notify_owner=bool(body.get("notify_owner", False)),
        gorgias_priority_set=bool(body.get("gorgias_priority_set", False)),
        note_posted=bool(body.get("note_posted", False)),
        draft_text=body.get("draft_text"),
    )

    return JSONResponse(content={"status": "ok"})


@app.get("/dashboard/api/settings")
async def dashboard_get_settings() -> JSONResponse:
    """Get all dashboard/app settings."""
    settings = await get_all_settings()
    # Ensure defaults
    if "gorgias_writes_enabled" not in settings:
        settings["gorgias_writes_enabled"] = "false"
    return JSONResponse(content=settings)


@app.get("/dashboard/api/settings/{key}")
async def dashboard_get_one_setting(key: str) -> JSONResponse:
    """Get a single setting value."""
    value = await get_setting(key, default="")
    return JSONResponse(content={"key": key, "value": value})


@app.put("/dashboard/api/settings")
async def dashboard_put_settings(request: Request) -> JSONResponse:
    """Update a dashboard/app setting.

    Body: {"key": "value"} — currently supports:
    - gorgias_writes_enabled: "true" | "false"
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    if not body or not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "empty_body"})

    for key, value in body.items():
        await set_setting(key, str(value).lower())
        log_event(logger, "INFO", f"Setting updated: {key}={value}")

    return JSONResponse(content={"status": "ok", "updated": body})



@app.get("/dashboard/api/review/list")
async def review_list() -> JSONResponse:
    from feedback import review as _rv
    return JSONResponse(content={"pending": _rv.list_pending()})


@app.get("/dashboard/api/review/packet/{ticket_id}")
async def review_packet(ticket_id: str) -> JSONResponse:
    from feedback import review as _rv
    p = _rv.get_packet(ticket_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return JSONResponse(content={
        "ticket_id": p["ticket_id"], "front": p["front"],
        "situation_masked": p["situation_masked"],
        "reply_masked": p["reply_masked"], "reply_raw": p["reply"],
        "pii_reply": p["pii_reply"],
    })


@app.post("/dashboard/api/review/approve/{ticket_id}")
async def review_approve(ticket_id: str, request: Request) -> JSONResponse:
    from feedback import review as _rv
    try:
        body = await request.json()
    except Exception:
        body = {}
    r = _rv.approve(ticket_id, pii_cleared=bool(body.get("pii_cleared")),
                    note=str(body.get("note", "")), why=str(body.get("why", "")))
    return JSONResponse(content=r, status_code=200 if r.get("ok") else 400)


@app.post("/dashboard/api/review/reject/{ticket_id}")
async def review_reject(ticket_id: str, purge: bool = False) -> JSONResponse:
    from feedback import review as _rv
    return JSONResponse(content=_rv.reject(ticket_id, purge=purge))


@app.post("/dashboard/api/review/reindex")
async def review_reindex() -> JSONResponse:
    from feedback import review as _rv
    return JSONResponse(content=_rv.reindex())


@app.post("/webhook/gorgias/{tenant_id}")
async def receive_gorgias_webhook(
    request: Request,
    tenant_id: str,
) -> JSONResponse:
    """Main webhook endpoint for Gorgias HTTP integration.

    Flow:
    1. Rate-limit check
    2. Read raw body (needed for HMAC verification)
    3. Verify authenticity (HMAC signature OR query-string secret)
    4. Parse + normalize event (coerce stringified Gorgias template values)
    5. Check idempotency (dedup by message_id)
    6. Check replay (event not too old)
    7. Persist event to DB (always, for audit)
    8. Enqueue job for orchestrator (only for customer-authored messages)
    9. Return 202 Accepted (or 200 if skipped)
    """
    raw_body = await request.body()

    # 1. Verify authenticity FIRST (reject unauthenticated before rate-limiting)
    sig_header = request.headers.get("X-Gorgias-Signature")
    query_secret = request.query_params.get("secret")
    if not verify_signature(raw_body, sig_header, query_secret):
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_signature"},
        )

    # 2. Rate limit (only for authenticated requests)
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        log_event(logger, "WARNING", "Rate limit exceeded", client_ip=client_ip)
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited"},
        )

    # 3. Parse event
    event = parse_event(raw_body)
    if event is None:
        return JSONResponse(
            status_code=400,
            content={"error": "malformed_payload"},
        )

    # Verify tenant matches
    if event["tenant_id"] != tenant_id:
        log_event(logger, "WARNING", "Tenant mismatch in webhook",
                  url_tenant=tenant_id,
                  event_tenant=event["tenant_id"])
        return JSONResponse(
            status_code=404,
            content={"error": "tenant_not_found"},
        )

    ticket_id = event.get("ticket_id")
    message_id_str = str(event.get("message_id") or "")
    if not ticket_id or not message_id_str:
        log_event(logger, "WARNING", "Webhook missing ticket_id or message_id",
                  tenant_id=tenant_id,
                  ticket_id=ticket_id,
                  message_id=message_id_str)
        return JSONResponse(
            status_code=400,
            content={"error": "missing_ticket_or_message_id"},
        )

    log_event(logger, "INFO", "Webhook received",
              tenant_id=tenant_id,
              ticket_id=ticket_id,
              message_id=message_id_str,
              event_type=event["event_type"],
              author_type=event["author_type"],
              is_customer_message=event["is_customer_message"],
              channel=event["channel"])

    # 4. Idempotency check — dedup by message_id
    if await is_duplicate(message_id_str):
        log_event(logger, "INFO", "Duplicate webhook — skipping",
                  message_id=message_id_str,
                  ticket_id=ticket_id)
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "message_id": message_id_str},
        )

    # 5. Replay protection — reject old events
    if is_event_too_old(event.get("created_at")):
        log_event(logger, "WARNING", "Webhook event too old — rejecting",
                  message_id=message_id_str,
                  created_at=event.get("created_at"))
        return JSONResponse(
            status_code=410,
            content={"error": "event_expired"},
        )

    # 6. Persist ALL events for audit/dedup (agent + customer)
    await record_event(
        message_id=message_id_str,
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        event_type=event["event_type"],
        author_type=event["author_type"],
        raw_payload=raw_body.decode("utf-8", errors="replace"),
    )

    # 6b. Store parsed message for the dashboard (all messages)
    await record_parsed_message(
        message_id=message_id_str,
        ticket_id=ticket_id,
        event_type=event["event_type"],
        author_type=event["author_type"],
        author_email=event.get("author_email"),
        channel=event.get("channel"),
        customer_email=event.get("customer_email"),
        ticket_subject=event.get("ticket_subject"),
        message_text=event.get("message_text"),
        intents=event.get("intents", []),
        is_customer_message=bool(event.get("is_customer_message", False)),
        created_at=event.get("created_at"),
    )

    # 7. Enqueue job — customer messages for drafting, agent messages for feedback
    is_customer = event.get("is_customer_message", False)

    job_payload = {
        "tenant_id": tenant_id,
        "ticket_id": ticket_id,
        "message_id": event.get("message_id"),
        "event_type": event["event_type"],
        "author_type": event["author_type"],
        "author_email": event.get("author_email"),
        "channel": event.get("channel"),
        "customer_email": event.get("customer_email"),
        "ticket_subject": event.get("ticket_subject"),
        "message_text": event.get("message_text"),
        "intents": event.get("intents", []),
        "created_at": event.get("created_at"),
    }

    job_id = await enqueue_job(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        message_id=message_id_str,
        event_type=event["event_type"],
        author_type=event["author_type"],
        is_customer_message=is_customer,
        payload=job_payload,
    )

    if not is_customer:
        log_event(logger, "INFO", "Agent message enqueued for feedback loop",
                  job_id=job_id,
                  ticket_id=ticket_id,
                  message_id=message_id_str,
                  author_type=event["author_type"])
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "job_id": job_id,
                "ticket_id": ticket_id,
                "message_id": message_id_str,
                "author_type": event["author_type"],
                "purpose": "feedback_loop",
            },
        )

    log_event(logger, "INFO", "Webhook processed and job enqueued",
              job_id=job_id,
              ticket_id=ticket_id,
              message_id=message_id_str,
              author_type=event["author_type"])

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": job_id,
            "ticket_id": ticket_id,
            "message_id": message_id_str,
            "author_type": event["author_type"],
            "event_type": event["event_type"],
            "intents": [i.get("name") for i in event.get("intents", []) if isinstance(i, dict)],
        },
    )

# ── Reply-from-dashboard actions (send / internal note / rewrite) ──
import os as _os
import asyncio as _asyncio
from .gorgias_client import GorgiasClient as _GClient
from .learning import record_lesson as _record_lesson, ledger as _ledger

_HERMES_BIN = "/usr/local/bin/hermes"


@app.post("/dashboard/api/ticket/{ticket_id}/send")
async def action_send(ticket_id: int, request: Request) -> JSONResponse:
    """Send a customer-facing reply directly (human-initiated from the console)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "empty reply"})
    res = await _GClient().send_public_reply(ticket_id, text)
    if not res.get("ok"):
        log_event(logger, "ERROR", "Public reply failed", ticket_id=ticket_id,
                  detail=res.get("error"))
        return JSONResponse(status_code=502, content={"error": res.get("error", "send failed")})
    log_event(logger, "INFO", "Public reply sent from dashboard", ticket_id=ticket_id)
    try:
        _record_lesson("sent", ticket_id, str(body.get("message_text", "")), str(body.get("ai_draft", "")), text, customer_name=str(body.get("customer_name", "")))
    except Exception:
        pass
    return JSONResponse(content={"ok": True})


@app.post("/dashboard/api/ticket/{ticket_id}/note")
async def action_note(ticket_id: int, request: Request) -> JSONResponse:
    """Post the draft as a Gorgias internal note (staff-only)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "empty note"})
    res = await _GClient().post_internal_note(ticket_id, text)
    if not res.get("ok"):
        log_event(logger, "ERROR", "Internal note failed", ticket_id=ticket_id,
                  detail=res.get("error"))
        return JSONResponse(status_code=502, content={"error": res.get("error", "note failed")})
    log_event(logger, "INFO", "Internal note posted from dashboard", ticket_id=ticket_id)
    try:
        _record_lesson("note", ticket_id, str(body.get("message_text", "")), str(body.get("ai_draft", "")), text, customer_name=str(body.get("customer_name", "")))
    except Exception:
        pass
    return JSONResponse(content={"ok": True})


@app.post("/dashboard/api/ticket/{ticket_id}/rewrite")
async def action_rewrite(ticket_id: int, request: Request) -> JSONResponse:
    """Rewrite the current draft to follow a human instruction (via Hermes)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    draft = str(body.get("draft", "")).strip()
    instruction = str(body.get("instruction", "")).strip()
    customer_msg = str(body.get("message_text", "")).strip()
    if not instruction:
        return JSONResponse(status_code=400, content={"error": "no instruction"})
    prompt = (
        "You are rewriting a customer-support reply for Buttons Bebe (a baby "
        "clothing Shopify store). Output ONLY the final customer-facing reply "
        "text - no preamble, no quotes, no commentary, no sign-off notes.\n\n"
        "CUSTOMER MESSAGE:\n" + customer_msg + "\n\n"
        "CURRENT DRAFT REPLY:\n" + draft + "\n\n"
        "REWRITE INSTRUCTION FROM THE HUMAN AGENT:\n" + instruction + "\n\n"
        "Rewrite the reply to follow the instruction. Stay accurate to Buttons "
        "Bebe policy; do not invent facts, prices, or promises."
    )
    try:
        env = dict(_os.environ)
        env["HOME"] = "/root"
        proc = await _asyncio.create_subprocess_exec(
            _HERMES_BIN, "-z", prompt,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
            env=env,
        )
        out, err = await _asyncio.wait_for(proc.communicate(), timeout=150)
    except _asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={"error": "rewrite timed out"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    reply = (out or b"").decode("utf-8", "ignore").strip()
    if not reply:
        return JSONResponse(status_code=502, content={"error": "empty rewrite"})
    for marker in ["\nThe response above", "\nThe previous response",
                   "\nThe reply above", "\nNote:", "\nNote that",
                   "\nI have rewritten", "\n(Note", "\nLet me know"]:
        i = reply.find(marker)
        if i != -1:
            reply = reply[:i].strip()
    try:
        _record_lesson(
            "rewrite",
            ticket_id,
            customer_msg,
            draft,
            reply,
            instruction=instruction,
            customer_name=str(body.get("customer_name", "")),
        )
    except Exception:
        pass
    return JSONResponse(content={"ok": True, "draft": reply})


@app.get("/dashboard/api/learning")
async def learning_stats() -> JSONResponse:
    """Learning ledger: lessons captured + draft acceptance."""
    return JSONResponse(content=_ledger())
