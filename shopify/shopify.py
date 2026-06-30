"""
shopify.py — independent Shopify module for Buttons Bebe.

This module is a self-contained entity. It knows only Shopify.
It knows nothing about Gorgias, Telegram, the KB, or the agent.

Public API:
  get_orders_by_email(email)  -> list of raw order dicts (newest first)
  get_order_by_number(number) -> single order dict or None
  get_order_status(order)     -> clean status dict
  format_status_reply(status) -> plain English string for the customer

Config (from environment / .env):
  SHOPIFY_STORE          = your-store-name (before .myshopify.com)

  Auth — pick ONE:
  (a) Mint-on-demand (recommended; tokens auto-refresh, ~24h each):
        SHOPIFY_API_KEY      = client id of the app
        SHOPIFY_API_SECRET   = shpss_... (client secret)
      The module exchanges these for a short-lived access token via the
      client_credentials grant and caches it until just before it expires.
  (b) Static token (legacy / overrides the above if set):
        SHOPIFY_ACCESS_TOKEN = shpat_... (read-only: read_orders, read_customers)

  Either way the app must have the read_orders + read_customers scopes, or
  order/customer reads return HTTP 403.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger('shopify')

_API_VERSION = '2024-01'
_USER_AGENT  = 'Teddy-Agent/1.0 (Buttons Bebe)'

# Refresh a minted token this many seconds before it actually expires.
_TOKEN_SAFETY_MARGIN = 300
_token_cache = {'token': None, 'expires_at': 0.0}
_token_lock  = threading.Lock()


def _store() -> str:
    store = os.environ.get('SHOPIFY_STORE', '').strip()
    if not store:
        raise RuntimeError("SHOPIFY_STORE not set in environment")
    return store


def _base_url() -> str:
    return f"https://{_store()}.myshopify.com/admin/api/{_API_VERSION}"


def _mint_token():
    """Exchange client_id/client_secret for a short-lived access token.

    Returns (token, expires_at_epoch). Raises on misconfig / HTTP error.
    """
    client_id     = os.environ.get('SHOPIFY_API_KEY', '').strip()
    client_secret = os.environ.get('SHOPIFY_API_SECRET', '').strip()
    if not (client_id and client_secret):
        raise RuntimeError(
            "No Shopify credentials: set SHOPIFY_ACCESS_TOKEN, or "
            "SHOPIFY_API_KEY + SHOPIFY_API_SECRET to mint one"
        )
    resp = requests.post(
        f"https://{_store()}.myshopify.com/admin/oauth/access_token",
        data={
            'grant_type':    'client_credentials',
            'client_id':     client_id,
            'client_secret': client_secret,
        },
        headers={'User-Agent': _USER_AGENT},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get('access_token')
    if not token:
        raise RuntimeError("Shopify token response had no access_token")
    expires_in = int(body.get('expires_in', 86400))
    expires_at = time.time() + max(0, expires_in - _TOKEN_SAFETY_MARGIN)
    return token, expires_at


def _get_token() -> str:
    """Return a valid access token. Static env token wins; otherwise mint+cache."""
    static = os.environ.get('SHOPIFY_ACCESS_TOKEN', '').strip()
    if static:
        return static
    now = time.time()
    with _token_lock:
        if _token_cache['token'] and now < _token_cache['expires_at']:
            return _token_cache['token']
        token, expires_at = _mint_token()
        _token_cache['token'] = token
        _token_cache['expires_at'] = expires_at
        log.info("Minted Shopify access token (valid ~%ds)", int(expires_at - now))
        return token


def _headers() -> dict:
    return {
        'X-Shopify-Access-Token': _get_token(),
        'Content-Type': 'application/json',
        'User-Agent': _USER_AGENT,
    }


# ── Public functions ───────────────────────────────────────────────────────────

def get_orders_by_email(email: str) -> list:
    """
    Return all orders for this customer email, newest first.
    Returns [] if none found or if Shopify is unreachable.
    """
    try:
        resp = requests.get(
            f"{_base_url()}/orders.json",
            headers=_headers(),
            params={'email': email, 'status': 'any', 'limit': 10},
            timeout=8,
        )
        resp.raise_for_status()
        orders = resp.json().get('orders', [])
        # Sort by created_at descending (newest first)
        orders.sort(key=lambda o: o.get('created_at', ''), reverse=True)
        return orders
    except Exception as e:
        log.error("get_orders_by_email(%s) failed: %s", email, e)
        return []


def get_order_by_number(order_number: str) -> dict:
    """
    Return the order matching this order number (e.g. "1234" or "#1234").
    Returns None if not found.
    """
    number = str(order_number).lstrip('#').strip()
    try:
        resp = requests.get(
            f"{_base_url()}/orders.json",
            headers=_headers(),
            params={'name': f"#{number}", 'status': 'any', 'limit': 1},
            timeout=8,
        )
        resp.raise_for_status()
        orders = resp.json().get('orders', [])
        return orders[0] if orders else None
    except Exception as e:
        log.error("get_order_by_number(%s) failed: %s", order_number, e)
        return None


def get_order_status(order: dict) -> dict:
    """
    Extract a clean, structured status from a raw Shopify order dict.

    Returns:
    {
        "order_number":        str,
        "created_at":          str (human-readable date),
        "financial_status":    str,
        "fulfillment_status":  str,
        "tracking_numbers":    [str],
        "tracking_urls":       [str],
        "carrier":             str,
        "items":               [{"title": str, "quantity": int}],
        "estimated_delivery":  str or None,
    }
    """
    if not order:
        return {}

    # Parse order date
    created_raw = order.get('created_at', '')
    try:
        dt = datetime.fromisoformat(created_raw.replace('Z', '+00:00'))
        created_str = dt.strftime('%B %d, %Y')
    except Exception:
        created_str = created_raw[:10] if created_raw else 'unknown date'

    # Tracking info from fulfillments
    tracking_numbers = []
    tracking_urls    = []
    carrier          = ''
    estimated        = None

    for fulfillment in order.get('fulfillments', []):
        tn = fulfillment.get('tracking_number')
        tu = fulfillment.get('tracking_url')
        tc = fulfillment.get('tracking_company', '')
        if tn:
            tracking_numbers.append(tn)
        if tu:
            tracking_urls.append(tu)
        if tc and not carrier:
            carrier = tc
        # Shopify sometimes includes estimated_delivery in shipment_status_url or metadata
        ed = fulfillment.get('estimated_delivery_at') or fulfillment.get('expected_delivery_date')
        if ed and not estimated:
            try:
                dt2 = datetime.fromisoformat(ed.replace('Z', '+00:00'))
                estimated = dt2.strftime('%B %d, %Y')
            except Exception:
                estimated = ed[:10] if ed else None

    # Line items
    items = [
        {
            'title':    li.get('title', 'Unknown item'),
            'quantity': li.get('quantity', 1),
        }
        for li in order.get('line_items', [])
    ]

    return {
        'order_number':       str(order.get('order_number', order.get('name', '?'))),
        'created_at':         created_str,
        'financial_status':   order.get('financial_status', 'unknown'),
        'fulfillment_status': order.get('fulfillment_status') or 'unfulfilled',
        'tracking_numbers':   tracking_numbers,
        'tracking_urls':      tracking_urls,
        'carrier':            carrier,
        'items':              items,
        'estimated_delivery': estimated,
    }


def format_status_reply(status: dict) -> str:
    """
    Turn a get_order_status() result into a plain English sentence
    the agent can send directly to the customer.
    """
    if not status:
        return "I was unable to find your order details. Please contact us with your order number."

    num          = status.get('order_number', '?')
    date         = status.get('created_at', '')
    fulfil       = status.get('fulfillment_status', 'unfulfilled')
    financial    = status.get('financial_status', '')
    tracking_urls = status.get('tracking_urls', [])
    carrier      = status.get('carrier', '')
    estimated    = status.get('estimated_delivery')
    items        = status.get('items', [])

    items_str = ', '.join(
        f"{i['title']} (x{i['quantity']})" for i in items[:3]
    )
    if len(items) > 3:
        items_str += f" and {len(items) - 3} more item(s)"

    date_str = f" placed on {date}" if date else ''

    if financial == 'refunded':
        return (
            f"Your order #{num}{date_str} has been fully refunded. "
            "Please allow 5–7 business days for the refund to appear on your statement."
        )

    if fulfil == 'unfulfilled':
        return (
            f"Your order #{num}{date_str} is currently being prepared for shipment. "
            "You'll receive a tracking email as soon as it ships (within 1–3 business days). "
            f"Items: {items_str}."
        )

    if fulfil in ('fulfilled', 'partial'):
        tracking_part = ''
        if tracking_urls:
            url = tracking_urls[0]
            carrier_str = f" via {carrier}" if carrier else ''
            tracking_part = f" You can track it here: {url}"
        delivery_part = f" Estimated delivery: {estimated}." if estimated else ''
        return (
            f"Your order #{num} has been shipped{date_str}{carrier_str}."
            f"{tracking_part}{delivery_part} "
            f"Items: {items_str}."
        )

    return (
        f"Your order #{num}{date_str} has status: {fulfil}. "
        "If you have questions, please reply with your order number and we'll look into it."
    )


# ── Product lookup (GraphQL; needs read_products) ───────────────────────────────

_PRODUCT_SEARCH_GQL = """
query($q: String!, $n: Int!) {
  products(first: $n, query: $q) {
    edges { node {
      id title vendor status handle onlineStoreUrl totalInventory
      priceRangeV2 { minVariantPrice { amount currencyCode } maxVariantPrice { amount currencyCode } }
      options { name values }
      variants(first: 100) { edges { node { title sku price availableForSale inventoryQuantity } } }
      descriptionHtml
    } }
  }
}"""


def _graphql(query: str, variables: dict = None) -> dict:
    """POST a GraphQL query to the Admin API; return the 'data' dict.
    Raises on transport or GraphQL errors so callers can fail soft."""
    resp = requests.post(
        f"{_base_url()}/graphql.json",
        headers=_headers(),
        json={'query': query, 'variables': variables or {}},
        timeout=12,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get('errors'):
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload.get('data') or {}


def _clean_html(html: str, limit: int = 600) -> str:
    import re
    txt = re.sub(r'<[^>]+>', ' ', html or '')
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt[:limit]


def _build_search_query(text: str, active_only: bool = True) -> str:
    """Free text -> Shopify product search query (plain relevance phrase).

    Shopify's relevance search across title/vendor/tags handles natural wording
    far better than ANDed title wildcards, and ranks the closest title first
    (so limit=1 reliably returns an exact-ish match)."""
    import re
    words = []
    for t in [w for w in re.split(r'\s+', (text or '').strip()) if w][:8]:
        t = re.sub(r"[^0-9A-Za-z\-'&]", '', t)
        if t:
            words.append(t)
    q = ' '.join(words) if words else '*'
    if active_only:
        q += ' status:active'
    return q


def summarize_product(node: dict) -> dict:
    """Clean a GraphQL product node into a compact, draft-friendly dict."""
    if not node:
        return {}
    pr = node.get('priceRangeV2') or {}
    pmin = pr.get('minVariantPrice') or {}
    pmax = pr.get('maxVariantPrice') or {}
    variants = [v.get('node', {}) for v in ((node.get('variants') or {}).get('edges') or [])]
    in_stock = [v.get('title') for v in variants if v.get('availableForSale')]
    out_stock = [v.get('title') for v in variants if not v.get('availableForSale')]
    return {
        'title': node.get('title'),
        'vendor': node.get('vendor'),
        'status': (node.get('status') or '').lower(),
        'handle': node.get('handle'),
        'url': node.get('onlineStoreUrl'),
        'price_min': pmin.get('amount'),
        'price_max': pmax.get('amount'),
        'currency': pmin.get('currencyCode') or pmax.get('currencyCode'),
        'options': [o.get('name') for o in (node.get('options') or [])],
        'sizes_in_stock': in_stock,
        'sizes_out_of_stock': out_stock,
        'total_variants': len(variants),
        'description': _clean_html(node.get('descriptionHtml')),
    }


def search_products(query_text: str, limit: int = 5, active_only: bool = True) -> list:
    """Search products by name. Returns summarize_product() dicts. [] on failure."""
    if not query_text or not str(query_text).strip():
        return []
    try:
        data = _graphql(_PRODUCT_SEARCH_GQL, {
            'q': _build_search_query(str(query_text), active_only),
            'n': max(1, min(int(limit or 5), 25)),
        })
        edges = ((data.get('products') or {}).get('edges')) or []
        return [summarize_product(e.get('node', {})) for e in edges]
    except Exception as e:
        log.error("search_products(%r) failed: %s", query_text, e)
        return []


def format_product_reply(summary: dict) -> str:
    """Plain-English one-liner about a product for a customer reply."""
    if not summary:
        return "I couldn't find that product. Could you share the product name or a link?"
    title = summary.get('title') or 'That item'
    vendor = summary.get('vendor')
    pmin = summary.get('price_min'); pmax = summary.get('price_max')
    cur = summary.get('currency') or ''
    if pmin and pmax and pmin != pmax:
        price = f"{pmin}-{pmax} {cur}".strip()
    elif pmin:
        price = f"{pmin} {cur}".strip()
    else:
        price = None
    sizes = summary.get('sizes_in_stock') or []
    by = f" by {vendor}" if vendor else ""
    parts = [f"{title}{by}"]
    if price:
        parts.append(f"is {price}")
    if sizes:
        parts.append("and is currently in stock in: " + ", ".join(str(s) for s in sizes))
    else:
        parts.append("is currently out of stock")
    sentence = " ".join(parts).rstrip(".") + "."
    url = summary.get('url')
    if url:
        sentence += f" {url}"
    return sentence


# -- Brand / vendor helpers (catalog-accurate "do you carry X?") ----------------

import json as _json, time as _time

_VENDORS_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.vendors_cache.json')


def list_vendors(active_only: bool = True, max_age_h: int = 24) -> list:
    """Distinct product vendors (brands), cached on disk; refreshes when stale.
    Returns [] on failure."""
    try:
        st = os.stat(_VENDORS_CACHE)
        if (_time.time() - st.st_mtime) < max_age_h * 3600:
            with open(_VENDORS_CACHE, encoding='utf-8') as f:
                return _json.load(f).get('vendors', [])
    except Exception:
        pass
    try:
        q = 'status:active' if active_only else '*'
        gql = ("query($q:String!,$c:String){products(first:250,query:$q,after:$c)"
               "{pageInfo{hasNextPage endCursor}edges{node{vendor}}}}")
        cursor = None
        names = set()
        for _ in range(40):
            data = _graphql(gql, {'q': q, 'c': cursor})
            conn = data.get('products') or {}
            for e in (conn.get('edges') or []):
                v = ((e.get('node') or {}).get('vendor') or '').strip()
                if v:
                    names.add(v)
            pi = conn.get('pageInfo') or {}
            if not pi.get('hasNextPage'):
                break
            cursor = pi.get('endCursor')
        out = sorted(names)
        try:
            with open(_VENDORS_CACHE, 'w', encoding='utf-8') as f:
                _json.dump({'vendors': out, 'fetched_at': _time.time()}, f)
        except Exception:
            pass
        return out
    except Exception as e:
        log.error("list_vendors failed: %s", e)
        try:
            with open(_VENDORS_CACHE, encoding='utf-8') as f:
                return _json.load(f).get('vendors', [])
        except Exception:
            return []


def products_by_vendor(vendor: str, limit: int = 5, active_only: bool = True) -> list:
    """In-stock-first products for a brand. [] on failure."""
    if not vendor:
        return []
    try:
        v = str(vendor).replace('"', ' ')
        q = 'vendor:"' + v + '"' + (' status:active' if active_only else '')
        data = _graphql(_PRODUCT_SEARCH_GQL, {'q': q, 'n': max(1, min(int(limit or 5), 25))})
        edges = ((data.get('products') or {}).get('edges')) or []
        return [summarize_product(e.get('node', {})) for e in edges]
    except Exception as e:
        log.error("products_by_vendor(%r) failed: %s", vendor, e)
        return []


# ── Smoke test ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    from pathlib import Path
    # Load /root/.env from repo root if running standalone
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'gorgias-webhook'))
        import dotenv_loader
        dotenv_loader.load()
    except ImportError:
        _env = Path('/root/.env')
        if _env.exists():
            for line in _env.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    test_email = sys.argv[1] if len(sys.argv) > 1 else 'test@example.com'
    print(f"Looking up orders for: {test_email}")
    orders = get_orders_by_email(test_email)
    if not orders:
        print("No orders found (or Shopify not configured)")
    else:
        status = get_order_status(orders[0])
        print("Status:", status)
        print("Reply:", format_status_reply(status))
