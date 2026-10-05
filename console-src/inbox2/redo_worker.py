"""Fetch opened-ticket Redo returns through the existing read-only Redo MCP.

Holds no Redo credentials: it calls one fixed tool on the localhost MCP
service (buttonsbebe-redo-mcp) and atomically publishes a bounded snapshot.
"""
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
import json
import http.client
import math
import socket
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
# Leave room under the 128 MiB service limit for decoded responses, Python objects
# and a staged replacement cache. Bound entire serialized entries, not fields alone.
MAX_CACHE_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_SESSION_SECONDS = 25
STOP = threading.Event()


class RedoUnavailable(Exception):
    pass


CAPACITY = threading.BoundedSemaphore(1)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class DeadlineConnection(http.client.HTTPConnection):
    def __init__(self, owner, *args, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)

    def connect(self):
        super().connect()
        with self.owner.socket_lock:
            self.owner.sockets.append(self.sock)
            expired = self.owner.expired.is_set()
        if expired:
            self.owner.abort()
            raise RedoUnavailable()


class DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, owner):
        self.owner = owner
        super().__init__()

    def http_open(self, request):
        def connection(*args, **kwargs):
            kwargs['timeout'] = self.owner.remaining()
            return DeadlineConnection(self.owner, *args, **kwargs)
        return self.do_open(connection, request)


class MCP:
    """One total deadline for capacity, initialize, notification, read and cleanup.

    Socket shutdown at the deadline also interrupts trickled SSE/header reads;
    per-socket inactivity timeouts alone cannot bound a complete session.
    """
    def __init__(self, timeout=20):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= MAX_SESSION_SECONDS:
            raise ValueError('Redo timeout must be positive and at most 25 seconds')
        self.timeout = timeout
        self.session, self.sequence = None, 0
        self.sockets, self.socket_lock = [], threading.Lock()
        self.expired = threading.Event()
        self.timer = None
        self.acquired = False

    def remaining(self):
        value = self.deadline - time.monotonic()
        if self.expired.is_set() or value <= 0:
            raise RedoUnavailable()
        return value

    def abort(self):
        self.expired.set()
        with self.socket_lock:
            for connection in self.sockets:
                try: connection.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                try: connection.close()
                except OSError: pass

    def __enter__(self):
        self.deadline = time.monotonic() + self.timeout
        if not CAPACITY.acquire(timeout=self.remaining()):
            raise RedoUnavailable()
        self.acquired = True
        try:
            self.timer = threading.Timer(self.remaining(), self.abort)
            self.timer.daemon = True
            self.timer.start()
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), DeadlineHTTPHandler(self))
            self.rpc('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'inbox-redo-readonly','version':'1'}})
            self.rpc('notifications/initialized', {}, notification=True)
            return self
        except Exception:
            self.finish()
            raise

    def finish(self):
        try:
            if self.session:
                # Only the local MCP transport session is deleted.
                try:
                    request=urllib.request.Request(MCP_URL, method='DELETE', headers={'Mcp-Session-Id':self.session})
                    with self.opener.open(request,timeout=self.remaining()): pass
                except Exception: pass
        finally:
            if self.timer: self.timer.cancel()
            self.abort()
            if self.acquired:
                self.acquired = False
                CAPACITY.release()

    def __exit__(self, *args):
        self.finish()

    def rpc(self, method, params, notification=False):
        self.sequence += 1
        payload={'jsonrpc':'2.0','method':method,'params':params}
        if not notification: payload['id']=self.sequence
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2024-11-05'}
        if self.session: headers['Mcp-Session-Id']=self.session
        request=urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=headers, method='POST')
        with self.opener.open(request,timeout=self.remaining()) as response:
            self.remaining()
            self.session=response.headers.get('Mcp-Session-Id',self.session)
            if notification: return {}
            size, body, lines, result = 0, bytearray(), [], None
            sse = 'text/event-stream' in response.headers.get('Content-Type','')
            while True:
                self.remaining()
                chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES: raise RedoUnavailable()
                if not chunk: break
                body.extend(chunk)
                if sse:
                    while b'\n' in body:
                        line, _, rest = body.partition(b'\n'); body = bytearray(rest)
                        if line.startswith(b'data:'): lines.append(line[5:].strip())
                        elif not line.strip() and lines:
                            event=json.loads(b'\n'.join(lines));lines=[]
                            if isinstance(event,dict) and event.get('id')==self.sequence:
                                result=event;break
                    if result is not None: break
            self.remaining()
            if not sse: result=json.loads(body)
        if not isinstance(result,dict) or result.get('jsonrpc')!='2.0' or result.get('id')!=self.sequence or result.get('error'):
            raise RedoUnavailable()
        return result.get('result',{})

    def call(self, tool, args):
        if tool not in {TOOL}: raise RedoUnavailable()
        result=self.rpc('tools/call', {'name':tool,'arguments':args})
        if not isinstance(result,dict) or result.get('isError'): raise RedoUnavailable()
        data=result.get('structuredContent')
        if not isinstance(data,dict):
            data=next((json.loads(c['text']) for c in result.get('content',[]) if c.get('type')=='text'),None)
        if not isinstance(data,dict) or data.get('error'): raise RedoUnavailable()
        return data


def call_mcp(order_name, timeout=20):
    try:
        with MCP(timeout=timeout) as client:
            return client.call(TOOL, {'order_name': order_name})
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise RedoUnavailable() from error


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


def _has_observed_value(value):
    if isinstance(value, dict): return any(_has_observed_value(v) for v in value.values())
    if isinstance(value, list): return any(_has_observed_value(v) for v in value)
    return value is not None


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
        kept['structuredFieldsUnavailable'] = [k for k in STRUCTURED if k in item and
            (k not in kept or (bool(item[k]) and not _has_observed_value(kept[k])))]
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


def serialized_size(ticket_id, entry):
    return len(json.dumps({'ticketId':ticket_id, **entry},ensure_ascii=False,separators=(',',':')).encode())


def bounded_cache(cache):
    """Newest whole entries fit both the count and aggregate UTF-8 byte budget."""
    retained, size = {}, 0
    for ticket_id, entry in sorted(cache.items(), key=lambda item:(item[1]['updated_at'], item[0]), reverse=True):
        entry_size=serialized_size(ticket_id,entry)
        if len(retained)>=MAX_SNAPSHOTS: break
        if size+entry_size>MAX_CACHE_BYTES: continue
        retained[ticket_id]=entry;size+=entry_size
    return retained


def _fsync_directory(directory):
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def publish(cache, destination=None):
    """Atomic old/new publication, retaining the prior inode until directory fsync.

    A publication failure restores the prior snapshot and does not create its
    parent. Runtime ownership/modes remain provided by systemd StateDirectory.
    """
    destination = Path(SNAPSHOT if destination is None else destination)
    fd, name = tempfile.mkstemp(prefix='.redo-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd);temporary=Path(name)
    backup=None;replaced=False;recovery_failed=False
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.execute('CREATE TABLE redo(ticket_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at REAL NOT NULL)')
            for ticket_id,entry in bounded_cache(cache).items():
                db.execute('INSERT INTO redo VALUES(?,?,?)',(ticket_id,json.dumps(entry['payload'],ensure_ascii=False,separators=(',',':')),entry['updated_at']))
            db.commit()
        os.chmod(temporary,0o640)
        with temporary.open('rb') as handle: os.fsync(handle.fileno())
        if destination.exists():
            fd,name=tempfile.mkstemp(prefix='.redo-before-',suffix='.sqlite3',dir=destination.parent)
            os.close(fd);backup=Path(name);backup.unlink()
            os.link(destination,backup,follow_symlinks=False)
        os.replace(temporary,destination);replaced=True
        _fsync_directory(destination.parent)
    except Exception:
        if replaced:
            try:
                if backup is not None: os.replace(backup,destination);backup=None
                else: destination.unlink(missing_ok=True)
                _fsync_directory(destination.parent)
            except Exception:
                recovery_failed=True  # Retain the prior protected inode for recovery.
                raise
        raise
    finally:
        temporary.unlink(missing_ok=True)
        if backup is not None and not recovery_failed:
            try: backup.unlink(missing_ok=True)
            except OSError:
                # The new snapshot is already durable; retain the old inode for cleanup.
                logging.warning('Redo snapshot backup cleanup deferred')


def _load_cache(path):
    cache,size={},0
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            total=db.execute('SELECT count(*) FROM redo').fetchone()[0]
            rows=db.execute('SELECT ticket_id,payload,updated_at FROM redo WHERE length(CAST(payload AS BLOB))<=? ORDER BY updated_at DESC,ticket_id DESC LIMIT ?', (MAX_CACHE_BYTES,MAX_SNAPSHOTS))
            for ticket_id,payload,updated_at in rows:
                if len(payload.encode())>MAX_CACHE_BYTES: continue
                value=json.loads(payload)
                if not isinstance(value,dict): continue
                entry={'payload':value,'updated_at':updated_at}
                entry_size=serialized_size(ticket_id,entry)
                if size+entry_size>MAX_CACHE_BYTES: continue
                cache[ticket_id]=entry;size+=entry_size
        return cache, len(cache)!=total
    except (sqlite3.Error,OSError,ValueError):
        return {},False


def load_cache(path=None):
    return _load_cache(SNAPSHOT if path is None else path)[0]


class Worker:
    def __init__(self, destination=None, call=call_mcp, publish=publish):
        destination = SNAPSHOT if destination is None else destination
        self.destination, self.call, self.publish = destination, call, publish
        cache,trimmed=_load_cache(destination)
        if trimmed: self.publish(cache,self.destination)
        self.cache, self.window_at, self.lookups = cache, 0, 0

    def process(self, requests, now=None):
        now = time.time() if now is None else now
        if now - self.window_at >= 60:
            self.window_at, self.lookups = now, 0
        completed = 0
        working=dict(self.cache)
        for request, key, requested_at in requests:
            # Reserve the whole request so one ticket never exceeds the window.
            if STOP.is_set() or self.lookups + len(request['orders']) > MAX_LOOKUPS_PER_MINUTE:
                break
            old = working.get(request['ticketId'], {}).get('payload', {})
            if fresh(old, key, now):
                continue
            if old.get('requestKey') != key:
                old = {}  # never carry results across identities
            elif requested_at <= old.get('attemptedAt', 0):
                continue
            orders, failed, interrupted = dict(old.get('orders', {})), False, False
            for order in request['orders']:
                if STOP.is_set():
                    interrupted=True;break
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
            if interrupted: break  # Keep prior observations; skipped orders cannot become fresh.
            payload = {'requestKey': key, 'email': request['email'], 'orders': orders, 'attemptedAt': now}
            if failed:
                failures = min(old.get('failures', 0) + 1, 5)
                payload.update(refreshError=True, failures=failures, retryAt=now + min(30 * 2 ** (failures - 1), 300),
                               fetchedAt=old.get('fetchedAt'), fetchedAtEpoch=old.get('fetchedAtEpoch', 0))
            else:
                payload.update(fetchedAt=datetime.fromtimestamp(now, timezone.utc).isoformat(), fetchedAtEpoch=now)
            working[request['ticketId']] = {'payload': payload, 'updated_at': now}
            working=bounded_cache(working)
            completed += 1
        if completed:
            self.publish(working, self.destination)
            self.cache=working
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
