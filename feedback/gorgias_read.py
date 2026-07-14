"""gorgias_read.py — read-only Gorgias client (GET only).

Mirrors the proven pattern in tools/gorgias_mcp.py: Basic auth (email + key),
explicit User-Agent (Gorgias' WAF 403s the default urllib UA), short timeouts.
NO write methods exist here by design — capture is read-only.
"""
from __future__ import annotations

import requests

from . import config


class GorgiasError(RuntimeError):
    pass


def _auth():
    if not config.gorgias_configured():
        raise GorgiasError("Gorgias not configured (subdomain / email / key missing).")
    return (config.GORGIAS_EMAIL, config.GORGIAS_KEY)


def _get(path: str, params: dict | None = None) -> dict:
    r = requests.get(
        config.GORGIAS_BASE + path,
        params=params or {},
        auth=_auth(),
        headers={"User-Agent": config.USER_AGENT},
        timeout=20,
    )
    if not r.ok:
        raise GorgiasError(f"Gorgias API {r.status_code}: {r.text[:200]}")
    return r.json()


def list_tickets_updated_since(updated_iso: str, limit: int = 50) -> list[dict]:
    """Tickets ordered by updated_datetime asc, from `updated_iso` forward.

    Gorgias supports ordering + views; we keep it simple and page by updated_datetime.
    Uses `limit` (NOT per_page — that was a real bug noted in DEV-ISSUES).
    """
    params = {"order_by": "updated_datetime:asc", "limit": min(limit, 100)}
    data = _get("/tickets", params)
    tickets = data.get("data", data if isinstance(data, list) else [])
    if updated_iso:
        tickets = [t for t in tickets if str(t.get("updated_datetime", "")) >= updated_iso]
    return tickets


def get_ticket(ticket_id: int) -> dict:
    return _get(f"/tickets/{ticket_id}")


def get_messages(ticket_id: int, limit: int = 50) -> list[dict]:
    data = _get(f"/tickets/{ticket_id}/messages", {"limit": min(limit, 100)})
    return data.get("data", data if isinstance(data, list) else [])
