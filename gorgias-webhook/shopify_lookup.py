#!/usr/bin/env python3
"""
shopify_lookup.py — thin wrapper around /root/shopify/shopify.py for the
gorgias-webhook agent.

Mirrors teddy/skills/lookup_order.py so BOTH agents use the SAME shared
Shopify module (single source of truth — no duplicated Shopify logic).

Why: the Gorgias-synced order block (gorgias_api.extract_order_context) is
missing tracking links, returns, and orders older than the 10 most recent.
This wrapper reads those gaps directly from Shopify (read-only) and enriches
the existing order_context in place.

Safety: READ-ONLY and fail-soft. Any problem (shared module missing, Shopify
unreachable, API scopes not granted yet) leaves order_context exactly as it
was and never raises into the pipeline.
"""

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("gorgias-webhook.shopify_lookup")

# Make the shared module importable: /root/gorgias-webhook/.. == /root,
# which contains the `shopify` package (shopify/shopify.py).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Ensure SHOPIFY_* from .env are present even when run outside server.py.
try:
    import dotenv_loader
    dotenv_loader.load()
except Exception:
    pass


def _shopify():
    """Import the shared Shopify module lazily (so import never hard-fails)."""
    from shopify import shopify  # /root/shopify/shopify.py
    return shopify


def _norm(name):
    """Normalize an order identifier for matching ('#1234' -> '1234')."""
    if name is None:
        return ""
    return str(name).lstrip("#").strip()


def is_configured():
    """True if the creds needed for a live Shopify lookup are present."""
    return bool(
        os.environ.get("SHOPIFY_STORE")
        and (
            os.environ.get("SHOPIFY_ACCESS_TOKEN")
            or (os.environ.get("SHOPIFY_API_KEY") and os.environ.get("SHOPIFY_API_SECRET"))
        )
    )


def get_orders_by_email(email):
    """All Shopify orders for an email, newest first. [] on any failure."""
    if not email or not str(email).strip():
        return []
    try:
        return _shopify().get_orders_by_email(str(email).strip()) or []
    except ImportError:
        log.warning("Shared Shopify module not importable under %s", _ROOT)
        return []
    except Exception as e:
        log.error("Shopify get_orders_by_email failed: %s", e)
        return []


def status_for_order(raw_order):
    """Clean status dict (incl. tracking) for one raw Shopify order. {} on failure."""
    if not raw_order:
        return {}
    try:
        return _shopify().get_order_status(raw_order) or {}
    except Exception as e:
        log.error("Shopify get_order_status failed: %s", e)
        return {}


def enrich_order_context(order_context):
    """Augment a gorgias_api.extract_order_context() dict with live Shopify data.

    Adds, when available:
      - per-order tracking_numbers / tracking_urls / carrier (the #1 Gorgias gap)
      - order_context['shopify_live']         — True if Shopify answered
      - order_context['shopify_orders_count'] — total orders Shopify returned
    If the Gorgias block had no orders but Shopify has some, populates orders[]
    from Shopify so the draft still has something factual to work with.

    Always returns order_context. Never raises.
    """
    if not isinstance(order_context, dict):
        return order_context

    order_context.setdefault("shopify_live", False)

    if not is_configured():
        order_context["shopify_enrich_note"] = "shopify_not_configured"
        return order_context

    email = (order_context.get("customer_email") or "").strip()
    if not email:
        return order_context

    raw_orders = get_orders_by_email(email)
    if not raw_orders:
        # Either a real "no orders", or scopes not granted yet — leave
        # the Gorgias-built data untouched.
        return order_context

    order_context["shopify_live"] = True
    order_context["shopify_orders_count"] = len(raw_orders)

    # Map normalized order name -> (raw order, clean status with tracking).
    status_by_name = {}
    for raw in raw_orders:
        st = status_for_order(raw)
        key = _norm(raw.get("name") or st.get("order_number"))
        if key:
            status_by_name[key] = (raw, st)

    existing = order_context.get("orders") or []
    any_tracking = False

    if existing:
        # Enrich the orders already pulled from the Gorgias block.
        for o in existing:
            if not isinstance(o, dict):
                continue
            match = status_by_name.get(_norm(o.get("name")))
            if not match:
                continue
            _, st = match
            tn = st.get("tracking_numbers") or []
            tu = st.get("tracking_urls") or []
            if tn or tu:
                o["tracking_numbers"] = tn
                o["tracking_urls"] = tu
                o["carrier"] = st.get("carrier") or ""
                any_tracking = True
    else:
        # Gorgias had no orders — build them from Shopify (same shape).
        built = []
        for raw in raw_orders[:10]:
            st = status_for_order(raw)
            tn = st.get("tracking_numbers") or []
            tu = st.get("tracking_urls") or []
            if tn or tu:
                any_tracking = True
            built.append({
                "name": raw.get("name"),
                "created_at": raw.get("created_at"),
                "financial_status": raw.get("financial_status"),
                "fulfillment_status": raw.get("fulfillment_status") or "unfulfilled",
                "line_items": [
                    {"sku": li.get("sku"), "title": li.get("title"), "quantity": li.get("quantity")}
                    for li in (raw.get("line_items") or [])
                ],
                "shipping_address": raw.get("shipping_address"),
                "billing_address": raw.get("billing_address"),
                "tracking_numbers": tn,
                "tracking_urls": tu,
                "carrier": st.get("carrier") or "",
            })
        if built:
            order_context["orders"] = built
            order_context["shopify_found"] = True
            if order_context.get("orders_count") is None:
                order_context["orders_count"] = len(raw_orders)

    # Tracking is no longer a "gap" if we actually found some.
    if any_tracking:
        gaps = order_context.get("gaps") or []
        order_context["gaps"] = [g for g in gaps if g != "tracking_links"]

    return order_context


# --- standalone smoke test: python3 shopify_lookup.py someone@example.com ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    test_email = sys.argv[1] if len(sys.argv) > 1 else "test@example.com"
    print("configured:", is_configured())
    orders = get_orders_by_email(test_email)
    print(f"orders for {test_email}: {len(orders)}")
    if orders:
        print("latest status:", status_for_order(orders[0]))
    demo = {
        "customer_email": test_email, "orders": [], "shopify_found": False,
        "gaps": ["tracking_links", "returns_refunds", "orders_older_than_10_most_recent"],
    }
    enrich_order_context(demo)
    print("enriched demo:", demo)
