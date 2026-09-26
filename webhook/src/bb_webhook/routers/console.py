"""Human-gated console actions."""

from __future__ import annotations

import asyncio as _asyncio
import os as _os

from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

from .. import deps
from ..console_actions import execute_action, action_status, actor, preflight_refusal
from ..gorgias_client import GorgiasClient as _GClient
from ..learning import ledger as _ledger, record_lesson as _record_lesson
from ..rewrite_runner import run_rewrite, RewriteFailure
from ..logging_utils import get_logger, log_event

router = APIRouter(prefix="/dashboard/api")
logger = get_logger(__name__)


@router.post('/ticket/{ticket_id}/retry-draft')
async def retry_failed_draft(ticket_id: int, request: Request) -> JSONResponse:
    """Queue read-only AI work; never enable send access or call a provider."""
    from ..draft_generation import retry_draft
    from ..send_intents import ActionConflict
    reviewer = actor(request)
    if not reviewer:
        return JSONResponse(status_code=401, content={'error': 'not_authenticated'})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={'error': 'invalid_json'})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={'error': 'invalid_json_object'})
    try:
        result = await retry_draft(ticket_id, body, reviewer, deps.get_db())
        return JSONResponse(status_code=202, content=result)
    except ActionConflict as exc:
        return JSONResponse(status_code=exc.status, content={'error': exc.error})

_HERMES_BIN = _os.environ.get("HERMES_BIN", "/usr/local/bin/hermes")
_HERMES_HOME = _os.environ.get("HERMES_OS_HOME", "/root")
_HERMES_PROFILE = _os.environ.get("HERMES_PROFILE", "").strip()
_HERMES_REWRITE_TOOLSETS = _os.environ.get("HERMES_REWRITE_TOOLSETS", "").strip()
# Empty means: invoke Hermes with no -t flag (model default tools). A bogus
# name like the old "todo" default would silently drop tools, so only
# non-empty values are passed through (see action_rewrite below).
_HERMES_IGNORE_RULES = _os.environ.get("HERMES_IGNORE_RULES", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_SUPPORT_STORE_NAME = " ".join(
    _os.environ.get("SUPPORT_STORE_NAME", "Buttons Bebe").split()
)[:80] or "Buttons Bebe"


def _app_value(name: str, default):
    return deps.resolve(name, default)


@router.get("/inbox/review-context/{inbox_ticket_id}")
async def inbox_review_context(inbox_ticket_id: str, request: Request,
                               source_message_id: str = Query(min_length=1,max_length=200),
                               draft_revision: str | None = Query(default=None,pattern="^[0-9a-f]{64}$"),
                               expected_recipient: str | None = Query(default=None,max_length=320)) -> JSONResponse:
    """Read-only Inbox preparation. This route alone cannot authorize delivery."""
    import re
    from ..send_intents import IntentStore, ActionConflict
    reviewer=actor(request)
    if not reviewer:return JSONResponse(status_code=401,content={"error":"not_authenticated"})
    if not re.fullmatch(r"gorgias:[1-9][0-9]{0,17}",inbox_ticket_id):
        return JSONResponse(status_code=400,content={"error":"invalid_inbox_ticket_id"})
    try:
        context=await IntentStore(deps.get_db()).review_context(ticket_id=int(inbox_ticket_id[8:]),
            source_message_id=source_message_id,actor_id=reviewer,
            expected_revision=draft_revision,expected_recipient=expected_recipient)
        return JSONResponse(content={"ok":True,"context":context})
    except ActionConflict as exc:
        return JSONResponse(status_code=exc.status,content={"ok":False,"error":exc.error})
    except Exception as exc:
        log_event(logger,"ERROR","Inbox review context unavailable",error_type=type(exc).__name__)
        return JSONResponse(status_code=503,content={"ok":False,"error":"review_context_unavailable"})


@router.post("/inbox/send-access")
async def inbox_send_access(request: Request) -> JSONResponse:
    """Toggle only this page's manual-send grant; never contact Gorgias."""
    from ..inbox_send_access import InboxSendAccess
    reviewer = actor(request)
    session_id = getattr(request.state, 'session_id', None)
    if not reviewer or not session_id:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    if not isinstance(body, dict) or type(body.get('enabled')) is not bool:
        return JSONResponse(status_code=400, content={"error": "enabled_boolean_required"})
    try:
        grants = InboxSendAccess(deps.get_db())
        if body['enabled']:
            result = await grants.enable(reviewer, session_id)
        else:
            await grants.disable(request.headers.get('X-Inbox-Send-Access'), reviewer, session_id)
            result = {}
        log_event(logger, "INFO", "Inbox manual send access changed", actor_id=reviewer, enabled=body['enabled'])
        return JSONResponse(content={"ok": True, "enabled": body['enabled'], **result})
    except Exception as exc:
        log_event(logger, "ERROR", "Inbox send access unavailable", error_type=type(exc).__name__)
        return JSONResponse(status_code=503, content={"error": "send_access_unavailable"})


@router.post("/inbox/ticket/{ticket_id}/send")
async def inbox_send(ticket_id: int, request: Request) -> JSONResponse:
    """Human-confirmed Inbox reply through the existing durable console sender."""
    from ..inbox_send_access import InboxSendAccess
    from ..send_intents import IntentStore, ActionConflict
    body = None
    try:
        body = await request.json()
    except Exception:
        return await preflight_refusal(400, "invalid_json", body)
    if not isinstance(body, dict):
        return await preflight_refusal(400, "invalid_json_object", body)
    try:
        allowed = await InboxSendAccess(deps.get_db()).allowed(
            request.headers.get('X-Inbox-Send-Access'), actor(request), getattr(request.state, 'session_id', None))
        if not allowed:
            return await preflight_refusal(403, "inbox_read_only", body)
        if body.get('confirmed') is not True:
            return await preflight_refusal(409, "confirmation_required", body)
        if not isinstance(body.get('source_message_id'), str) or not isinstance(body.get('expected_recipient'), str):
            return await preflight_refusal(400, "review_context_required", body)
        context = await IntentStore(deps.get_db()).review_context(
            ticket_id=ticket_id, source_message_id=body['source_message_id'], actor_id=actor(request),
            expected_revision=body.get('draft_revision'), expected_recipient=body['expected_recipient'])
        if not context['recipient'] or not context['channel'] or context['sourceMessageTruncated']:
            return await preflight_refusal(409, "reply_context_unavailable", body)
        if body.get('context_id') != context['contextId']:
            return await preflight_refusal(409, "review_changed_refresh_ticket", body)
    except ActionConflict as exc:
        return await preflight_refusal(exc.status, exc.error, body)
    except Exception as exc:
        log_event(logger, "ERROR", "Inbox send preflight unavailable", error_type=type(exc).__name__)
        return await preflight_refusal(503, "review_context_unavailable", body)
    return await action_send(ticket_id, request)


@router.post("/ticket/{ticket_id}/send")
async def action_send(ticket_id: int, request: Request) -> JSONResponse:
    """Send a customer-facing reply after an explicit human confirmation."""
    body = None
    try:
        body = await request.json()
    except Exception:
        return await preflight_refusal(400, "invalid_json", body)
    if not isinstance(body, dict):
        return await preflight_refusal(400, "invalid_json_object", body)
    if not await deps.database_function("dashboard_ticket_exists")(ticket_id):
        return await preflight_refusal(404, "ticket_not_in_console", body)
    raw_text = body.get("text", "")
    if not isinstance(raw_text, str):
        return await preflight_refusal(400, "invalid_reply", body)
    text = raw_text.strip()
    if not text or len(text) > 50_000:
        return await preflight_refusal(400, "empty reply", body)
    if body.get("confirmed") is not True:
        return await preflight_refusal(409, "confirmation_required", body)
    return await execute_action('send', ticket_id, request, body, text,
                                _app_value("_GClient", _GClient), _app_value("_record_lesson", _record_lesson))


@router.post("/ticket/{ticket_id}/note")
async def action_note(ticket_id: int, request: Request) -> JSONResponse:
    """Post a draft as a staff-only Gorgias internal note."""
    body = None
    try:
        body = await request.json()
    except Exception:
        return await preflight_refusal(400, "invalid_json", body)
    if not isinstance(body, dict):
        return await preflight_refusal(400, "invalid_json_object", body)
    if not await deps.database_function("dashboard_ticket_exists")(ticket_id):
        return await preflight_refusal(404, "ticket_not_in_console", body)
    raw_text = body.get("text", "")
    if not isinstance(raw_text, str):
        return await preflight_refusal(400, "invalid_note", body)
    text = raw_text.strip()
    if not text or len(text) > 50_000:
        return await preflight_refusal(400, "empty note", body)
    return await execute_action('note', ticket_id, request, body, text,
                                _app_value("_GClient", _GClient), _app_value("_record_lesson", _record_lesson))


@router.get("/ticket/{ticket_id}/actions/{operation_id}")
async def get_action_status(ticket_id: int, operation_id: str, request: Request) -> JSONResponse:
    return await action_status(ticket_id, operation_id, request,
                               _app_value("_GClient", _GClient), _app_value("_record_lesson", _record_lesson))


@router.post("/ticket/{ticket_id}/rewrite")
async def action_rewrite(ticket_id: int, request: Request) -> JSONResponse:
    """Rewrite a draft through Hermes; this route never sends it."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "invalid_json_object"})
    if not await deps.database_function("dashboard_ticket_exists")(ticket_id):
        return JSONResponse(status_code=404, content={"error": "ticket_not_in_console"})
    for field in ("draft", "instruction", "message_text", "customer_name"):
        if field in body and not isinstance(body[field], str):
            return JSONResponse(status_code=400, content={"error": f"invalid_{field}"})
    draft = body.get("draft", "").strip()
    instruction = body.get("instruction", "").strip()
    customer_msg = body.get("message_text", "").strip()
    if not instruction:
        return JSONResponse(status_code=400, content={"error": "no instruction"})
    if max(len(draft), len(instruction), len(customer_msg)) > 50_000:
        return JSONResponse(status_code=413, content={"error": "rewrite_input_too_large"})

    if not actor(request):
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    # Customer context is loaded from the console's database. Browser text is
    # not authoritative knowledge and cannot become a learned fact.
    source_id = body.get("source_message_id")
    from ..db import Database
    rows = await Database(deps.get_db()).fetch(
        "SELECT message_text FROM parsed_messages WHERE ticket_id=? AND message_id=? AND is_customer_message=1",
        (ticket_id, str(source_id or "")), operation="rewrite_context")
    if not rows:
        return JSONResponse(status_code=404, content={"error": "source_message_not_in_console"})
    customer_msg = rows[0]["message_text"] or ""
    command = [_app_value("_HERMES_BIN", _HERMES_BIN)]
    profile = _app_value("_HERMES_PROFILE", _HERMES_PROFILE)
    if profile:
        command.extend(["-p", profile])
    if _app_value("_HERMES_IGNORE_RULES", _HERMES_IGNORE_RULES):
        command.append("--ignore-rules")
    toolsets = _app_value("_HERMES_REWRITE_TOOLSETS", _HERMES_REWRITE_TOOLSETS)
    if toolsets:
        command.extend(["-t", toolsets])
    # Model provider credentials may be needed, but shared customer-system
    # credentials are not passed into this draft-only subprocess.
    env = {key: value for key, value in _os.environ.items() if key in {
        "PATH", "LANG", "LC_ALL", "TERM", "OLLAMA_API_KEY", "OPENAI_API_KEY"}}
    env["HOME"] = _app_value("_HERMES_HOME", _HERMES_HOME)
    try:
        reply = await run_rewrite(command, env,
            store_name=_app_value("_SUPPORT_STORE_NAME", _SUPPORT_STORE_NAME),
            customer_message=customer_msg, draft=draft, instruction=instruction)
    except RewriteFailure as exc:
        return JSONResponse(status_code=exc.status, content={"error": exc.error})
    except Exception as exc:
        log_event(logger, "ERROR", "Rewrite failed", ticket_id=ticket_id, error_type=type(exc).__name__)
        return JSONResponse(status_code=502, content={"error": "rewrite_unavailable"})
    # Generated output is a candidate only. It is neither delivered nor approved
    # for knowledge promotion; an explicit later owner action supplies approval.
    return JSONResponse(content={"ok": True, "draft": reply})


@router.get("/learning")
async def learning_stats() -> JSONResponse:
    """Return the learning ledger used by the console."""
    return JSONResponse(content=_app_value("_ledger", _ledger)())
