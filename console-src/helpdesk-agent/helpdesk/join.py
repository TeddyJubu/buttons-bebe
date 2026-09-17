"""Cute Things Shopify join. Reads only. Miss → GID null. No Customer writes."""

from __future__ import annotations

import re

from . import fixtures_live_holes as live_holes
from . import queries
from .auth import PINNED_LIVE_SHOP
from .client import graphql
from .env import load_shopify_env
from .errors import HelpdeskError
from .names import API_VERSION, LIVE_HOLE_SHOP
from .shop import _try_live

_ORDER_NAME = re.compile(r"#(\d+)")


def parse_order_name(*parts: str) -> str | None:
    blob = " ".join(part or "" for part in parts)
    match = _ORDER_NAME.search(blob)
    return match.group(1) if match else None


def _fixture_order(name: str) -> dict[str, str | None] | None:
    bare = name.lstrip("#")
    needle = f"#{bare}"
    for order in live_holes.ORDERS.values():
        if order["name"] == needle or order["name"].lstrip("#") == bare:
            return {"id": order["id"], "customerId": order.get("customerId")}
    return None


def _fixture_customer(email: str) -> dict[str, str] | None:
    want = email.strip().lower()
    if not want:
        return None
    for customer in live_holes.CUSTOMERS.values():
        addr = (customer.get("defaultEmailAddress") or {}).get("emailAddress")
        if addr and addr.lower() == want:
            return {"id": customer["id"]}
    return None


def _live_order(name: str, env: dict[str, str]) -> dict[str, str | None] | None:
    live = _try_live(LIVE_HOLE_SHOP, env)
    if live is None:
        return None
    data = graphql(
        PINNED_LIVE_SHOP,
        env["SHOPIFY_CLIENT_ID"],
        env["SHOPIFY_CLIENT_SECRET"],
        queries.ORDER_BY_NAME_QUERY,
        {"query": f"name:{name.lstrip('#')}"},
        api_version=env.get("SHOPIFY_API_VERSION") or API_VERSION,
        env=env,
    )
    nodes = ((data.get("orders") or {}).get("nodes") or [])
    if not nodes:
        return None
    node = nodes[0]
    customer = node.get("customer") if isinstance(node.get("customer"), dict) else {}
    return {"id": node.get("id"), "customerId": customer.get("id")}


def _live_customer(email: str, env: dict[str, str]) -> dict[str, str] | None:
    live = _try_live(LIVE_HOLE_SHOP, env)
    if live is None:
        return None
    data = graphql(
        PINNED_LIVE_SHOP,
        env["SHOPIFY_CLIENT_ID"],
        env["SHOPIFY_CLIENT_SECRET"],
        queries.CUSTOMER_BY_EMAIL_QUERY,
        {"query": f'email:"{email}"'},
        api_version=env.get("SHOPIFY_API_VERSION") or API_VERSION,
        env=env,
    )
    nodes = ((data.get("customers") or {}).get("nodes") or [])
    if not nodes:
        return None
    node = nodes[0]
    addr = ((node.get("defaultEmailAddress") or {}).get("emailAddress") or "").lower()
    if addr != email.strip().lower():
        return None
    return {"id": node.get("id")}


def _find_order(name: str, env: dict[str, str]) -> dict[str, str | None] | None:
    try:
        found = _live_order(name, env)
    except HelpdeskError:
        found = None
    if found and found.get("id"):
        return found
    return _fixture_order(name)


def _find_customer(email: str, env: dict[str, str]) -> dict[str, str] | None:
    try:
        found = _live_customer(email, env)
    except HelpdeskError:
        found = None
    if found and found.get("id"):
        return found
    return _fixture_customer(email)


def join_shopify(
    *,
    subject: str = "",
    body: str = "",
    from_email: str | None,
    channel: str,
    env: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Return (customerId, orderId). Cute Things only. Miss → (None, None)."""
    env = env if env is not None else load_shopify_env()
    order_id: str | None = None
    customer_id: str | None = None
    name = parse_order_name(subject, body)
    if name:
        found = _find_order(name, env)
        if found:
            order_id = found.get("id")
            customer_id = found.get("customerId")
    if customer_id is None and channel == "email" and from_email:
        found = _find_customer(from_email, env)
        if found:
            customer_id = found.get("id")
    return customer_id, order_id


JOIN_SHOP = LIVE_HOLE_SHOP
