"""Build the console's live, actionable notification feed.

Notifications are intentionally derived from the current ticket state rather
than written as a second, stale event log.  Once a ticket is no longer failed
or requires review, it naturally disappears from the feed.
"""

from __future__ import annotations

from typing import Any


_REVIEW_PRIORITIES = {"critical", "high"}


def dashboard_notifications(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the current human-action items for dashboard ticket rows.

    A failed processor job takes precedence over a risk-review item for the
    same message: fixing the failed job is the immediate next action.
    """
    notifications: list[dict[str, Any]] = []

    for ticket in tickets:
        message_id = str(ticket.get("message_id") or ticket.get("ticket_id") or "")
        if not message_id:
            continue

        job_status = str(ticket.get("job_status") or "").lower()
        priority = str(ticket.get("priority") or "").lower()
        action = str(ticket.get("action") or "").lower()
        occurred_at = (
            ticket.get("job_finished_at")
            or ticket.get("processed_at")
            or ticket.get("received_at")
            or ticket.get("created_at")
            or ""
        )
        subject = str(ticket.get("ticket_subject") or "(no subject)")
        customer = str(ticket.get("customer_email") or "Customer")

        if job_status == "failed":
            notifications.append({
                "id": f"failed:{message_id}",
                "kind": "failed",
                "severity": "error",
                "title": "Ticket processing failed",
                "detail": str(ticket.get("reason") or subject),
                "ticket_id": ticket.get("ticket_id"),
                "message_id": message_id,
                "subject": subject,
                "customer": customer,
                "occurred_at": occurred_at,
                "filter": "failed",
            })
            continue

        if "escal" in action or priority in _REVIEW_PRIORITIES:
            title = (
                "Sensitive ticket needs review"
                if "escal" in action
                else "High-priority ticket needs review"
            )
            notifications.append({
                "id": f"review:{message_id}",
                "kind": "review",
                "severity": "warning",
                "title": title,
                "detail": str(ticket.get("reason") or subject),
                "ticket_id": ticket.get("ticket_id"),
                "message_id": message_id,
                "subject": subject,
                "customer": customer,
                "occurred_at": occurred_at,
                "filter": "escalated",
            })

    return sorted(
        notifications,
        key=lambda notification: str(notification["occurred_at"]),
        reverse=True,
    )
