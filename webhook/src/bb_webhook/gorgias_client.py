"""Gorgias API client — fetch ticket + message data using Basic Auth."""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_settings
from .logging_utils import get_logger, log_event

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────
_API_VERSION = "/api"
_TIMEOUT = 15.0  # seconds


class GorgiasClient:
    """Thin async wrapper around the Gorgias REST API."""

    def __init__(
        self,
        subdomain: str | None = None,
        email: str | None = None,
        api_key: str | None = None,
    ):
        settings = get_settings()
        self.subdomain = subdomain or settings.gorgias_subdomain
        self.email = email or settings.gorgias_api_email
        self.api_key = api_key or settings.gorgias_api_key
        self.base_url = f"https://{self.subdomain}.gorgias.com"
        self._auth = (self.email, self.api_key) if self.email and self.api_key else None

    # ── Public methods ─────────────────────────────────────

    async def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        """Fetch a full ticket with all messages and customer info."""
        if not self._auth:
            log_event(logger, "ERROR", "Gorgias credentials not configured")
            return None

        url = f"{self.base_url}{_API_VERSION}/tickets/{ticket_id}"
        params = {"per_page": 50}  # max messages to retrieve

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, auth=self._auth, params=params)

                if resp.status_code == 401:
                    log_event(logger, "ERROR", "Gorgias auth failed — check API key",
                              ticket_id=ticket_id)
                    return None
                if resp.status_code == 404:
                    log_event(logger, "WARNING", "Ticket not found",
                              ticket_id=ticket_id)
                    return None
                resp.raise_for_status()
                return resp.json()

        except httpx.HTTPStatusError as exc:
            log_event(logger, "ERROR", f"Gorgias API error {exc.response.status_code}",
                      ticket_id=ticket_id)
            return None
        except httpx.RequestError as exc:
            log_event(logger, "ERROR", f"Gorgias request failed: {exc}",
                      ticket_id=ticket_id)
            return None

    async def get_message(self, message_id: int) -> dict[str, Any] | None:
        """Fetch a single message by ID."""
        if not self._auth:
            return None

        url = f"{self.base_url}{_API_VERSION}/messages/{message_id}"

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, auth=self._auth)
                if resp.status_code in (401, 404):
                    log_event(logger, "WARNING", f"Message {message_id} not found")
                    return None
                resp.raise_for_status()
                return resp.json()

        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            log_event(logger, "ERROR", f"Failed to fetch message {message_id}: {exc}")
            return None

    async def get_customer(self, customer_id: int) -> dict[str, Any] | None:
        """Fetch customer details."""
        if not self._auth:
            return None

        url = f"{self.base_url}{_API_VERSION}/customers/{customer_id}"

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, auth=self._auth)
                if resp.status_code in (401, 404):
                    return None
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError):
            return None

    async def test_connection(self) -> bool:
        """Quick connectivity test — returns True if auth works."""
        if not self._auth:
            return False

        url = f"{self.base_url}{_API_VERSION}/tickets?per_page=1"

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, auth=self._auth)
                ok = resp.status_code == 200
                log_event(logger, "INFO", f"Gorgias connection test: {'OK' if ok else 'FAIL'}",
                          status_code=resp.status_code)
                return ok
        except Exception as exc:
            log_event(logger, "ERROR", f"Gorgias connection test failed: {exc}")
            return False

    # ── WRITE side (added for reply-from-dashboard) ────────

    async def _post_message(self, ticket_id: int, payload: dict) -> dict:
        """Low-level POST of a message to a ticket. Returns {ok, ...}."""
        if not self._auth:
            return {"ok": False, "error": "gorgias credentials not configured"}
        url = f"{self.base_url}{_API_VERSION}/tickets/{ticket_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, auth=self._auth, json=payload)
                if resp.status_code in (200, 201):
                    return {"ok": True, "message": resp.json()}
                if resp.status_code == 400 and "body_text" in payload:
                    p2 = dict(payload)
                    txt = p2.pop("body_text")
                    p2["body_html"] = txt.replace("\n", "<br>")
                    resp2 = await client.post(url, auth=self._auth, json=p2)
                    if resp2.status_code in (200, 201):
                        return {"ok": True, "message": resp2.json()}
                    return {"ok": False, "error": f"gorgias {resp2.status_code}: {resp2.text[:300]}"}
                return {"ok": False, "error": f"gorgias {resp.status_code}: {resp.text[:300]}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def post_internal_note(self, ticket_id: int, body_text: str) -> dict:
        """Post a staff-only internal note (not sent to the customer)."""
        payload = {
            "channel": "internal-note",
            "via": "api",
            "from_agent": True,
            "body_text": body_text,
            "public": False,
            "sender": {"email": self.email},
        }
        return await self._post_message(ticket_id, payload)

    async def send_public_reply(self, ticket_id: int, body_text: str) -> dict:
        """Send a customer-facing reply on the ticket's own channel.

        Mirrors the most recent customer message's channel + source (swapping
        direction) so the reply goes back the same way they contacted us.
        Leaves ticket status unchanged (stays open).
        """
        if not self._auth:
            return {"ok": False, "error": "gorgias credentials not configured"}
        murl = f"{self.base_url}{_API_VERSION}/tickets/{ticket_id}/messages"
        msgs = []
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as _c:
                mr = await _c.get(murl, auth=self._auth, params={"limit": 30})
                if mr.status_code == 404:
                    return {"ok": False, "error": "ticket not found"}
                if mr.status_code == 200:
                    jd = mr.json()
                    msgs = jd.get("data", jd) if isinstance(jd, dict) else jd
        except Exception as exc:
            return {"ok": False, "error": f"failed to read ticket: {exc}"}
        if not isinstance(msgs, list):
            msgs = []
        def _dt(m):
            return m.get("created_datetime") or m.get("sent_datetime") or ""
        msgs_sorted = sorted(msgs, key=_dt)
        base = None
        for m in reversed(msgs_sorted):
            if not m.get("from_agent", False):
                base = m
                break
        if base is None and msgs_sorted:
            base = msgs_sorted[-1]
        base = base or {}
        channel = base.get("channel") or ticket.get("channel") or "email"
        src = base.get("source") or {}
        cust_from = src.get("from") or {}
        our_to = src.get("to") or []
        new_source = {}
        stype = src.get("type") or channel
        if stype:
            new_source["type"] = stype
        if cust_from:
            new_source["to"] = [cust_from]
        if isinstance(our_to, list) and our_to:
            new_source["from"] = our_to[0]
        payload = {
            "channel": channel,
            "via": channel,
            "from_agent": True,
            "public": True,
            "body_text": body_text,
            "sender": {"email": self.email},
        }
        if new_source:
            payload["source"] = new_source
        return await self._post_message(ticket_id, payload)
