"""Outbound Gorgias reply (stdlib only). Lazy-imported when the switch is ON."""

from __future__ import annotations

import base64
import html
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_USER_AGENT = "DemoHelpdesk-GorgiasBridge/1.0"
_API = "/api"


def _base_url() -> str:
    override = os.environ.get("GORGIAS_BASE_URL", "").strip().rstrip("/")
    if override:
        return override
    subdomain = os.environ.get("GORGIAS_SUBDOMAIN", "").strip()
    if not subdomain:
        raise RuntimeError("GORGIAS_SUBDOMAIN is not set")
    return f"https://{subdomain}.gorgias.com"


def _auth_header() -> str:
    email = os.environ.get("GORGIAS_API_EMAIL", "").strip()
    key = os.environ.get("GORGIAS_API_KEY", "").strip()
    if not email or not key:
        raise RuntimeError("Gorgias API credentials are not set")
    token = base64.b64encode(f"{email}:{key}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    retries: int = 1,
) -> dict[str, Any]:
    url = f"{_base_url()}{_API}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": _auth_header(),
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt <= retries + 1:
                time.sleep(0.5 * attempt)
                continue
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"Gorgias {method} {path} failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            # A freshly-activated bridge fails closed like every other path, with
            # the structured error callers already handle.
            raise RuntimeError(f"Gorgias {method} {path} unreachable: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Gorgias {method} {path} returned invalid JSON: {exc.msg}") from exc


def _is_customer(message: dict[str, Any]) -> bool:
    flag = message.get("from_agent")
    if isinstance(flag, str):
        return flag.strip().lower() not in {"true", "1", "yes"}
    return flag is not True


def _last_customer_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    def _dt(m: dict[str, Any]) -> str:
        return str(m.get("created_datetime") or m.get("sent_datetime") or "")

    ordered = sorted(messages, key=_dt)
    for message in reversed(ordered):
        if _is_customer(message):
            return message
    return None


def send_public_reply(ticket_id: int | str, body_text: str) -> dict[str, Any]:
    """POST a public agent reply and poll delivery briefly."""
    from helpdesk.send_access import ACTIVATE_SEND_MESSAGE, send_access_enabled

    if not send_access_enabled():
        return {"ok": False, "error": ACTIVATE_SEND_MESSAGE}
    text = str(body_text or "").strip()
    if not text:
        return {"ok": False, "error": "empty body"}
    tid = int(ticket_id)
    listed = _request(
        "GET",
        f"/tickets/{tid}/messages?limit=30&order_by={urllib.parse.quote('created_datetime:desc')}",
    )
    messages = listed.get("data") if isinstance(listed, dict) else None
    if not isinstance(messages, list):
        messages = listed if isinstance(listed, list) else []
    base = _last_customer_message([m for m in messages if isinstance(m, dict)])
    if base is None:
        return {"ok": False, "error": "no customer message to reply on"}
    channel = str(base.get("channel") or "email")
    src = base.get("source") if isinstance(base.get("source"), dict) else {}
    cust_from = src.get("from") if isinstance(src.get("from"), dict) else {}
    if not cust_from:
        sender = base.get("sender") if isinstance(base.get("sender"), dict) else {}
        cust_from = {"address": sender.get("email") or sender.get("address")}
    our_to = src.get("to") if isinstance(src.get("to"), list) else []
    support_addr = None
    if our_to and isinstance(our_to[0], dict):
        support_addr = our_to[0].get("address")
    customer_email = cust_from.get("address") or cust_from.get("email")
    if not customer_email:
        sender = base.get("sender") if isinstance(base.get("sender"), dict) else {}
        customer_email = sender.get("email") or sender.get("address")
    if not customer_email:
        return {"ok": False, "error": "customer email missing on last message"}
    email = os.environ.get("GORGIAS_API_EMAIL", "").strip()
    new_source = {
        "type": channel,
        "to": [{"address": customer_email}],
        "from": {"address": support_addr or email},
    }
    payload = {
        "channel": channel,
        "via": "api",
        "from_agent": True,
        "public": True,
        "body_text": text,
        "body_html": html.escape(text).replace("\n", "<br>"),  # parity with the production adapter (gorgias_client.py)
        "sender": {"email": email},
        "receiver": {"email": customer_email},
        "source": new_source,
    }
    created = _request("POST", f"/tickets/{tid}/messages", body=payload, retries=1)
    message_id = created.get("id") if isinstance(created, dict) else None
    if message_id is None:
        return {"ok": False, "error": "Gorgias did not return a message id", "raw": created}
    delivery = _wait_for_delivery(tid, int(message_id))
    return {
        "ok": delivery["status"] != "failed",
        "messageId": str(message_id),
        "deliveryStatus": delivery["status"],
        "channel": channel,
    }


def close_ticket(ticket_id: int | str) -> dict[str, Any]:
    from helpdesk.send_access import ACTIVATE_SEND_MESSAGE, send_access_enabled

    if not send_access_enabled():
        return {"ok": False, "error": ACTIVATE_SEND_MESSAGE}
    tid = int(ticket_id)
    try:
        _request("PUT", f"/tickets/{tid}", body={"status": "closed"}, retries=1)
        return {"ok": True}
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}


def _wait_for_delivery(ticket_id: int, message_id: int) -> dict[str, Any]:
    for _ in range(4):
        try:
            latest = _request("GET", f"/tickets/{ticket_id}/messages/{message_id}")
        except RuntimeError:
            time.sleep(0.5)
            continue
        if latest.get("sent_datetime"):
            return {"status": "sent", "message": latest}
        if latest.get("failed_datetime") or latest.get("last_sending_error"):
            return {"status": "failed", "message": latest}
        time.sleep(0.5)
    return {"status": "pending"}
