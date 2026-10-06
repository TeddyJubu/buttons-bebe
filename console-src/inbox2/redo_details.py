"""Credential-free Redo request queue and snapshot reader for opened tickets.

The inbox API enqueues only identity it has already verified: the customer
email and Shopify order names that Shopify reports for that customer. The Redo
read contract (tools/redo_mcp.py) carries no customer email, so a Redo return
is accepted only when its own order_name equals one of those verified orders.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

SNAPSHOT = Path('/var/lib/buttonsbebe-inbox2-redo/redo.sqlite3')
QUEUE = Path('/var/lib/buttonsbebe-inbox2/redo-requests.sqlite3')
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]{1,253}$")
ORDER_NAME = re.compile(r'^#?(\d{4,10})$')
MAX_ORDERS = 3
FOUND_TTL, EMPTY_TTL, STALE_AFTER = 30 * 60, 10 * 60, 6 * 3600


def database():
    return sqlite3.connect(QUEUE, timeout=2)


def initialize(db):
    db.execute('PRAGMA journal_mode=DELETE')
    db.execute('CREATE TABLE IF NOT EXISTS redo_requests (id TEXT PRIMARY KEY, request_key TEXT NOT NULL, request TEXT NOT NULL, requested_at REAL NOT NULL)')


def make_request(ticket_id, email, orders):
    """Normalize API-verified identity, or None when it is not exact."""
    email = (email or '').strip().casefold()
    if not isinstance(ticket_id, str) or not ticket_id or not EMAIL.fullmatch(email):
        return None
    names = []
    for order in orders or []:
        match = ORDER_NAME.fullmatch(str(order).strip())
        if not match:
            return None  # A malformed verified order means the caller's identity is suspect.
        if match.group(1) not in names:
            names.append(match.group(1))
    if not names or len(names) > MAX_ORDERS:
        return None
    return {'ticketId': ticket_id, 'email': email, 'orders': sorted(names)}


def request_key(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def fresh(payload, key, now):
    if not payload or payload.get('requestKey') != key:
        return False
    if payload.get('refreshError'):
        return now < payload.get('retryAt', 0)
    found = any(o.get('status') == 'observed' for o in payload.get('orders', {}).values())
    return now - payload.get('fetchedAtEpoch', 0) < (FOUND_TTL if found else EMPTY_TTL)


def read_snapshot(ticket_id, path=None):
    path = SNAPSHOT if path is None else path
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            row = db.execute('SELECT payload FROM redo WHERE ticket_id=?', (ticket_id,)).fetchone()
        payload = json.loads(row[0]) if row else None
        return payload if isinstance(payload, dict) else None
    except (sqlite3.Error, OSError, ValueError):
        return None


def view(request, payload, now):
    """Map a snapshot to one explicit UI state; never reuse another identity.

    Per order: 'observed'/'empty' are valid observations; an order whose Redo
    returns were all rejected for identity, that failed with no prior data, or
    that is absent is incomplete. No valid order -> unavailable; any incomplete
    or partly rejected order -> partial (never an all-clear); any refresh
    failure or old observation -> stale. Each order keeps its own observedAt.
    """
    key = request_key(request)
    if not payload or payload.get('requestKey') != key or payload.get('email') != request['email']:
        return {'source': 'redo', 'status': 'pending', 'requestedAt': now}
    orders = payload.get('orders') or {}
    valid = {n: o for n, o in orders.items() if n in request['orders'] and o.get('status') in ('observed', 'empty')}
    incomplete = sorted(n for n in request['orders'] if n not in valid or valid[n].get('rejected'))
    base = {'source': 'redo', 'orders': valid, 'incomplete': incomplete, 'fetchedAt': payload.get('fetchedAt'),
            'fetchedAtEpoch': payload.get('fetchedAtEpoch')}
    if not valid:
        return {**base, 'status': 'unavailable', 'reason': 'Redo could not be read or matched for this order.'}
    if incomplete:
        return {**base, 'status': 'partial', 'reason': 'Some Redo results are missing or could not be matched to this order.'}
    if payload.get('refreshError') or any(o.get('refreshFailed') or now - o.get('observedAtEpoch', 0) > STALE_AFTER
                                          for o in valid.values()):
        return {**base, 'status': 'stale'}
    status = 'observed' if any(o['status'] == 'observed' for o in valid.values()) else 'empty'
    return {**base, 'status': status}


def attach(ticket, email, orders, database=None, path=None, now=None):
    """Set ticket['redoDetails'] and enqueue a refresh when the snapshot is not fresh.

    database/path default to the module's QUEUE/SNAPSHOT at call time, so
    production callers pass neither; tests and fixtures must pass both.
    """
    database = globals()['database'] if database is None else database
    now = time.time() if now is None else now
    request = make_request(ticket.get('id'), email, orders)
    if not request:
        ticket['redoDetails'] = {'source': 'redo', 'status': 'unavailable',
                                 'reason': 'A verified customer email and Shopify order are needed to look up Redo returns.'}
        return ticket
    key = request_key(request)
    payload = read_snapshot(request['ticketId'], path)
    ticket['redoDetails'] = view(request, payload, now)
    if fresh(payload, key, now):
        return ticket
    with closing(database()) as db, db:
        db.execute('DELETE FROM redo_requests WHERE requested_at < ?', (now - 86400,))
        db.execute('''INSERT INTO redo_requests VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            request_key=excluded.request_key,request=excluded.request,requested_at=excluded.requested_at
            WHERE redo_requests.request_key != excluded.request_key OR redo_requests.requested_at < ?''',
                   (request['ticketId'], key, json.dumps(request), now, now - 10))
        db.execute('DELETE FROM redo_requests WHERE id IN (SELECT id FROM redo_requests ORDER BY requested_at DESC LIMIT -1 OFFSET 1000)')
    return ticket
