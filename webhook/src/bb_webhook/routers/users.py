"""Admin-only console user management."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import deps, user_store
from ..console_auth import hash_password
from ..console_actions import actor
from ..middleware.console_session import trusted_origin

router = APIRouter(prefix="/dashboard/api/users")


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
    except Exception:
        return _error(503, "users_unavailable")
    return JSONResponse(content={"ok": True, "users": users})


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
        hashed = hash_password(password)
        created = await user_store.create_user(
            deps.get_settings().db_path_absolute,
            username=username,
            password_hash=hashed,
            role=role,
            gorgias_email=email,
        )
    except ValueError as exc:
        code = str(exc) or "invalid_user"
        status = 409 if code == "username_taken" else 400
        return _error(status, code)
    except Exception:
        return _error(503, "users_unavailable")
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
    hashed = hash_password(password) if isinstance(password, str) else None
    try:
        updated = await user_store.update_user(
            deps.get_settings().db_path_absolute,
            username,
            role=role,
            gorgias_email=email,
            password_hash=hashed,
            disabled=disabled,
        )
    except ValueError as exc:
        code = str(exc) or "invalid_user"
        status = 404 if code == "not_found" else 409 if code == "last_admin" else 400
        return _error(status, code)
    except Exception:
        return _error(503, "users_unavailable")
    return JSONResponse(content={"ok": True, "user": updated})
