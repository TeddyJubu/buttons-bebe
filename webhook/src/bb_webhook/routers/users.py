"""Admin-only console user management."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import deps, user_store
from ..console_auth import hash_password
from ..console_actions import actor
from ..logging_utils import get_logger, log_event
from ..middleware.console_session import trusted_origin

router = APIRouter(prefix="/dashboard/api/users")
_AUDIT_PAGE = 100
logger = get_logger(__name__)


def _require_admin(request: Request) -> str | JSONResponse:
    if getattr(request.state, "actor_role", None) != "admin":
        return JSONResponse(status_code=403, content={"error": "console_admin_required"})
    reviewer = actor(request)
    if not reviewer:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    return reviewer


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


@router.get("")
async def list_console_users(request: Request) -> JSONResponse:
    gate = _require_admin(request)
    if isinstance(gate, JSONResponse):
        return gate
    try:
        users = await user_store.list_users(deps.get_settings().db_path_absolute)
    except Exception as exc:
        log_event(logger, "ERROR", "Console user list unavailable", error_type=type(exc).__name__)
        return _error(503, "users_unavailable")
    return JSONResponse(content={"ok": True, "users": users})


@router.get("/audit")
async def list_console_user_audit(request: Request) -> JSONResponse:
    gate = _require_admin(request)
    if isinstance(gate, JSONResponse):
        return gate
    raw_before = request.query_params.get("before")
    before = None
    if raw_before is not None:
        if not raw_before.isdigit() or len(raw_before) > 18:
            return _error(400, "invalid_cursor")
        before = int(raw_before)
    try:
        entries = await user_store.list_audit(
            deps.get_settings().db_path_absolute, limit=_AUDIT_PAGE + 1, before=before
        )
    except Exception as exc:
        log_event(logger, "ERROR", "Console user audit unavailable", error_type=type(exc).__name__)
        return _error(503, "users_unavailable")
    # Fetch one extra row to know whether an older page exists without a COUNT.
    more = len(entries) > _AUDIT_PAGE
    entries = entries[:_AUDIT_PAGE]
    return JSONResponse(content={
        "ok": True,
        "entries": entries,
        "nextBefore": entries[-1]["id"] if more else None,
    })


@router.post("")
async def create_console_user(request: Request) -> JSONResponse:
    gate = _require_admin(request)
    if isinstance(gate, JSONResponse):
        return gate
    if not trusted_origin(request):
        return _error(403, "invalid_origin")
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid_json")
    if not isinstance(body, dict):
        return _error(400, "invalid_json_object")
    username = body.get("username")
    password = body.get("password")
    role = body.get("role", "staff")
    email = body.get("gorgiasEmail", "")
    if not isinstance(username, str) or not isinstance(password, str) or not isinstance(role, str):
        return _error(400, "invalid_user")
    if not isinstance(email, str):
        return _error(400, "invalid_email")
    if len(password) < 10 or len(password) > 1024:
        return _error(400, "invalid_password")
    try:
        hashed = await asyncio.to_thread(hash_password, password)
        created = await user_store.create_user(
            deps.get_settings().db_path_absolute,
            username=username,
            password_hash=hashed,
            role=role,
            gorgias_email=email,
            actor=gate,
        )
    except ValueError as exc:
        code = str(exc) or "invalid_user"
        status = 409 if code == "username_taken" else 400
        return _error(status, code)
    except Exception as exc:
        log_event(logger, "ERROR", "Console user create unavailable",
                  actor_id=gate, error_type=type(exc).__name__)
        return _error(503, "users_unavailable")
    log_event(logger, "INFO", "Console user created", actor_id=gate,
              username=created["username"], role=created["role"])
    return JSONResponse(status_code=201, content={"ok": True, "user": created})


@router.patch("/{username}")
async def update_console_user(username: str, request: Request) -> JSONResponse:
    gate = _require_admin(request)
    if isinstance(gate, JSONResponse):
        return gate
    if not trusted_origin(request):
        return _error(403, "invalid_origin")
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid_json")
    if not isinstance(body, dict) or not body:
        return _error(400, "invalid_json_object")
    for field, code in (
        ("role", "invalid_role"),
        ("gorgiasEmail", "invalid_email"),
        ("disabled", "invalid_disabled"),
        ("password", "invalid_password"),
    ):
        if field in body and body[field] is None:
            return _error(400, code)
    role = body.get("role") if "role" in body else None
    email = body.get("gorgiasEmail") if "gorgiasEmail" in body else None
    disabled = body.get("disabled") if "disabled" in body else None
    password = body.get("password") if "password" in body else None
    if role is not None and not isinstance(role, str):
        return _error(400, "invalid_role")
    if email is not None and not isinstance(email, str):
        return _error(400, "invalid_email")
    if disabled is not None and type(disabled) is not bool:
        return _error(400, "invalid_disabled")
    if password is not None:
        if not isinstance(password, str) or len(password) < 10 or len(password) > 1024:
            return _error(400, "invalid_password")
    try:
        hashed = await asyncio.to_thread(hash_password, password) if isinstance(password, str) else None
        updated = await user_store.update_user(
            deps.get_settings().db_path_absolute,
            username,
            role=role,
            gorgias_email=email,
            password_hash=hashed,
            disabled=disabled,
            actor=gate,
        )
    except ValueError as exc:
        code = str(exc) or "invalid_user"
        status = 404 if code == "not_found" else 409 if code == "last_admin" else 400
        return _error(status, code)
    except Exception as exc:
        log_event(logger, "ERROR", "Console user update unavailable",
                  actor_id=gate, username=username, error_type=type(exc).__name__)
        return _error(503, "users_unavailable")
    log_event(logger, "INFO", "Console user updated", actor_id=gate, username=updated["username"],
              role=updated["role"], disabled=updated["disabled"],
              password_reset=password is not None, email_set="gorgiasEmail" in body)
    return JSONResponse(content={"ok": True, "user": updated})
