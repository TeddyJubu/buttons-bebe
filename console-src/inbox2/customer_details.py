"""Credential-free request queue and snapshot reader for opened inbox tickets."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

SNAPSHOT = Path('/var/lib/buttonsbebe-inbox2-shop/rail.sqlite3')
QUEUE = Path('/var/lib/buttonsbebe-inbox2/customer-requests.sqlite3')
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]{1,253}$")
ORDER = re.compile(r'(?i)(?:\border\s*#?\s*(\d{4,10})\b|#(\d{4,7})\b)')


def database():
    return sqlite3.connect(QUEUE, timeout=2)


def initialize(db):
    # A dedicated DELETE-journal queue can be read through a read-only mount.
    # The live Gorgias cache uses WAL, whose transient sidecars require writes.
    db.execute('PRAGMA journal_mode=DELETE')
    db.execute('CREATE TABLE IF NOT EXISTS shop_requests (id TEXT PRIMARY KEY, request_key TEXT NOT NULL, ticket TEXT NOT NULL, requested_at REAL NOT NULL)')


def request_ticket(ticket):
    context = ticket.get('customerContext') or {}
    identity = context.get('identity') or {}
    email = (identity.get('email') or ticket.get('fromEmail') or '').strip().casefold()
    if context.get('conflict') or not EMAIL.fullmatch(email):
        return None
    names = []
    for body in [ticket.get('subject', ''), *[m.get('body', '') for m in ticket.get('messages', []) if isinstance(m, dict)]]:
        for match in ORDER.finditer(body or ''):
            name = match.group(1) or match.group(2)
            if name not in names:
                names.append(name)
            if len(names) == 3:
                break
        if len(names) == 3:
            break
    # Store only verified identity and bounded order references, never entire messages.
    return {'id': ticket['id'], 'fromEmail': email, 'subject': ' '.join('Order ' + name for name in names)}


def request_key(ticket):
    return hashlib.sha256(json.dumps(ticket, sort_keys=True).encode()).hexdigest()


def fresh(payload, key, now):
    if not payload or payload.get('requestKey') != key:
        return False
    if payload.get('refreshError'):
        return now < payload.get('retryAt', 0)
    ttl = 6 * 3600 if payload.get('status') == 'found' else 30 * 60
    return now - payload.get('fetchedAtEpoch', 0) < ttl


def read_snapshot(ticket_id, path=SNAPSHOT):
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            row = db.execute('SELECT payload FROM rail WHERE ticket_id=?', (ticket_id,)).fetchone()
        payload = json.loads(row[0]) if row else None
        return payload if isinstance(payload, dict) else None
    except (sqlite3.Error, OSError, ValueError):
        return None


def attach(ticket, database, path=SNAPSHOT, now=None):
    now = time.time() if now is None else now
    request = request_ticket(ticket)
    if not request:
        ticket['shopifyRail'] = {'status': 'unavailable', 'reason': 'A consistent customer email is needed to look up Shopify details.'}
        return ticket
    key = request_key(request)
    payload = read_snapshot(ticket['id'], path)
    if payload and payload.get('requestKey') == key and payload.get('email', '').casefold() == request['fromEmail']:
        payload['stale'] = bool(payload.get('refreshError')) or now - payload.get('fetchedAtEpoch', 0) > 6 * 3600
        ticket['shopifyRail'] = payload
        if fresh(payload, key, now):
            return ticket
    elif ticket.get('shopifyRail', {}).get('requestKey'):
        ticket.pop('shopifyRail', None)
    # Legacy snapshots may come from an older/partial conversation. Display them
    # while looking up the full live ticket, but do not use them to skip the queue.
    legacy = ticket.get('shopifyRail', {})
    with closing(database()) as db, db:
        db.execute('DELETE FROM shop_requests WHERE requested_at < ?', (now - 86400,))
        db.execute('''INSERT INTO shop_requests VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            request_key=excluded.request_key,ticket=excluded.ticket,requested_at=excluded.requested_at
            WHERE shop_requests.request_key != excluded.request_key OR shop_requests.requested_at < ?''',
            (ticket['id'], key, json.dumps(request), now, now - 10))
        db.execute('DELETE FROM shop_requests WHERE id IN (SELECT id FROM shop_requests ORDER BY requested_at DESC LIMIT -1 OFFSET 1000)')
    if legacy.get('customer') or legacy.get('order'):
        ticket['shopifyRail'] = {**legacy, 'refreshing': True, 'stale': True}
    else:
        ticket['shopifyRail'] = {'status': 'loading', 'requestedAt': now}
    return ticket
