"""Persistent dashboard notification state and alert feed."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import deps
from ..notifications import dashboard_notifications

router = APIRouter(prefix="/dashboard/api")
_NOTIFICATION_READ_STATE_KEY = "console_notification_read_state_v1"


def _read_notification_state(raw_state: str) -> dict[str, str]:
    """Read the bounded, server-side acknowledgement map safely."""
    try:
        data = json.loads(raw_state)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(notification_id): str(read_at)
        for notification_id, read_at in data.items()
        if isinstance(notification_id, str) and isinstance(read_at, str)
    }


async def _current_notifications() -> tuple[list[dict[str, Any]], dict[str, str]]:
    tickets = await deps.database_function("get_dashboard_tickets")(limit=100)
    notifications = dashboard_notifications(tickets)
    read_state = _read_notification_state(
        await deps.database_function("get_setting")(_NOTIFICATION_READ_STATE_KEY, "{}")
    )
    # ids gained a :{ticket_id} suffix to stay unique per ticket; an ack
    # recorded under the old unsuffixed key still covers the message, so honor
    # it rather than resurrecting every previously-read alert.
    legacy_read = {
        tuple(notification_id.split(":", 1))
        for notification_id in read_state
        if notification_id.startswith(("failed:", "review:"))
        and notification_id.count(":") == 1
    }
    for notification in notifications:
        notification["read"] = (
            notification["id"] in read_state
            or (notification["kind"], notification["message_id"]) in legacy_read
        )
    return notifications, read_state


@router.get("/notifications")
async def dashboard_notifications_api() -> JSONResponse:
    """Return current ticket alerts plus their persistent read state."""
    notifications, _ = await _current_notifications()
    unread_count = sum(not notification["read"] for notification in notifications)
    return JSONResponse(
        content={
            "notifications": notifications,
            "unread_count": unread_count,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.post("/notifications/read")
async def mark_dashboard_notifications_read(request: Request) -> JSONResponse:
    """Acknowledge current alerts without changing the underlying tickets."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    notifications, read_state = await _current_notifications()
    active_ids = {notification["id"] for notification in notifications}
    requested_ids = body.get("ids") if isinstance(body, dict) else None
    if isinstance(body, dict) and body.get("all") is True:
        ids_to_mark = active_ids
    elif isinstance(requested_ids, list):
        ids_to_mark = {
            notification_id
            for notification_id in requested_ids
            if isinstance(notification_id, str) and notification_id in active_ids
        }
    else:
        return JSONResponse(status_code=400, content={"error": "ids_or_all_required"})

    now = datetime.now(timezone.utc).isoformat()
    next_read_state = {
        notification_id: read_at
        for notification_id, read_at in read_state.items()
        if notification_id in active_ids
    }
    for notification in notifications:
        if notification["read"] and notification["id"] not in next_read_state:
            # read only via a legacy unsuffixed key, which the prune above
            # drops: migrate the ack to the new id so it survives.
            next_read_state[notification["id"]] = now
    for notification_id in ids_to_mark:
        next_read_state[notification_id] = now
    await deps.database_function("set_setting")(
        _NOTIFICATION_READ_STATE_KEY,
        json.dumps(next_read_state, separators=(",", ":")),
    )
    unread_count = sum(
        notification["id"] not in next_read_state for notification in notifications
    )
    return JSONResponse(content={"ok": True, "unread_count": unread_count})
