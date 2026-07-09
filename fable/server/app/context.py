"""Order/return context fetchers (Shopify + Redo emulators).

Uses httpx with short timeouts. **Never raises for the pipeline**: on any
connection/HTTP error it logs and returns empty/None so a ticket is always
drafted even when an emulator is down.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

import httpx

from . import config

log = logging.getLogger("fable.context")

_TIMEOUT = 5.0

# --- Shopify client-credentials token cache --------------------------------
_token_lock = threading.Lock()
_token_value: Optional[str] = None
_token_expiry: float = 0.0


def _mint_token() -> Optional[str]:
    """Client-credentials grant → 24h Admin API token. Cached + refreshed."""
    global _token_value, _token_expiry
    with _token_lock:
        now = time.time()
        if _token_value and now < _token_expiry - 30:
            return _token_value
        url = f"{config.SHOPIFY_BASE}/admin/oauth/access_token"
        payload = {
            "client_id": config.SHOPIFY_CLIENT_ID,
            "client_secret": config.SHOPIFY_CLIENT_SECRET,
            "grant_type": "client_credentials",
        }
        try:
            # trust_env=False: these calls target localhost emulators and must
            # never be routed through an HTTP(S)/SOCKS proxy in the environment
            # (keeps the "nothing leaves localhost" invariant true).
            r = httpx.post(url, json=payload, timeout=_TIMEOUT, trust_env=False)
            if r.status_code != 200:
                log.warning("shopify token mint failed: %s %s", r.status_code, r.text[:120])
                return None
            data = r.json()
            _token_value = data.get("access_token")
            _token_expiry = now + float(data.get("expires_in", 3600))
            return _token_value
        except Exception as e:  # connection refused, timeout, etc.
            log.warning("shopify token mint error: %r", e)
            return None


def _invalidate_token() -> None:
    global _token_value, _token_expiry
    with _token_lock:
        _token_value = None
        _token_expiry = 0.0


# --- Shopify orders --------------------------------------------------------
def _trim_order(o: dict) -> dict:
    tracking_number = None
    tracking_url = None
    fulfillments = o.get("fulfillments") or []
    if fulfillments:
        f0 = fulfillments[0]
        tracking_number = (
            f0.get("tracking_number")
            or (f0.get("tracking_numbers") or [None])[0]
            or ((f0.get("tracking_info") or {}) or {}).get("number")
        )
        tracking_url = (
            f0.get("tracking_url")
            or (f0.get("tracking_urls") or [None])[0]
            or ((f0.get("tracking_info") or {}) or {}).get("url")
        )
    line_items = [
        {"title": li.get("title"), "quantity": li.get("quantity"), "sku": li.get("sku")}
        for li in (o.get("line_items") or [])
    ]
    return {
        "id": o.get("id"),
        "name": o.get("name"),
        "email": o.get("email"),
        "created_at": o.get("created_at"),
        "financial_status": o.get("financial_status"),
        "fulfillment_status": o.get("fulfillment_status"),
        "total_price": o.get("total_price"),
        "currency": o.get("currency"),
        "tracking_number": tracking_number,
        "tracking_url": tracking_url,
        "line_items": line_items,
    }


def fetch_orders_by_email(email: str) -> Optional[List[dict]]:
    """Return trimmed orders for the email, or None on transport failure."""
    if not email:
        return []
    token = _mint_token()
    if not token:
        return None
    ver = config.SHOPIFY_API_VERSION
    url = f"{config.SHOPIFY_BASE}/admin/api/{ver}/orders.json"
    headers = {"X-Shopify-Access-Token": token}
    params = {"email": email, "status": "any", "limit": 50}
    try:
        r = httpx.get(url, headers=headers, params=params, timeout=_TIMEOUT, trust_env=False)
        if r.status_code == 401:
            _invalidate_token()
            token = _mint_token()
            if not token:
                return None
            headers["X-Shopify-Access-Token"] = token
            r = httpx.get(url, headers=headers, params=params, timeout=_TIMEOUT, trust_env=False)
        if r.status_code != 200:
            log.warning("shopify orders %s: %s", r.status_code, r.text[:120])
            return None
        orders = r.json().get("orders", [])
        return [_trim_order(o) for o in orders]
    except Exception as e:
        log.warning("shopify orders error: %r", e)
        return None


# --- Redo returns ----------------------------------------------------------
def _trim_return(rt: dict) -> dict:
    return {
        "id": rt.get("id"),
        "order_name": rt.get("order_name"),
        "status": rt.get("status"),
        "items": rt.get("items") or [],
        "refund_amount": rt.get("refund_amount"),
        "created_at": rt.get("created_at"),
    }


def _redo_get(path: str, params: dict | None = None) -> Optional[dict]:
    store = config.REDO_STORE_ID
    url = f"{config.REDO_BASE}/v2.2/stores/{store}{path}"
    headers = {"Authorization": f"Bearer {config.REDO_API_KEY}"}
    try:
        r = httpx.get(url, headers=headers, params=params or {}, timeout=_TIMEOUT,
                      trust_env=False)
        if r.status_code != 200:
            log.warning("redo %s: %s", r.status_code, r.text[:120])
            return None
        return r.json()
    except Exception as e:
        log.warning("redo error: %r", e)
        return None


def _extract_returns(payload) -> List[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("returns", "data", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
        # single return object
        if payload.get("id"):
            return [payload]
    return []


def fetch_returns_for_orders(order_names: List[str]) -> Optional[List[dict]]:
    """Fetch returns tied to the customer's orders. None on transport failure."""
    names = [n for n in (order_names or []) if n]
    collected: dict = {}
    any_success = False

    if names:
        for name in names:
            payload = _redo_get("/returns", {"order_name": name})
            if payload is None:
                continue
            any_success = True
            for rt in _extract_returns(payload):
                collected[rt.get("id")] = _trim_return(rt)
        if not any_success:
            return None
        return list(collected.values())

    # No order names known — try a plain list so returns can still surface.
    payload = _redo_get("/returns", {"limit": 50})
    if payload is None:
        return None
    return [_trim_return(rt) for rt in _extract_returns(payload)]


# --- Combined --------------------------------------------------------------
def fetch_context(email: str) -> Optional[dict]:
    """Return {"orders":[...],"returns":[...]} or None if nothing was reachable."""
    orders = fetch_orders_by_email(email)          # None on failure, [] on empty
    order_names = [o.get("name") for o in orders] if orders else []
    returns = fetch_returns_for_orders(order_names)  # None on failure

    if orders is None and returns is None:
        return None
    return {"orders": orders or [], "returns": returns or []}
