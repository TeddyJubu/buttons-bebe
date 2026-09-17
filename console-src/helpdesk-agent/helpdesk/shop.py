"""Shop tissue: live Admin GraphQL or labeled fixtures. Live never invents OPEN."""

from __future__ import annotations

from typing import Any
import os

from . import fixtures_live_holes as live_holes
from . import fixtures_sample as sample
from . import queries
from .auth import PINNED_LIVE_SHOP, cached_token, normalize_shop
from .client import graphql
from .dto import clerk_customer, clerk_history_row, clerk_order, clerk_returns
from .env import load_shopify_env
from .errors import HelpdeskError, not_found
from .gids import require_gid, require_shop
from .names import API_VERSION, LIVE_HOLE_SHOP, SAMPLE_SHOP


def _catalog_get(catalog, mapping: dict, ident: str, kind: str) -> dict:
    row = mapping.get(ident)
    if not row:
        raise not_found(kind, ident)
    return row


def _history(catalog, customer_id: str) -> list[dict]:
    ids = catalog.CUSTOMER_ORDERS.get(customer_id)
    if ids is None:
        raise not_found("customer", customer_id)
    rows = [catalog.ORDERS[oid] for oid in ids]
    rows = sorted(rows, key=lambda item: item["createdAt"], reverse=True)
    return [clerk_history_row(item) for item in rows]


class CatalogShop:
    def __init__(self, catalog, source: str):
        self.catalog = catalog
        self.source = source

    def get_customer(self, shop: str, customer_id: str) -> dict[str, Any]:
        return clerk_customer(_catalog_get(self.catalog, self.catalog.CUSTOMERS, customer_id, "customer"))

    def get_order(self, shop: str, order_id: str) -> dict[str, Any]:
        return clerk_order(_catalog_get(self.catalog, self.catalog.ORDERS, order_id, "order"))

    def get_returns(self, shop: str, order_id: str) -> dict[str, Any]:
        return clerk_returns(_catalog_get(self.catalog, self.catalog.ORDERS, order_id, "order"))

    def list_past_orders(self, shop: str, customer_id: str) -> list[dict[str, Any]]:
        return _history(self.catalog, customer_id)


class LiveShop:
    source = "live"

    def __init__(self, env: dict[str, str]):
        self.env = env

    def _gql(self, shop: str, document: str, ident: str) -> dict[str, Any]:
        return graphql(
            shop,
            self.env["SHOPIFY_CLIENT_ID"],
            self.env["SHOPIFY_CLIENT_SECRET"],
            document,
            {"id": ident},
            api_version=self.env.get("SHOPIFY_API_VERSION") or API_VERSION,
            env=self.env,
        )

    def get_customer(self, shop: str, customer_id: str) -> dict[str, Any]:
        node = self._gql(shop, queries.CUSTOMER_QUERY, customer_id).get("customer")
        if not node:
            raise not_found("customer", customer_id)
        return clerk_customer(node)

    def get_order(self, shop: str, order_id: str) -> dict[str, Any]:
        node = self._gql(shop, queries.ORDER_QUERY, order_id).get("order")
        if not node:
            raise not_found("order", order_id)
        return clerk_order(node)

    def get_returns(self, shop: str, order_id: str) -> dict[str, Any]:
        node = self._gql(shop, queries.ORDER_QUERY, order_id).get("order")
        if not node:
            raise not_found("order", order_id)
        return clerk_returns(node)

    def list_past_orders(self, shop: str, customer_id: str) -> list[dict[str, Any]]:
        node = self._gql(shop, queries.PAST_ORDERS_QUERY, customer_id).get("customer")
        if not node:
            raise not_found("customer", customer_id)
        rows = [clerk_history_row(item) for item in (node.get("orders") or {}).get("nodes") or []]
        return sorted(rows, key=lambda item: item["createdAt"] or "", reverse=True)


SAMPLE = CatalogShop(sample, "sample")
LIVE_HOLES = CatalogShop(live_holes, "sample")


def resolve_shop(shop: str | None, env: dict[str, str] | None = None):
    host = require_shop(shop)
    env = env if env is not None else load_shopify_env()
    if os.environ.get("HELPDESK_PRODUCTION") == "1":
        # The preview catalog is pinned to a different test store. Do not
        # fall back to it or silently connect production to that store.
        raise HelpdeskError("integration_inactive", "Order lookup is not connected to this inbox yet.")
    forced = (env.get("HELPDESK_SOURCE") or "").strip().lower()
    if host == SAMPLE_SHOP or forced == "sample":
        return SAMPLE, host
    if forced == "live-holes":
        return LIVE_HOLES, host
    live = _try_live(host, env)
    if live is not None:
        return live, host
    if host == LIVE_HOLE_SHOP:
        return LIVE_HOLES, host
    return SAMPLE, host


def _can_mint(shop: str, env: dict[str, str]) -> bool:
    configured = normalize_shop(env.get("SHOPIFY_SHOP", ""))
    request = normalize_shop(shop)
    return bool(
        env.get("SHOPIFY_CLIENT_ID")
        and env.get("SHOPIFY_CLIENT_SECRET")
        and configured == PINNED_LIVE_SHOP
        and request == PINNED_LIVE_SHOP
    )


def _try_live(shop: str, env: dict[str, str]) -> LiveShop | None:
    if (env.get("HELPDESK_SOURCE") or "").strip().lower() == "live-holes":
        return None
    if not _can_mint(shop, env):
        return None
    try:
        cached_token(env["SHOPIFY_CLIENT_ID"], env["SHOPIFY_CLIENT_SECRET"], env=env)
    except HelpdeskError:
        return None
    return LiveShop(env)


def rail_get_customer(shop: str, customer_id: str, env: dict[str, str] | None = None) -> tuple[str, dict]:
    port, host = resolve_shop(shop, env)
    return port.source, port.get_customer(host, require_gid(customer_id, "Customer"))


def rail_get_order(shop: str, order_id: str, env: dict[str, str] | None = None) -> tuple[str, dict]:
    port, host = resolve_shop(shop, env)
    return port.source, port.get_order(host, require_gid(order_id, "Order"))


def rail_get_returns(shop: str, order_id: str, env: dict[str, str] | None = None) -> tuple[str, dict]:
    port, host = resolve_shop(shop, env)
    return port.source, port.get_returns(host, require_gid(order_id, "Order"))


def rail_list_past_orders(shop: str, customer_id: str, env: dict[str, str] | None = None) -> tuple[str, list]:
    port, host = resolve_shop(shop, env)
    return port.source, port.list_past_orders(host, require_gid(customer_id, "Customer"))
