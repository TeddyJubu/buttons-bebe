"""Isolated production inbox ASGI service. Authentication belongs to Caddy.

No credentials are loaded here. Shopify customer/order lookup is a local snapshot
written by a separate exporter. Send remains unconditionally locked.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import sys

INBOX = Path(__file__).resolve().parent
sys.path.insert(0, str(INBOX))
sys.path.insert(0, str(INBOX.parent / "helpdesk-agent"))
for name in ("SHOPIFY_MUTATIONS_ENABLED", "HELPDESK_OUTBOUND_ENABLED", "GORGIAS_BRIDGE_ENABLED"):
    os.environ[name] = "0"
os.environ["HELPDESK_PRODUCTION"] = "1"
os.umask(0o077)
os.environ.setdefault("HELPDESK_DB_FILE", "/var/lib/buttonsbebe-inbox/inbox.sqlite3")
for name in ("HELPDESK_STORE_FILE", "HELPDESK_SEEN_FILE"):
    os.environ.pop(name, None)
# Legacy files must be migrated explicitly before starting this low-privilege
# service. It must never try to traverse /root or load the application's .env.
from fastapi import FastAPI, Request  # noqa: E402
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError  # noqa: E402
from helpdesk.http import handle_http  # noqa: E402
from helpdesk import tickets  # noqa: E402
from helpdesk.send_access import ACTIVATE_SEND_MESSAGE, SEND_ACCESS_ERROR  # noqa: E402
from projection import query as projection_query, ProjectionUnavailable
from helpdesk.state_store import StoreUnavailable  # noqa: E402

MAX_BODY = 1024 * 1024
CAPABILITIES = {
    "listTickets": True, "getTicket": True, "sendReply": False,
    "draftReply": False, "summarizeThread": False, "searchMacros": False,
    "applyMacro": False, "escalateTicket": False, "markPrivacyHandled": False,
    "markUnsubscribed": False, "markBugHandled": False, "customerDetails": False,
    "intake": False,
}
STATIC_FILES = frozenset(json.loads((INBOX / "static-manifest.json").read_text()))


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: StrictStr = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListArguments(Arguments):
    view: StrictStr = Field(default="open", max_length=30)
    limit: StrictInt = Field(default=20, ge=1, le=100)
    offset: StrictInt = Field(default=0, ge=0, le=10000)


class TicketArguments(Arguments):
    ticketId: StrictStr = Field(min_length=1, max_length=200)


SCHEMAS = {
    "helpdesk.list_tickets": ListArguments,
    "helpdesk.get_ticket": TicketArguments,
    "helpdesk.capabilities": Arguments,
    "helpdesk.projection_status": Arguments,
    "helpdesk.write_gate_status": Arguments,
    "helpdesk.bridge_status": Arguments,
}


@asynccontextmanager
async def lifespan(app):
    # Fail startup on corrupt/unwritable state. Never report an empty store.
    with tickets.transaction():
        pass
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def error(status, code, message):
    return JSONResponse({"ok": False, "error": code, "message": message}, status_code=status)


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://cdn.shopify.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


@app.post("/webhook/gorgias")
@app.post("/webhook/gorgias/{rest:path}")
async def disabled_bridge(rest: str = ""):
    return JSONResponse({"ok": False, "status": "bridge_disabled", "message": ACTIVATE_SEND_MESSAGE}, status_code=503)


@app.get("/health")
async def health():
    return {"ok": True}


def _storage_read_check():
    with tickets.transaction(write=False):
        pass


@app.get("/ready")
async def ready():
    try:
        await run_in_threadpool(_storage_read_check)
        projection = await run_in_threadpool(projection_query, "helpdesk.projection_status", {})
        if projection["projection"]["stale"]:
            return error(503, "projection_stale", "Observed ticket history is stale.")
    except Exception:
        return error(503, "storage_unavailable", "Inbox storage is unavailable.")
    return {"ok": True, "sendAccessEnabled": False}


@app.post("/console/api/helpdesk")
async def invoke(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return error(415, "unsupported_media_type", "Use application/json.")
    try:
        # Stream and bound actual bytes, rather than trusting Content-Length.
        raw = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_BODY:
                    return error(413, "body_too_large", "Request body is too large.")
        invocation = Invocation.model_validate_json(bytes(raw))
        if invocation.tool == "helpdesk.send_reply":
            # The lock wins even when callers submit extra send arguments.
            return {"ok": False, "error": SEND_ACCESS_ERROR, "message": ACTIVATE_SEND_MESSAGE}
        schema = SCHEMAS.get(invocation.tool)
        if schema is None:
            return error(403, "capability_unavailable", "This action is not available in this inbox.")
        args = schema.model_validate(invocation.arguments).model_dump()
        if invocation.tool == "helpdesk.capabilities":
            return {"ok": True, "capabilities": CAPABILITIES}
        if invocation.tool in {"helpdesk.list_tickets", "helpdesk.get_ticket", "helpdesk.projection_status"}:
            return await run_in_threadpool(projection_query, invocation.tool, args)
        # All exposed operations are bounded local state operations; external
        # providers and model execution are absent from SCHEMAS.
        return await run_in_threadpool(handle_http, invocation.tool, args, actor="human")
    except (ValidationError, ValueError, UnicodeError):
        return error(400, "invalid_request", "Expected a valid tool name and arguments object.")
    except TimeoutError:
        return error(408, "request_timeout", "Request body timed out.")
    except ProjectionUnavailable:
        return error(503, "projection_unavailable", "Observed ticket history is unavailable. Existing records have not been reset.")
    except StoreUnavailable:
        return error(503, "storage_unavailable", "Inbox storage is unavailable. Existing data has not been reset.")
    except Exception:
        logging.getLogger("inbox").error("Inbox invocation failed; request contents omitted")
        return error(500, "internal_error", "Inbox request could not be completed.")


@app.api_route("/{asset:path}", methods=["GET", "HEAD"])
async def static(asset: str):
    name = asset or "index.html"
    if name not in STATIC_FILES:
        return error(404, "not_found", "Not found.")
    path = INBOX / name
    if not path.is_file() or path.resolve() != path.absolute():
        return error(404, "not_found", "Not found.")
    return FileResponse(path)


def main(host="127.0.0.1", port=8766):
    if host != "127.0.0.1":
        raise SystemExit("Inbox must bind to 127.0.0.1")
    import uvicorn
    uvicorn.run(app, host=host, port=port, workers=1, limit_concurrency=32,
                timeout_keep_alive=5, access_log=False, server_header=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Isolated inbox service")
    parser.add_argument("--host", default=os.environ.get("INBOX_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("INBOX_PORT", "8766")))
    args = parser.parse_args()
    main(args.host, args.port)
