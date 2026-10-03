"""Read-only Shopify rail snapshot for the isolated inbox. No network."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import closing

DEFAULT_PATH = '/var/lib/buttonsbebe-inbox-projection/shop-rail.sqlite3'
PAYLOAD_VERSION = 2


def display_payload(payload):
    if payload.get('payloadVersion') == PAYLOAD_VERSION:
        return payload
    payload = copy.deepcopy(payload)
    customer = payload.get('customer')
    if isinstance(customer, dict):
        customer['amountSpent'] = None
    order = payload.get('order')
    if isinstance(order, dict):
        order['currentTotalPriceSet'] = None
        lines = order.get('lineItems', {}).get('nodes', []) if isinstance(order.get('lineItems'), dict) else []
        for line in lines:
            if isinstance(line, dict):
                line['originalUnitPriceSet'] = None
    for history in payload.get('history') or []:
        if isinstance(history, dict):
            history['currentTotalPriceSet'] = None
    payload['legacyMoneyUnverified'] = True
    return payload


def connect(path):
    """Read-only, row-typed connection — the one DB-open recipe for the
    projection pair (report 10, action 6). projection.py re-exports this."""
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def _path(path=None):
    return Path(path or os.environ.get('SHOP_RAIL_PATH', DEFAULT_PATH))


def attach(ticket, path=None):
    """Attach a local Shopify snapshot when one exists. Missing files stay silent."""
    if not isinstance(ticket, dict):
        return ticket
    ticket_id = ticket.get('id')
    if not ticket_id:
        return ticket
    target = _path(path)
    if not target.is_file():
        return ticket
    try:
        with closing(connect(target)) as db:
            row = db.execute(
                'SELECT payload FROM rail WHERE ticket_id=?',
                (ticket_id,),
            ).fetchone()
    except (sqlite3.Error, OSError, ValueError):
        return ticket
    if not row:
        return ticket
    try:
        payload = json.loads(row[0])
    except (TypeError, json.JSONDecodeError, ValueError):
        return ticket
    if not isinstance(payload, dict):
        return ticket
    payload = display_payload(payload)
    context = ticket.get('customerContext') or {}
    identity = context.get('identity') or {}
    email = identity.get('email') or ticket.get('fromEmail') or ticket.get('customerName')
    if context.get('conflict') or not isinstance(email, str):
        return ticket
    if str(payload.get('email') or '').casefold() != email.strip().casefold():
        return ticket
    fetched = payload.get('fetchedAtEpoch')
    payload['stale'] = not isinstance(fetched, (int, float)) or time.time() - fetched > 6 * 3600 or bool(payload.get('refreshError'))
    ticket['shopifyRail'] = payload
    if payload.get('customerId'):
        ticket['customerId'] = payload['customerId']
    if payload.get('orderId'):
        ticket['orderId'] = payload['orderId']
    return ticket
