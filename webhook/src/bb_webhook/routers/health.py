"""Cheap local liveness/readiness probes; no external model/provider calls."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from .. import deps
from ..db import Database
from ..result_auth import configured_secret

router = APIRouter()
_REQUIRED_TABLES = {"webhook_events", "parsed_messages", "job_queue", "ticket_results", "app_settings", "console_sessions"}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _age(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    except (TypeError, ValueError):
        return None


@router.get("/ready")
async def ready() -> JSONResponse:
    settings = deps.get_settings()
    checks = {"db": "unavailable", "schema": "unavailable", "gorgias_configured": bool(settings.gorgias_auth), "processor_result_configured": bool(configured_secret(settings))}
    diagnostics = {}
    try:
        path = settings.db_path_absolute
        if path.is_file():
            async with asyncio.timeout(3):
                db = Database(path)
                await db.fetch("SELECT 1", operation="readiness_query")
                checks["db"] = "ok"
                tables = await db.fetch("SELECT name FROM sqlite_master WHERE type='table'", operation="readiness_schema")
                if _REQUIRED_TABLES.issubset({row["name"] for row in tables}):
                    # These queries also validate critical columns, unlike merely
                    # confirming that the directory or table names exist.
                    rows = await db.fetch("SELECT status,COUNT(*) AS count,MIN(created_at) AS oldest_created,MIN(started_at) AS oldest_started FROM job_queue GROUP BY status", operation="readiness_queue")
                    await db.fetch("SELECT message_id,ticket_id FROM webhook_events LIMIT 0", operation="readiness_events")
                    await db.fetch("SELECT message_id,ticket_id FROM parsed_messages LIMIT 0", operation="readiness_messages")
                    await db.fetch("SELECT message_id,ticket_id,draft_text FROM ticket_results LIMIT 0", operation="readiness_results")
                    await db.fetch("SELECT token_id,revoked_at,expires_at FROM console_sessions LIMIT 0", operation="readiness_sessions")
                    checks["schema"] = "ok"
                    by_status = {row["status"]: row for row in rows}
                    diagnostics = {
                        "pending_jobs": int(by_status["pending"]["count"]) if "pending" in by_status else 0,
                        "processing_jobs": int(by_status["processing"]["count"]) if "processing" in by_status else 0,
                        "failed_jobs": int(by_status["failed"]["count"]) if "failed" in by_status else 0,
                        "oldest_pending_seconds": _age(by_status["pending"]["oldest_created"]) if "pending" in by_status else None,
                        "oldest_processing_seconds": _age(by_status["processing"]["oldest_started"]) if "processing" in by_status else None,
                    }
    except Exception:
        # Never echo DB paths, credentials or provider errors from a public probe.
        checks["db"] = "unavailable"
        checks["schema"] = "unavailable"
    ready = checks["db"] == "ok" and checks["schema"] == "ok" and checks["gorgias_configured"] and checks["processor_result_configured"]
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ready" if ready else "not_ready", "checks": checks, "diagnostics": diagnostics})
