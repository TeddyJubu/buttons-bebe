"""Cookie-backed authentication for the human support console."""

from __future__ import annotations

import hmac
import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .. import session_store
from ..config import get_settings
from ..password_executor import verify_bounded, PasswordVerifierBusy
from ..middleware.console_session import resolve_identity, trusted_origin, UNSAFE_METHODS
from ..console_auth import (
    build_session_token,
    safe_next_path,
    session_claims,
)

router = APIRouter()

_COOKIE_NAME = "bb_console_session"
_LOGIN_MAX_REQUESTS_PER_MINUTE = 12
_LOGIN_COOKIE_TTL_SECONDS = 12 * 60 * 60


def _client_ip(request: Request) -> str:
    """Use the proxy-provided client address, with a safe local fallback."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded and len(forwarded) <= 128:
        return forwarded
    return request.client.host if request.client else "unknown"


def _login_allowed(request: Request) -> bool:
    # Importing the existing limiter keeps login attempts bounded by the same
    # process-wide, deterministic mechanism used by the webhook receiver.
    from ..middleware.rate_limit import _check_rate_limit

    return _check_rate_limit(_client_ip(request), _LOGIN_MAX_REQUESTS_PER_MINUTE)


async def _session_username(request: Request) -> str | None:
    identity = await resolve_identity(request)
    return identity["username"] if identity else None


def _invalid_origin() -> JSONResponse:
    return JSONResponse(status_code=403, content={"error": "invalid_origin"})


def _forwarded_mutation(request: Request) -> bool:
    return request.headers.get("x-forwarded-method", request.method).upper() in UNSAFE_METHODS


async def _checked_username(request: Request):
    try:
        return await _session_username(request)
    except Exception:
        return JSONResponse(status_code=503, content={"error": "authentication_unavailable"})


def _auth_unconfigured() -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": "console_auth_unconfigured"})


@router.post("/auth/login")
async def auth_login(request: Request) -> JSONResponse:
    """Validate the form credentials and issue an HttpOnly session cookie."""
    if not trusted_origin(request):
        return _invalid_origin()
    if not _login_allowed(request):
        return JSONResponse(status_code=429, content={"error": "too_many_attempts"})
    settings = get_settings()
    if not settings.console_password_hash or not settings.console_session_secret:
        return _auth_unconfigured()
    try:
        raw = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 16384:
                    return JSONResponse(status_code=413, content={"error": "body_too_large"})
        import json
        body = json.loads(raw)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "invalid_json_object"})

    username = body.get("username", "")
    password = body.get("password", "")
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 128 or len(password) > 1024:
        return JSONResponse(status_code=400, content={"error": "invalid_credentials"})
    if not hmac.compare_digest(username.strip().encode(), settings.console_username.encode()):
        return JSONResponse(status_code=401, content={"error": "invalid_credentials"})
    try:
        verified = await verify_bounded(password, settings.console_password_hash)
    except PasswordVerifierBusy:
        return JSONResponse(status_code=429, content={"error": "login_busy"}, headers={"Retry-After": "1"})
    except Exception:
        return JSONResponse(status_code=503, content={"error": "authentication_unavailable"})
    if not verified:
        return JSONResponse(status_code=401, content={"error": "invalid_credentials"})

    redirect = safe_next_path(body.get("next"))
    token = build_session_token(username.strip(), settings.console_session_secret)
    claims = session_claims(token, settings.console_session_secret)
    try:
        await session_store.register(claims, settings.db_path_absolute)
    except Exception:
        return JSONResponse(status_code=503, content={"error": "authentication_unavailable"})
    response = JSONResponse(content={"ok": True, "redirect": redirect})
    response.set_cookie(
        _COOKIE_NAME,
        token,
        max_age=_LOGIN_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=not settings.demo_mode,
        samesite="strict",
        path="/",
    )
    return response


@router.get("/auth/session")
async def auth_session(request: Request) -> JSONResponse:
    username = await _checked_username(request)
    if isinstance(username, Response):
        return username
    if username is None:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    return JSONResponse(content={"authenticated": True, "username": username})


@router.get("/auth/check")
async def auth_check(request: Request) -> Response:
    """Small forward-auth target used by Caddy for API and admin routes."""
    username = await _checked_username(request)
    if isinstance(username, Response):
        return username
    if username is None:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    if _forwarded_mutation(request) and not trusted_origin(request):
        return _invalid_origin()
    return Response(status_code=204, headers={"X-Authenticated-Actor": "owner:" + username})


@router.get("/auth/page-check")
async def auth_page_check(request: Request) -> Response:
    """Redirect unauthenticated browser navigation to the standalone login."""
    username = await _checked_username(request)
    if isinstance(username, Response):
        return username
    original = request.headers.get("x-forwarded-uri", "/console/")
    if username is None:
        if "/api/" in original.split("?", 1)[0] or _forwarded_mutation(request):
            return JSONResponse(status_code=401, content={"error": "not_authenticated"})
        destination = safe_next_path(original)
        location = "/console/login?next=" + quote(destination, safe="")
        return RedirectResponse(url=location, status_code=302)
    if _forwarded_mutation(request) and not trusted_origin(request):
        return _invalid_origin()
    return Response(status_code=204, headers={"X-Authenticated-Actor": "owner:" + username})


@router.post("/auth/logout")
async def auth_logout(request: Request) -> Response:
    if not trusted_origin(request):
        return _invalid_origin()
    settings = get_settings()
    claims = session_claims(request.cookies.get(_COOKIE_NAME), settings.console_session_secret)
    if claims and claims.username == settings.console_username:
        try:
            await session_store.revoke(claims, settings.db_path_absolute)
        except Exception:
            # Do not claim logout if a copied cookie remains usable.
            return JSONResponse(status_code=503, content={"error": "authentication_unavailable"})
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(_COOKIE_NAME, path="/")
    return response
