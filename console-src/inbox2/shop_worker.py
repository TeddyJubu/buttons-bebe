"""Fetch opened-ticket Shopify details using the existing fixed read-only queries.

Runs outside the credential-free inbox service. Reads its bounded request queue
and atomically publishes an independent snapshot; the legacy exporter cannot erase it.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
import threading
import time

INBOX_MODULES = Path(__file__).resolve().parent.parent / 'inbox'
if not (INBOX_MODULES / 'projection.py').is_file():
    INBOX_MODULES = Path('/opt/buttonsbebe/inbox/console-src/inbox')
sys.path.insert(0, str(INBOX_MODULES))
import export_shop_rail as exporter
from customer_details import QUEUE, SNAPSHOT, fresh, request_key, request_ticket

STOP = threading.Event()


def read_requests(path=QUEUE):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        rows = db.execute('SELECT id,request_key,ticket,requested_at FROM shop_requests WHERE requested_at > ? ORDER BY requested_at DESC LIMIT 1000', (time.time() - 86400,)).fetchall()
    result = []
    for ticket_id, key, raw, requested_at in rows:
        try:
            ticket = json.loads(raw)
            normalized = request_ticket(ticket)
            if normalized != ticket or ticket_id != ticket['id'] or request_key(ticket) != key:
                continue
            result.append((ticket, key, requested_at))
        except (ValueError, TypeError, KeyError):
            continue
    return result


def publish(cache, destination=SNAPSHOT):
    destination = Path(destination)
    fd, name = tempfile.mkstemp(prefix='.rail-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE rail(ticket_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at REAL NOT NULL)')
            for ticket_id, entry in sorted(cache.items(), key=lambda item: item[1]['updated_at'], reverse=True)[:2000]:
                db.execute('INSERT INTO rail VALUES(?,?,?)', (ticket_id, json.dumps(entry['payload']), entry['updated_at']))
            db.commit()
        os.chmod(temporary, 0o640)
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


class Worker:
    def __init__(self, env, destination=SNAPSHOT, graphql=None, mint=None):
        self.env, self.destination = env, destination
        self.graphql, self.mint = graphql or exporter.graphql, mint or exporter.mint_token
        self.cache = exporter.load_cache(destination)
        self.token = None
        self.token_at = 0
        self.window_at = 0
        self.caches = self.new_caches()

    def new_caches(self):
        return {'customers': {}, 'orders': {}, 'history': {}, 'lookups': 0, 'graphql': self.graphql}

    def process(self, requests, now=None):
        now = time.time() if now is None else now
        if now - self.window_at >= 60:
            self.window_at = now
            self.caches = self.new_caches()
        completed = 0
        for ticket, key, requested_at in requests:
            if STOP.is_set() or self.caches['lookups'] >= exporter.MAX_LOOKUPS:
                break
            entry = self.cache.get(ticket['id'], {})
            old = entry.get('payload', {})
            if fresh(old, key, now):
                continue
            if old.get('requestKey') != key:
                old = {}
            elif requested_at <= old.get('attemptedAt', 0):
                continue  # Refresh only tickets requested since the previous lookup.
            try:
                if not self.token or now - self.token_at > 23 * 3600:
                    self.token = self.mint(self.env)
                    self.token_at = now
                payload, _ = exporter.lookup_ticket(self.env, self.token, ticket, self.caches)
                payload.update(fetchedAt=datetime.fromtimestamp(now, timezone.utc).isoformat(), fetchedAtEpoch=now)
            except exporter.LookupBudgetExceeded:
                break
            except Exception as error:
                # Never put secrets, upstream bodies, or customer data in logs.
                logging.warning('Shopify details lookup failed (%s)', type(error).__name__)
                failures = min(old.get('failures', 0) + 1, 5)
                payload = {**old, 'status': old.get('status', 'error'), 'email': ticket['fromEmail'],
                           'refreshError': True, 'failures': failures, 'retryAt': now + min(30 * 2 ** (failures - 1), 300)}
                if not self.token:
                    self.caches['lookups'] = exporter.MAX_LOOKUPS
                if getattr(error, 'code', None) == 401:
                    self.token = None
            payload.update(requestKey=key, attemptedAt=now)
            self.cache[ticket['id']] = {'payload': payload, 'updated_at': now}
            publish(self.cache, self.destination)
            completed += 1
        return completed


def main():
    env_file = os.environ.get('SHOP_RAIL_ENV_FILE')
    if not env_file:
        raise SystemExit('SHOP_RAIL_ENV_FILE is required')
    worker = Worker(exporter.load_shopify_env(env_file))
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: STOP.set())
    while not STOP.is_set():
        try:
            completed = worker.process(read_requests())
            if completed:
                logging.info('Published customer details for %d requested tickets', completed)
        except (sqlite3.Error, OSError):
            logging.warning('Customer detail queue temporarily unavailable')
        STOP.wait(1)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    main()
