"""
lookup_order.py — thin wrapper around /root/shopify/shopify.py.

The shopify module is an independent entity mounted at /app/shopify inside
the container. This skill isolates the agent from knowing anything about
Shopify's API directly.

Returns:
  {"order": <order dict> | None, "source": "shopify", "error": str | None}
"""

import logging
import sys
from pathlib import Path

log = logging.getLogger('teddy.lookup_order')

# Add the mounted shopify module to path
_SHOPIFY_PATH = Path(__file__).parent.parent.parent / 'shopify'
if str(_SHOPIFY_PATH) not in sys.path:
    sys.path.insert(0, str(_SHOPIFY_PATH.parent))


def lookup_order(email: str) -> dict:
    """
    lookup_order(email) -> {"order": dict | None, "source": "shopify", "error": str | None}

    Returns the most recent Shopify order for this customer email.
    Returns order=None if not found or if Shopify is not configured.
    """
    if not email or not email.strip():
        return {'order': None, 'source': 'shopify', 'error': 'No email provided'}

    try:
        from shopify import shopify
        orders = shopify.get_orders_by_email(email.strip())
        if not orders:
            log.info("No Shopify orders found for %s", email)
            return {'order': None, 'source': 'shopify', 'error': None}

        latest = orders[0]
        status = shopify.get_order_status(latest)
        return {'order': status, 'source': 'shopify', 'error': None}

    except ImportError:
        log.warning("Shopify module not available — is /shopify mounted?")
        return {'order': None, 'source': 'shopify', 'error': 'Shopify module not mounted'}

    except Exception as e:
        log.error("Shopify lookup failed: %s", e)
        return {'order': None, 'source': 'shopify', 'error': str(e)}
