"""Fetch opened-ticket Redo returns through the existing read-only Redo MCP.

Holds no Redo credentials: it calls one fixed tool on the localhost MCP
service (buttonsbebe-redo-mcp) and atomically publishes a bounded snapshot.
"""
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
import threading
import time

from redo_details import QUEUE, SNAPSHOT, fresh, make_request, request_key

MCP_URL = 'http://127.0.0.1:8078/mcp'  # fixed: no caller-chosen URL, method, or tool
TOOL = 'get_returns_for_order'
# Canonical keys emitted by tools/redo_mcp.py _trim. Scalars are copied as-is;
# STRUCTURED values are copied through bounded(). _trim's notes, tags, order,
# dropoffs and id lists are deliberately not shown.
FIELDS = ('id', 'status', 'type', 'created_at', 'updated_at', 'order_name', 'complete_with_no_action',
          'refund_amount', 'store_credit_amount', 'tracking_number', 'tracking_url')
STRUCTURED = ('totals', 'refunds', 'compensation_methods', 'gift_cards', 'exchange', 'items', 'shipments', 'tracking')
NESTED_FIELDS = {'id', 'status', 'type', 'amount', 'refund', 'storeCredit',
                 'trackingNumber', 'trackingUrl', 'itemCount', 'createdAt', 'updatedAt',
                 'quantity', 'name', 'currency', 'currencyCode', 'carrier'}
MAX_DEPTH, MAX_LIST, MAX_KEYS, MAX_STR = 3, 10, 20, 200
MAX_RETURNS, MAX_SNAPSHOTS, MAX_LOOKUPS_PER_MINUTE = 10, 2000, 60
STOP = threading.Event()


class RedoUnavailable(Exception):
    pass


CAPACITY = threading.BoundedSemaphore(1)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class MCP:
    """Bounded JSON-RPC / SSE client to one fixed local MCP address."""
    def __enter__(self):
        CAPACITY.acquire()
        self.session = None
        self.sequence = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            self.rpc('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'inbox-redo-readonly','version':'1'}})
            self.rpc('notifications/initialized', {}, notification=True)
            return self
        except Exception:
            CAPACITY.release()
            raise
    def __exit__(self, *args):
        if self.session:
            # Deletes a local MCP transport session, never a Gorgias resource.
            try:
                request=urllib.request.Request(MCP_URL, method='DELETE', headers={'Mcp-Session-Id':self.session})
                with self.opener.open(request,timeout=3): pass
            except Exception: pass
        CAPACITY.release()
    def rpc(self, method, params, notification=False):
        self.sequence += 1
        payload={'jsonrpc':'2.0','method':method,'params':params}
        if not notification: payload['id']=self.sequence
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2024-11-05'}
        if self.session: headers['Mcp-Session-Id']=self.session
        request=urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers, method='POST')
        with self.opener.open(request,timeout=25) as response:
            self.session=response.headers.get('Mcp-Session-Id',self.session)
            if notification: return {}
            if 'text/event-stream' in response.headers.get('Content-Type',''):
                chunks=[];size=0;result=None
                for line in response:
                    size+=len(line)
                    if size>1024*1024: raise RedoUnavailable()
                    if line.startswith(b'data:'): chunks.append(line[5:].strip())
                    elif not line.strip() and chunks:
                        event=json.loads(b'\n'.join(chunks));chunks=[]
                        if event.get('id')==self.sequence: result=event;break
                if result is None: raise RedoUnavailable()
            else:
                raw=response.read(1024*1024+1)
                if len(raw)>1024*1024: raise RedoUnavailable()
                result=json.loads(raw)
        if result.get('error'): raise RedoUnavailable()
        return result.get('result',{})
    def call(self, tool, args):
        if tool not in {TOOL}: raise RedoUnavailable()
        result=self.rpc('tools/call', {'name':tool,'arguments':args})
        if result.get('isError'): raise RedoUnavailable()
        data=result.get('structuredContent')
        if not isinstance(data,dict):
            data=next((json.loads(c['text']) for c in result.get('content',[]) if c.get('type')=='text'),None)
        if not isinstance(data,dict): raise RedoUnavailable()
        if data.get('error'):
            if '404' in str(data['error']) or '410' in str(data['error']): raise RedoUnavailable()
            raise RedoUnavailable()
        return data


def call_mcp(order_name, timeout=20):
    with MCP() as client:
        return client.call(TOOL, {'order_name': order_name})


def order_digits(value):
    return str(value or '').strip().lstrip('#')


def bounded(value, depth=0):
    """Copy a nested Redo value within depth/list/key/string bounds, using reviewed field names."""
    if isinstance(value, str):
        return value[:MAX_STR]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= MAX_DEPTH:
        return None
    if isinstance(value, list):
        return [bounded(v, depth + 1) for v in value[:MAX_LIST]]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if len(out) >= MAX_KEYS:
                break
            k = str(k)[:40]
            if k in NESTED_FIELDS:
                out[k] = bounded(v, depth + 1)
        return out
    return None


def summarize(order, data, now):
    """Keep only allowlisted Redo fields for returns that name this exact order."""
    if not isinstance(data, dict) or 'error' in data or not isinstance(data.get('returns'), list):
        raise RedoUnavailable('unexpected Redo response')
    returns, rejected = [], 0
    for item in data['returns']:
        if not isinstance(item, dict) or order_digits(item.get('order_name')) != order:
            rejected += 1  # missing or mismatched order identity is never shown
            continue
        kept = {k: bounded(item[k]) for k in FIELDS if isinstance(item.get(k), (str, int, float, bool))}
        kept.update({k: bounded(item[k]) for k in STRUCTURED if isinstance(item.get(k), (dict, list))})
        kept['structuredFieldsUnavailable'] = [k for k in STRUCTURED if k in item and not kept.get(k)]
        kept['missing'] = [k for k in FIELDS + STRUCTURED if k not in kept]
        returns.append(kept)
    # Only rejected returns means Redo answered about other identities: not a clean empty.
    status = 'observed' if returns else 'rejected' if rejected else 'empty'
    return {'status': status, 'returns': returns[:MAX_RETURNS],
            'truncated': len(returns) > MAX_RETURNS, 'rejected': rejected,
            'observedAt': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'observedAtEpoch': now}


def read_requests(path=None, now=None):
    path = QUEUE if path is None else path
    now = time.time() if now is None else now
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        rows = db.execute('SELECT id,request_key,request,requested_at FROM redo_requests WHERE requested_at > ? ORDER BY requested_at DESC LIMIT 1000', (now - 86400,)).fetchall()
    result = []
    for ticket_id, key, raw, requested_at in rows:
        try:
            request = json.loads(raw)
            normalized = make_request(request.get('ticketId'), request.get('email'), request.get('orders'))
            if normalized != request or ticket_id != request['ticketId'] or request_key(request) != key:
                continue
            result.append((request, key, requested_at))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return result


def publish(cache, destination=None):
    """Atomically replace the bounded snapshot (readers see old or new, never partial).

    Never creates the parent: systemd StateDirectory owns it and its mode, so a
    missing directory raises OSError instead of a world-default mkdir.
    """
    destination = Path(SNAPSHOT if destination is None else destination)
    fd, name = tempfile.mkstemp(prefix='.redo-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.execute('CREATE TABLE redo(ticket_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at REAL NOT NULL)')
            for ticket_id, entry in sorted(cache.items(), key=lambda kv: kv[1]['updated_at'], reverse=True)[:MAX_SNAPSHOTS]:
                db.execute('INSERT INTO redo VALUES(?,?,?)', (ticket_id, json.dumps(entry['payload']), entry['updated_at']))
            db.commit()
        os.chmod(temporary, 0o640)
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def load_cache(path=None):
    path = SNAPSHOT if path is None else path
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            rows = db.execute('SELECT ticket_id,payload,updated_at FROM redo').fetchall()
        return {t: {'payload': json.loads(p), 'updated_at': u} for t, p, u in rows}
    except (sqlite3.Error, OSError, ValueError):
        return {}


class Worker:
    def __init__(self, destination=None, call=call_mcp, publish=publish):
        destination = SNAPSHOT if destination is None else destination
        self.destination, self.call, self.publish = destination, call, publish
        self.cache, self.window_at, self.lookups = load_cache(destination), 0, 0

    def process(self, requests, now=None):
        now = time.time() if now is None else now
        if now - self.window_at >= 60:
            self.window_at, self.lookups = now, 0
        completed = 0
        for request, key, requested_at in requests:
            # Reserve the whole request so one ticket never exceeds the window.
            if STOP.is_set() or self.lookups + len(request['orders']) > MAX_LOOKUPS_PER_MINUTE:
                break
            old = self.cache.get(request['ticketId'], {}).get('payload', {})
            if fresh(old, key, now):
                continue
            if old.get('requestKey') != key:
                old = {}  # never carry results across identities
            elif requested_at <= old.get('attemptedAt', 0):
                continue
            orders, failed = dict(old.get('orders', {})), False
            for order in request['orders']:
                self.lookups += 1
                try:
                    orders[order] = summarize(order, self.call(order), now)
                except Exception as error:
                    logging.warning('Redo details lookup failed (%s)', type(error).__name__)
                    failed = True
                    # Keep the prior observation with its own timestamp, marked failed.
                    previous = orders.get(order)
                    orders[order] = ({**previous, 'refreshFailed': True, 'refreshFailedAt': now}
                                     if previous and previous.get('status') in ('observed', 'empty')
                                     else {'status': 'failed', 'refreshFailedAt': now})
            payload = {'requestKey': key, 'email': request['email'], 'orders': orders, 'attemptedAt': now}
            if failed:
                failures = min(old.get('failures', 0) + 1, 5)
                payload.update(refreshError=True, failures=failures, retryAt=now + min(30 * 2 ** (failures - 1), 300),
                               fetchedAt=old.get('fetchedAt'), fetchedAtEpoch=old.get('fetchedAtEpoch', 0))
            else:
                payload.update(fetchedAt=datetime.fromtimestamp(now, timezone.utc).isoformat(), fetchedAtEpoch=now)
            self.cache[request['ticketId']] = {'payload': payload, 'updated_at': now}
            completed += 1
        if completed:
            self.publish(self.cache, self.destination)
            # Bounded history: drop entries the snapshot no longer holds.
            keep = sorted(self.cache.items(), key=lambda kv: kv[1]['updated_at'], reverse=True)[:MAX_SNAPSHOTS]
            self.cache = dict(keep)
        return completed


def main():
    worker = Worker()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: STOP.set())
    while not STOP.is_set():
        try:
            completed = worker.process(read_requests())
            if completed:
                logging.info('Published Redo details for %d requested tickets', completed)
        except (sqlite3.Error, OSError):
            logging.warning('Redo detail queue temporarily unavailable')
        STOP.wait(1)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    main()
