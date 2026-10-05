"""Defense in depth behind Caddy; only processor results are loopback-only."""
from __future__ import annotations

import ipaddress
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
from .. import session_store
from ..config import get_settings
from ..console_auth import session_claims
from ..result_auth import configured_secret, authorized

COOKIE_NAME = "bb_console_session"
TRUSTED_ORIGINS = frozenset({"https://support.buttonsbebe.com", "https://srv1766050.hstgr.cloud"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def trusted_origin(request) -> bool:
    """Exact configured public origins; Host/Forwarded are not trust sources."""
    return request.headers.get("origin", "") in TRUSTED_ORIGINS and request.headers.get("sec-fetch-site", "same-origin") != "cross-site"


def direct_loopback(request) -> bool:
    try:
        local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:
        local = False
    forwarded = any(name.lower().startswith("x-forwarded-") or name.lower() == "forwarded" for name in request.headers)
    return local and not forwarded and not request.headers.get("origin")


async def resolve_identity(request) -> dict | None:
    settings = get_settings()
    claims = session_claims(request.cookies.get(COOKIE_NAME), settings.console_session_secret)
    if claims is None or claims.username != settings.console_username:
        return None
    return await session_store.authenticate(claims, settings.db_path_absolute)


class ConsoleSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/dashboard/api/results":
            if not direct_loopback(request):
                return JSONResponse({"error": "internal_endpoint"}, status_code=403)
            secret = configured_secret(get_settings())
            if not secret:
                return JSONResponse({"error": "result_authentication_unavailable"}, status_code=503)
            if not authorized(request.headers.get("authorization", ""), secret):
                return JSONResponse({"error": "not_authenticated"}, status_code=401)
            request.state.actor_id = "processor"
            request.state.actor_role = "processor"
            return await call_next(request)
        protected = path == "/dashboard/api" or path.startswith("/dashboard/api/")
        if protected:
            try:
                identity = await resolve_identity(request)
            except Exception:
                return JSONResponse({"error": "authentication_unavailable"}, status_code=503)
            if not identity:
                return JSONResponse({"error": "not_authenticated"}, status_code=401)
            if request.method in UNSAFE_METHODS and not trusted_origin(request):
                return JSONResponse({"error": "invalid_origin"}, status_code=403)
            for key, value in identity.items():
                setattr(request.state, key, value)
        response = await call_next(request)
        if protected or path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Cookie"
        return response
