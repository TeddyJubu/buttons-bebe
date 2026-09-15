"""Read-only Shopify rail snapshot for the isolated inbox. No network."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import closing

DEFAULT_PATH = '/var/lib/buttonsbebe-inbox-projection/shop-rail.sqlite3'


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
        uri = target.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as db:
            db.execute('PRAGMA query_only=ON')
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
