"""Gorgias write-back client — posts internal notes and updates tickets.

Uses the Gorgias REST API to write data back to tickets:
  - Post internal notes (draft replies, escalation notes)
  - Add tags to tickets (e.g. "escalated", "ai-drafted")
  - Update ticket status

This is the WRITE side of the Gorgias integration. The webhook's
gorgias_client.py is the READ side.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from config import get_settings
from logging_setup import get_logger, log_event

logger = get_logger(__name__)

_TIMEOUT = 15.0


class GorgiasWriter:
    """Write-side Gorgias API client for posting notes and updating tickets."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.gorgias_base_url
        self._auth = settings.gorgias_auth

    def _check_auth(self) -> bool:
        if not self._auth:
            log_event(logger, "ERROR", "Gorgias credentials not configured")
            return False
        return True

    def post_internal_note(
        self,
        ticket_id: int,
        body_text: str,
    ) -> dict[str, Any] | None:
        """Post an internal note to a ticket.

        Internal notes are NOT sent to the customer — they are for
        staff review only. This is what the AI agent uses to post drafts.
        """
        if not self._check_auth():
            return None

        url = f"{self.base_url}/api/tickets/{ticket_id}/messages"

        # Gorgias requires channel="internal-note" (not "internal") and
        # a sender with an "id" field (not just email). We use the API
        # user's email to look up their sender ID at runtime.
        sender = {"email": get_settings().gorgias_api_email}

        payload = {
            "channel": "internal-note",
            "source": "api",
            "via": "api",
            "from_agent": True,
            "action": "internal_note",
            "body_text": body_text,
            "public": False,
            "sender": sender,
        }

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(url, auth=self._auth, json=payload)

                if resp.status_code == 401:
                    log_event(logger, "ERROR", "Gorgias auth failed",
                              ticket_id=ticket_id)
                    return None
                if resp.status_code == 404:
                    log_event(logger, "WARNING", "Ticket not found",
                              ticket_id=ticket_id)
                    return None
                if resp.status_code == 400:
                    # Try with body_html as fallback (some Gorgias versions
                    # require body_html instead of body_text for internal notes)
                    payload_fallback = dict(payload)
                    payload_fallback.pop("body_text", None)
                    payload_fallback["body_html"] = body_text.replace("\n", "<br>")
                    resp = client.post(url, auth=self._auth, json=payload_fallback)
                    if resp.status_code in (200, 201):
                        result = resp.json()
                        log_event(logger, "INFO", "Internal note posted (body_html fallback)",
                                  ticket_id=ticket_id,
                                  message_id=result.get("id"))
                        return result
                    log_event(logger, "ERROR", f"Gorgias API error {resp.status_code}",
                              ticket_id=ticket_id,
                              body=resp.text[:300])
                    return None
                resp.raise_for_status()
                result = resp.json()
                log_event(logger, "INFO", "Internal note posted",
                          ticket_id=ticket_id,
                          message_id=result.get("id"))
                return result

        except httpx.HTTPStatusError as exc:
            log_event(logger, "ERROR", f"Gorgias API error {exc.response.status_code}",
                      ticket_id=ticket_id)
            return None
        except httpx.RequestError as exc:
            log_event(logger, "ERROR", f"Gorgias request failed: {exc}",
                      ticket_id=ticket_id)
            return None

    def add_tags(
        self,
        ticket_id: int,
        tags: list[str],
    ) -> bool:
        """Add tags to a ticket (e.g. 'escalated', 'ai-drafted')."""
        if not self._check_auth():
            return False

        url = f"{self.base_url}/api/tickets/{ticket_id}"

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                # First get existing tags
                resp = client.get(url, auth=self._auth)
                if resp.status_code != 200:
                    log_event(logger, "ERROR", "Failed to fetch ticket for tag update",
                              ticket_id=ticket_id,
                              status=resp.status_code)
                    return False

                existing = resp.json().get("tags", [])
                # Gorgias can return tags as a list of dicts (e.g.
                # [{"name": "escalated", "id": 123}]) rather than plain
                # strings.  Normalize to strings so set() doesn't fail
                # with "TypeError: unhashable type: 'dict'".
                existing_strs = [
                    t.get("name", str(t)) if isinstance(t, dict) else str(t)
                    for t in existing
                ]
                tags_strs = [str(t) for t in tags]
                merged = sorted(set(existing_strs + tags_strs))

                resp = client.put(url, auth=self._auth, json={"tags": merged})
                resp.raise_for_status()

                log_event(logger, "INFO", "Tags added to ticket",
                          ticket_id=ticket_id,
                          tags=tags,
                          all_tags=merged)
                return True

        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            log_event(logger, "ERROR", f"Failed to add tags: {exc}",
                      ticket_id=ticket_id)
            return False

    def assign_ticket(
        self,
        ticket_id: int,
        assignee_user_id: int,
    ) -> bool:
        """Assign a ticket to a specific user."""
        if not self._check_auth():
            return False

        url = f"{self.base_url}/api/tickets/{ticket_id}"

        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.put(url, auth=self._auth,
                                  json={"assignee_user_id": assignee_user_id})
                resp.raise_for_status()
                log_event(logger, "INFO", "Ticket assigned",
                          ticket_id=ticket_id,
                          assignee=assignee_user_id)
                return True
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            log_event(logger, "ERROR", f"Failed to assign ticket: {exc}",
                      ticket_id=ticket_id)
            return False