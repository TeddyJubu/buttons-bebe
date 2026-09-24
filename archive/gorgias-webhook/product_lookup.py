#!/usr/bin/env python3
"""
product_lookup.py — thin wrapper around /root/shopify/shopify.py product search
for the gorgias-webhook agent. Mirrors shopify_lookup.py so both agents share
the SAME Shopify module (single source of truth).

Read-only and fail-soft: any problem returns empty results, never raises.
"""
import logging
import sys
from pathlib import Path

log = logging.getLogger("gorgias-webhook.product_lookup")

# /root/gorgias-webhook/.. == /root, which holds the `shopify` package.
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
    from shopify import shopify  # /root/shopify/shopify.py
    return shopify


def search_products(query_text, limit=5, active_only=True):
    """Live product search by name -> list of compact product dicts. [] on failure."""
    if not query_text or not str(query_text).strip():
        return []
    try:
        return _shopify().search_products(str(query_text).strip(), limit=limit, active_only=active_only) or []
    except ImportError:
        log.warning("Shared Shopify module not importable under %s", _ROOT)
        return []
    except Exception as e:
        log.error("Product search failed: %s", e)
        return []


def describe_product(query_text, active_only=True):
    """Best single match as a plain-English line for a draft, or '' if none."""
    results = search_products(query_text, limit=1, active_only=active_only)
    if not results:
        return ""
    try:
        return _shopify().format_product_reply(results[0])
    except Exception as e:
        log.error("format_product_reply failed: %s", e)
        return ""




def list_vendors():
    try:
        return _shopify().list_vendors() or []
    except Exception as e:
        log.error("list_vendors failed: %s", e); return []


def products_by_vendor(vendor, limit=5):
    if not vendor:
        return []
    try:
        return _shopify().products_by_vendor(str(vendor).strip(), limit=limit) or []
    except Exception as e:
        log.error("products_by_vendor failed: %s", e); return []

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    q = " ".join(sys.argv[1:]) or "denim"
    res = search_products(q, limit=5)
    print(f"query: {q!r} -> {len(res)} match(es)")
    for r in res:
        print(" -", r.get("title"), "|", r.get("vendor"), "|",
              r.get("price_min"), r.get("currency"), "| in stock:", r.get("sizes_in_stock"))
    print("one-liner:", describe_product(q))
