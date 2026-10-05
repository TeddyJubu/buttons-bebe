"""Inbox: credential-free Gorgias reads through the local GET-only MCP.

The public API is authenticated by Caddy. Only three named read operations are
accepted. The process has loopback-only network access and read-only source
snapshots. Local SQLite stores synchronization state, never provider mutations.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from dataclasses import dataclass, field
import json
import logging
import math
import os
import re
from pathlib import Path
import sqlite3
import sys
import threading
import time
import urllib.request
import uuid
from urllib.error import HTTPError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool

INBOX_MODULES = Path(__file__).resolve().parent.parent / 'inbox'
if not (INBOX_MODULES / 'projection.py').is_file():
    INBOX_MODULES = Path('/opt/buttonsbebe/inbox/console-src/inbox')
sys.path.insert(0, str(INBOX_MODULES))
import projection
from projection import query as projection_query, ProjectionUnavailable
from shop_rail import attach as attach_shop_rail
import customer_details
import redo_details

OPERATOR_EMAIL = os.environ.get('INBOX_OPERATOR_GORGIAS_EMAIL', '').strip().casefold()
DB = Path('/var/lib/buttonsbebe-inbox2/live.sqlite3')
MCP_URL = 'http://127.0.0.1:8079/mcp'
WORKER_LOCK = threading.Lock()
WORKER = None
CAPACITY_TIMEOUT = 30
SHUTDOWN_TIMEOUT = 29
PHASE_LIMITS = {'starting': 30, 'scanning': 90, 'capacity': 30, 'transport': 30,
                'writing': 20, 'sleeping': 45, 'backoff': 315, 'stopping': 30}
READ_TIMEOUT = .2
CAPACITY = threading.BoundedSemaphore(2)
DETAIL_LOCK = threading.Lock()
DETAIL_CACHE = {}

class Unavailable(Exception): pass
class Gone(Exception): pass
class Cancelled(Exception): pass

@dataclass
class Worker:
    worker_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: threading.Thread | None = None
    phase: str = 'starting'
    phase_since: float = field(default_factory=time.monotonic)
    progress_since: float = field(default_factory=time.monotonic)
    progress_at: str | None = None
    success_at: str | None = None
    error: bool = False
    metadata_error: bool = False
    shutdown_timeout: bool = False

    def update(self, phase, *, progress=False, success=False, **changes):
        with self.lock:
            self.phase = phase
            self.phase_since = time.monotonic()
            if progress:
                self.progress_at = now()
                self.progress_since = time.monotonic()
            if success: self.success_at = now()
            for key, value in changes.items(): setattr(self, key, value)

    def snapshot(self):
        with self.lock:
            return {'workerId': self.worker_id, 'alive': bool(self.thread and self.thread.is_alive()), 'phase': self.phase,
                    'elapsed': time.monotonic() - self.phase_since,
                    'progressElapsed': time.monotonic() - self.progress_since, 'progressAt': self.progress_at,
                    'successAt': self.success_at, 'error': self.error,
                    'metadataError': self.metadata_error, 'shutdownTimeout': self.shutdown_timeout}


def now(): return datetime.now(timezone.utc).isoformat()
def epoch(value):
    try: return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError): return 0

def source_epoch(value):
    if not isinstance(value,str) or not value.strip(): return 0
    try:
        parsed=datetime.fromisoformat(value.strip())
        return parsed.timestamp() if parsed.tzinfo is not None and parsed.utcoffset() is not None else 0
    except (ValueError,OverflowError,OSError): return 0

def database():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with closing(database()) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, updated REAL, payload TEXT, generation TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS ticket_updated ON tickets(updated DESC,id)')
        db.execute("CREATE INDEX IF NOT EXISTS ticket_category_updated ON tickets(coalesce(json_extract(payload,'$.trashed'),0),coalesce(json_extract(payload,'$.spam'),0),updated DESC,id)")
        db.execute("CREATE INDEX IF NOT EXISTS ticket_category_status_updated ON tickets(coalesce(json_extract(payload,'$.trashed'),0),coalesce(json_extract(payload,'$.spam'),0),json_extract(payload,'$.status'),updated DESC,id)")
        for key in ('spam','trash','snoozed'):
            db.execute("CREATE INDEX IF NOT EXISTS ticket_available_"+key+" ON tickets(id) WHERE json_extract(payload,'$.categoryAvailability."+key+"')=1")
        db.execute('CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT)')
        db.execute("INSERT OR IGNORE INTO meta VALUES(1, '{}')")
    with closing(customer_details.database()) as db, db:
        customer_details.initialize(db)
    with closing(redo_details.database()) as db, db:
        redo_details.initialize(db)

def get_meta(db): return json.loads(db.execute('SELECT payload FROM meta WHERE id=1').fetchone()[0])
def set_meta(db, meta): db.execute('UPDATE meta SET payload=? WHERE id=1', (json.dumps(meta),))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class MCP:
    """Bounded JSON-RPC / SSE client to one fixed local MCP address."""
    def __init__(self, worker=None):
        self.worker = worker

    def check_stop(self):
        if self.worker and self.worker.stop.is_set(): raise Cancelled()

    def __enter__(self):
        if self.worker: self.worker.update('capacity')
        deadline = time.monotonic() + CAPACITY_TIMEOUT
        while True:
            self.check_stop()
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise Unavailable()
            if CAPACITY.acquire(timeout=min(.2, remaining)): break
        self.session = None
        self.sequence = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            self.rpc('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'inbox-readonly','version':'1'}})
            self.rpc('notifications/initialized', {}, notification=True)
            return self
        except Exception:
            CAPACITY.release()
            raise
    def __exit__(self, *args):
        if self.session and not (self.worker and self.worker.stop.is_set()):
            # Deletes a local MCP transport session, never a Gorgias resource.
            try:
                request=urllib.request.Request(MCP_URL, method='DELETE', headers={'Mcp-Session-Id':self.session})
                with self.opener.open(request,timeout=3): pass
            except Exception: pass
        CAPACITY.release()
    def rpc(self, method, params, notification=False):
        self.check_stop()
        if self.worker: self.worker.update('transport')
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
                    self.check_stop()
                    size+=len(line)
                    if size>8*1024*1024: raise Unavailable()
                    if line.startswith(b'data:'): chunks.append(line[5:].strip())
                    elif not line.strip() and chunks:
                        event=json.loads(b'\n'.join(chunks));chunks=[]
                        if event.get('id')==self.sequence: result=event;break
                if result is None: raise Unavailable()
            else:
                raw=response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024: raise Unavailable()
                result=json.loads(raw)
        self.check_stop()
        if result.get('error'): raise Unavailable()
        return result.get('result',{})
    def call(self, tool, args):
        if tool not in {'list_inbox_tickets','get_ticket','get_ticket_messages'}: raise Unavailable()
        result=self.rpc('tools/call', {'name':tool,'arguments':args})
        if result.get('isError'): raise Unavailable()
        data=result.get('structuredContent')
        if not isinstance(data,dict):
            data=next((json.loads(c['text']) for c in result.get('content',[]) if c.get('type')=='text'),None)
        if not isinstance(data,dict): raise Unavailable()
        if data.get('error'):
            if '404' in str(data['error']) or '410' in str(data['error']): raise Gone()
            raise Unavailable()
        return data

def summary(t):
    customer=t.get('customer') or {};user=t.get('assignee_user') or {};team=t.get('assignee_team') or {}
    if not isinstance(customer,dict): customer={}
    if not isinstance(user,dict): user={}
    if not isinstance(team,dict): team={}
    return {'id':'gorgias:'+str(t['id']), 'subject':t.get('subject') or '',
            'customerName':customer.get('name') or ' '.join(filter(None,[customer.get('firstname'),customer.get('lastname')])) or customer.get('email') or '',
            'fromEmail':customer.get('email') or '', 'status':t.get('status') or '',
            'gorgiasPriority':t.get('priority') or '', 'assignee':user.get('name') or team.get('name') or 'unassigned',
            'assigneeEmail':user.get('email') or '', 'assigneeTeam':team.get('name') or '',
            'channel':t.get('channel') or '', 'updatedAt':t.get('updated_datetime') or t.get('created_datetime'),
            'snippet':t.get('display_text') if t.get('cleanup_version') else t.get('excerpt') or '',
            'previewProvenance':{'source':t.get('display_source') or 'excerpt','truncated':bool(t.get('source_truncated',True)),'cleanupVersion':t.get('cleanup_version')}, 'tags':[x.get('name','') if isinstance(x,dict) else str(x) for x in t.get('tags') or []],
            'spam':bool(t.get('spam')), 'trashed':bool(t.get('trashed_datetime')),
            'snoozedUntil':t.get('snooze_datetime'), 'lastMessageAt':t.get('last_message_datetime'),
            'categoryAvailability':{key:field in t for key,field in [('spam','spam'),('trash','trashed_datetime'),('snoozed','snooze_datetime')]}, 'source':'gorgias_api',
            'customerContext':{'source':'gorgias_api','status':'observed','conflict':False,
                               'identity':{'email':customer.get('email') or '', 'name':customer.get('name') or ''},'observedAt':now()}}

def cache_summary(db,ticket,generation,preserve_preview=True):
    old=db.execute('SELECT payload FROM tickets WHERE id=?',(ticket['id'],)).fetchone()
    if old and preserve_preview:  # summary syncs keep a detail preview; a fresh covered detail may replace it
        prior=json.loads(old[0])
        if prior.get('previewMessageId') and prior.get('lastMessageAt') and prior.get('lastMessageAt')==ticket.get('lastMessageAt'):
            for key in ('snippet','previewMessageId','previewProvenance'):
                if key in prior:ticket[key]=prior[key]
    db.execute('INSERT INTO tickets VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,payload=excluded.payload,generation=excluded.generation',
               (ticket['id'],epoch(ticket['updatedAt']),json.dumps(ticket),generation))
    # Keep only bounded trash summaries. Never mutate provider records.
    db.execute("DELETE FROM tickets WHERE id IN (SELECT id FROM tickets WHERE json_extract(payload,'$.trashed')=1 ORDER BY updated DESC,id LIMIT -1 OFFSET 1000)")

def sync_once(worker):
    worker.update('scanning')
    with closing(database()) as db:
        meta=get_meta(db)
    start=now();watermark=float(meta.get('watermark',0))
    full=not meta.get('complete') or time.time()-float(meta.get('fullAt',0))>21600
    pending=meta.get('pendingFull') or {}
    generation=(pending.get('generation') or start) if full else meta.get('generation','')
    cursor=pending.get('cursor') if full else None
    seen=set();newest=max(watermark,float(pending.get('watermark',0)) if full else 0)
    last_head=time.monotonic()
    with closing(database()) as db, db:
        meta.update(syncing=True);set_meta(db,meta)
    while not worker.stop.is_set():
        with MCP(worker) as client: result=client.call('list_inbox_tickets',{'limit':100,**({'cursor':cursor} if cursor else {})})
        rows=result.get('data')
        if not isinstance(rows,list): raise Unavailable()
        next_cursor=(result.get('meta') or {}).get('next_cursor')
        worker.update('writing')
        with closing(database()) as db, db:
            for raw in rows:
                ticket=summary(raw);updated=epoch(ticket['updatedAt']);newest=max(newest,updated)
                cache_summary(db,ticket,generation)
            meta=get_meta(db);meta.update(lastPageAt=now(),syncing=True)
            if cursor is None: meta['lastHeadAt']=now()
            if full:meta['pendingFull']={'cursor':next_cursor,'generation':generation,'watermark':newest}
            set_meta(db,meta)
        worker.update('scanning', progress=True)
        reached_old=not full and rows and all(epoch(x.get('updated_datetime') or x.get('created_datetime'))<watermark-120 for x in rows)
        if not next_cursor or reached_old:
            worker.update('writing')
            with closing(database()) as db, db:
                if full: db.execute('DELETE FROM tickets WHERE generation<>?',(generation,))
                meta=get_meta(db);meta.update(complete=True,syncing=False,error=False,generatedAt=now(),lastCompletedAt=now(),watermark=newest,generation=generation)
                if full:
                    meta['fullAt']=time.time();meta.pop('pendingFull',None)
                set_meta(db,meta)
            worker.update('scanning', progress=True, success=True, error=False, metadata_error=False)
            return
        if next_cursor in seen: raise Unavailable()
        seen.add(next_cursor);cursor=next_cursor
        # Keep the newest page fresh during a long historical backfill.
        if full and time.monotonic()-last_head>=30:
            with MCP(worker) as client: head=client.call('list_inbox_tickets',{'limit':100})
            if not isinstance(head.get('data'),list): raise Unavailable()
            worker.update('writing')
            with closing(database()) as db, db:
                for raw in head['data']:
                    ticket=summary(raw)
                    cache_summary(db,ticket,generation)
                meta=get_meta(db);meta.update(generatedAt=now(),lastHeadAt=now());set_meta(db,meta)
            last_head=time.monotonic()
        worker.update('scanning')
        if worker.stop.wait(.6): return

def sync_loop(worker):
    delay=30
    quick_retry_available=True
    try:
        while not worker.stop.is_set():
            try:
                sync_once(worker)
                if worker.stop.is_set(): break
                delay=30
                quick_retry_available=True
                worker.update('sleeping')
            except Cancelled:
                break
            except Exception:
                worker.update('writing', error=True)
                logging.getLogger('inbox').warning('Gorgias read sync unavailable; retaining readable ticket rows')
                try:
                    with closing(database()) as db, db:
                        meta=get_meta(db);meta.update(error=True,syncing=False);set_meta(db,meta)
                except Exception:
                    worker.update('writing', metadata_error=True)
                    logging.getLogger('inbox').warning('Sync error metadata could not be stored')
                delay=5 if quick_retry_available else min(max(delay,30)*2,300)
                quick_retry_available=False
                worker.update('backoff')
            worker.stop.wait(delay)
    finally:
        worker.update('stopped')


def display_content(m):
    return str(m.get('display_text') or m.get('preferred_content') or '')


def message(m):
    sender=m.get('sender') or {}
    if not isinstance(sender,dict): sender={}
    return {'id':str(m.get('id') or ''),'fromAgent':bool(m.get('from_agent')),
            'from':'agent' if m.get('from_agent') else 'customer',
            'fromName':sender.get('name') or '', 'fromEmail':sender.get('email') or '',
            'body':display_content(m), 'at':m.get('created_datetime'),
            **{key:m[key] for key in ('display_text','current_text','display_source','current_source','original_content','original_field','history_available','source_truncated','cleanup_version') if key in m},
            'attachments':[{key:a[key] for key in ('name','url','content_type','size') if key in a} for a in (m.get('attachments') or [])[:20] if isinstance(a,dict)],
            'internal':m.get('channel')=='internal-note' or m.get('public') is False,
            'contentUnavailable':bool(m.get('content_unavailable'))}

def messages_page(client, number, cursor=None):
    raw=client.call('get_ticket_messages',{'ticket_id':number,'limit':50,**({'cursor':cursor} if cursor else {})})
    if not isinstance(raw.get('data'),list): raise Unavailable()
    return {'messages':sorted([message(m) for m in raw['data']],key=lambda m:(epoch(m['at']),m['id'])),
            'nextCursor':(raw.get('meta') or {}).get('next_cursor')}

def enrich(ticket):
    try:
        old=projection_query('helpdesk.get_ticket',{'ticketId':ticket['id']}).get('ticket',{})
    except ProjectionUnavailable: old={}
    if (old.get('fromEmail') or '').casefold() != (ticket.get('fromEmail') or '').casefold(): old={}
    for key in ('readonlyDraft','draftReason','draftSuperseded','draftSourceMessageId','draftSourceMessageAt','draftProcessedAt','priority','draftAction',
                'draftGenerationState','draftGenerationError','draftAttemptCount','draftNextRetryAt','draftMissingFacts',
                'draftReviewRequired','draftStaffNextStep','draftRevision'):
        if key in old: ticket[key]=old[key]
    latest=max((epoch(m['at']) for m in ticket['messages'] if not m['internal']),default=0)
    source=epoch(ticket.get('draftSourceMessageAt'))
    if (ticket.get('readonlyDraft') or ticket.get('draftGenerationState')) and (not source or latest>source): ticket['draftSuperseded']=True
    attach_shop_rail(ticket)
    attach_customer_details(ticket)
    return ticket

def attach_customer_details(ticket):
    try:
        customer_details.attach(ticket, customer_details.database, path=customer_details.SNAPSHOT)
    except (sqlite3.Error, OSError):
        if not ticket.get('shopifyRail'):
            ticket['shopifyRail']={'status':'error','refreshError':True}
    rail=ticket.get('shopifyRail') or {}
    context=ticket.get('customerContext') or {}
    email=str(rail.get('email') or '').strip().casefold()
    verified=(rail.get('status')=='found' and not rail.get('stale') and not rail.get('refreshError')
              and not context.get('conflict') and email==str(ticket.get('fromEmail') or '').strip().casefold())
    order=rail.get('order') or {}
    orders=[order.get('name')] if order.get('name') else []
    if verified:
        orders += [o.get('name') for o in (rail.get('history') or []) if isinstance(o,dict) and o.get('name')]
    try:
        redo_details.attach(ticket,email if verified else '',list(dict.fromkeys(orders))[:3] if verified else [],
                            redo_details.database,redo_details.SNAPSHOT)
    except (sqlite3.Error,OSError):
        ticket['redoDetails']={'source':'redo','status':'unavailable','reason':'Redo lookup storage is temporarily unavailable.'}
    return ticket

def newer_summary(db,ticket):
    """Stored summary row, and whether it carries newer activity (or same activity, newer metadata) than ticket."""
    row=db.execute('SELECT generation,payload FROM tickets WHERE id=?',(ticket['id'],)).fetchone()
    stored=json.loads(row[1]) if row else {}
    if not isinstance(stored,dict): raise ValueError('Invalid cached ticket summary')
    mine,theirs=epoch(ticket.get('lastMessageAt')),epoch(stored.get('lastMessageAt'))
    return row,theirs>mine,theirs>mine or (theirs==mine and epoch(stored.get('updatedAt'))>epoch(ticket.get('updatedAt')))

def mark_stale(ticket):
    # Keep messages readable, but block Send/rewrite/retry and Use draft in the existing UI.
    return {**ticket,'syncStale':True,'draftSuperseded':True}

def get_ticket(number):
    key=str(number)
    with DETAIL_LOCK: cached=DETAIL_CACHE.get(key)
    if cached:
        try:
            with closing(database()) as db: stale=newer_summary(db,cached[1])[2]
        except (sqlite3.Error,ValueError,TypeError):
            # A failed freshness check may serve readable cached data, never an actionable fresh draft.
            return attach_customer_details(mark_stale(cached[1]))
        if stale: cached=(0,cached[1])  # a newer synced summary forces a re-fetch past the 15 s cache
    if cached and time.time()-cached[0]<15: return attach_customer_details(cached[1])
    try:
        with MCP() as client:
            raw=client.call('get_ticket',{'ticket_id':number})
            if str(raw.get('id'))!=key: raise Gone()
            page=messages_page(client,number)
        ticket=summary(raw);ticket.update(messages=page['messages'],messagesNextCursor=page['nextCursor'],historyIncomplete=bool(page['nextCursor']),observedMessageCount=len(page['messages']),syncedAt=now(),syncStale=False)
        enrich(ticket)
        # Promote a message preview only when the page reaches the provider's latest activity (any message, notes included).
        activity=source_epoch(raw.get('last_message_datetime'))
        public_chronology_known=all(source_epoch(m['at'])>0 for m in ticket['messages'] if not m['internal'])
        covered=activity>0 and public_chronology_known and max((source_epoch(m['at']) for m in ticket['messages']),default=0)>=activity
        latest=next((m for m in reversed(ticket['messages']) if not m['internal'] and m.get('body')),None) if covered else None
        if latest:
            ticket.update(snippet=latest['body'][:300],previewMessageId=latest['id'],previewProvenance={'source':'message','truncated':len(latest['body'])>300,'cleanupVersion':latest.get('cleanup_version')})
        with closing(database()) as db, db:
            db.execute('BEGIN IMMEDIATE')  # recheck the row and publish atomically against a concurrent sync
            row,stale,older=newer_summary(db,ticket)
            if not older and covered:
                saved=summary(raw)
                for field in ('snippet','previewMessageId','previewProvenance'):
                    if field in ticket:saved[field]=ticket[field]
                cache_summary(db,saved,row[0] if row else (get_meta(db).get('pendingFull') or {}).get('generation',get_meta(db).get('generation','')),preserve_preview=not latest)
        # Known activity the page does not reach, or a newer synced summary, makes this detail explicitly stale.
        if older or not public_chronology_known or (activity>0 and not covered): return mark_stale(ticket)
        with DETAIL_LOCK:
            if len(DETAIL_CACHE)>=128: DETAIL_CACHE.pop(next(iter(DETAIL_CACHE)))
            DETAIL_CACHE[key]=(time.time(),ticket)
        return ticket
    except Gone:
        with DETAIL_LOCK: DETAIL_CACHE.pop(key,None)
        with closing(database()) as db, db: db.execute('DELETE FROM tickets WHERE id=?',('gorgias:'+key,))
        raise
    except Exception:
        if cached: return attach_customer_details(mark_stale(cached[1]))
        raise Unavailable()

def list_tickets(args):
    clause=[];params=[]
    view=args['view']
    trash="coalesce(json_extract(payload,'$.trashed'),0)"
    spam="coalesce(json_extract(payload,'$.spam'),0)"
    snooze="coalesce(json_extract(payload,'$.snoozedUntil'),'')"
    if view=='trash': clause.append(trash+'=1')
    else:
        clause.append(trash+'=0')
        if view=='spam': clause.append(spam+'=1')
        else: clause.append(spam+'=0')
    if view in {'open','closed'}:
        clause.append("json_extract(payload,'$.status')=?");params.append(view)
    elif view=='assigned':
        clause.append("fold(coalesce(json_extract(payload,'$.assigneeEmail'),''))=? AND ?<>''")
        params.extend([OPERATOR_EMAIL,OPERATOR_EMAIL])
    elif view=='unassigned':
        clause.append("coalesce(json_extract(payload,'$.assigneeEmail'),'')='' AND coalesce(json_extract(payload,'$.assigneeTeam'),'')='' AND coalesce(json_extract(payload,'$.assignee'),'unassigned') IN ('','unassigned')")
    elif view=='snoozed':
        clause.append("epoch("+snooze+")>?");params.append(time.time())
    for key,path in [('priority','gorgiasPriority'),('assignee','assigneeEmail'),('channel','channel')]:
        value=args.get(key,'').strip()
        if value:
            clause.append("fold(coalesce(json_extract(payload,'$."+path+"'),''))=?");params.append(value.casefold())
    if args.get('tag','').strip():
        clause.append("EXISTS (SELECT 1 FROM json_each(tickets.payload,'$.tags') WHERE fold(value)=?)")
        params.append(args['tag'].strip().casefold())
    needle=' '.join(args['query'].split()).casefold()
    if needle:
        clause.append("instr(fold(id || ' ' || coalesce(json_extract(payload,'$.subject'),'') || ' ' || coalesce(json_extract(payload,'$.customerName'),'') || ' ' || coalesce(json_extract(payload,'$.fromEmail'),'') || ' ' || coalesce(json_extract(payload,'$.snippet'),'')),?)>0")
        params.append(needle)
    where=' WHERE '+' AND '.join(clause) if clause else ''
    direction='ASC' if args['oldest'] else 'DESC'
    with closing(database()) as db:
        db.create_function('fold',1,lambda value:str(value).casefold(),deterministic=True)
        db.create_function('epoch',1,epoch,deterministic=True)
        meta=get_meta(db)
        total=db.execute('SELECT count(*) FROM tickets'+where,params).fetchone()[0]
        rows=db.execute('SELECT payload FROM tickets'+where+' ORDER BY updated '+direction+',id LIMIT ? OFFSET ?',
                        (*params,args['limit'],args['offset'])).fetchall()
        availability={key:bool(db.execute("SELECT EXISTS(SELECT 1 FROM tickets WHERE json_extract(payload,'$.categoryAvailability."+key+"')=1)").fetchone()[0]) for key in ('spam','trash','snoozed')}
    stamp=meta.get('generatedAt') or meta.get('lastPageAt')
    return {'ok':True,'source':'gorgias_api','tickets':[json.loads(row[0]) for row in rows],'total':total,
            'operatorEmail':OPERATOR_EMAIL,'categoryAvailability':{**availability,'assigned':bool(OPERATOR_EMAIL)},
            'nextOffset':args['offset']+args['limit'] if args['offset']+args['limit']<total else None,
            'projection':{'generatedAt':stamp,'stale':bool(meta.get('error')) or (bool(stamp) and time.time()-epoch(stamp)>120),
                          'syncing':bool(meta.get('syncing')),'complete':bool(meta.get('complete')),'ticketCount':total}}

class Arguments(BaseModel):
    model_config=ConfigDict(extra='forbid')
class ListArguments(Arguments):
    view: StrictStr=Field(default='all',pattern=r'^(all|open|closed|assigned|unassigned|snoozed|trash|spam)$')
    priority: StrictStr=Field(default='',pattern=r'^(|critical|high|normal|low)$')
    assignee: StrictStr=Field(default='',max_length=254)
    tag: StrictStr=Field(default='',max_length=200)
    channel: StrictStr=Field(default='',max_length=80)
    query: StrictStr=Field(default='',max_length=500)
    oldest: StrictBool=False
    offset: StrictInt=Field(default=0,ge=0)
    limit: StrictInt=Field(default=100,ge=1,le=100)
class TicketArguments(Arguments):
    ticketId: StrictStr=Field(pattern=r'^gorgias:[1-9][0-9]{0,17}$')
class MessageArguments(TicketArguments):
    cursor: StrictStr=Field(min_length=1,max_length=2048)
class Invocation(Arguments):
    tool: StrictStr=Field(max_length=80)
    arguments: dict=Field(default_factory=dict)
SCHEMAS={'helpdesk.list_tickets':ListArguments,'helpdesk.get_ticket':TicketArguments,
         'helpdesk.get_messages':MessageArguments,'helpdesk.capabilities':Arguments}

def start_worker():
    global WORKER
    with WORKER_LOCK:
        if WORKER and WORKER.thread and WORKER.thread.is_alive():
            raise RuntimeError('Previous Inbox sync worker is still running')
        init_db()
        worker=Worker()
        worker.thread=threading.Thread(target=sync_loop,args=(worker,),daemon=True,name='inbox-sync')
        WORKER=worker
        worker.thread.start()
        return worker


def stop_worker(worker):
    worker.stop.set()
    worker.update('stopping')
    worker.thread.join(SHUTDOWN_TIMEOUT)
    if worker.thread.is_alive():
        worker.update('stopping', shutdown_timeout=True)
        logging.getLogger('inbox').warning('Inbox sync worker did not stop before shutdown timeout')


@asynccontextmanager
async def lifespan(app):
    worker=start_worker()
    try:
        yield
    finally:
        await run_in_threadpool(stop_worker,worker)

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)

@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response

@app.get('/health')
def health(): return {'ok':True,'readOnly':True}

def read_snapshot(path, *, canonical=False):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=READ_TIMEOUT)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if canonical:
            db.execute('SELECT id,observed_at,summary,detail FROM tickets LIMIT 1').fetchone()
            row=db.execute('SELECT substr(CAST(payload AS BLOB),1,65537) FROM metadata WHERE id=1').fetchone()
        else:
            db.execute('SELECT id,updated,payload,generation FROM tickets LIMIT 1').fetchone()
            row=db.execute('SELECT substr(CAST(payload AS BLOB),1,65537) FROM meta WHERE id=1').fetchone()
        if not row or not isinstance(row[0],bytes) or len(row[0])>65536:
            raise ValueError('Missing or oversized snapshot metadata')
        meta=json.loads(row[0])
        if not isinstance(meta,dict): raise ValueError('Invalid snapshot metadata')
        return meta


def freshness(value, wall, limit, *, numeric=False):
    try:
        if numeric:
            if isinstance(value,bool) or not isinstance(value,(int,float)): return 'invalid',None
            stamp=float(value)
        else:
            parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if parsed.tzinfo is None: return 'invalid',None
            stamp=parsed.timestamp()
        age=wall-stamp
        if not math.isfinite(stamp) or age<0: return 'invalid',None
        return ('fresh' if age<=limit else 'stale'),datetime.fromtimestamp(stamp,timezone.utc).isoformat()
    except (ValueError,TypeError,OverflowError,OSError):
        return 'invalid',None


def assess_readiness():
    checks={'storage':'unavailable','worker':'unavailable','ticketData':'unavailable','projection':'unavailable'}
    diagnostics={}
    unusable=False
    with WORKER_LOCK: worker=WORKER
    state=worker.snapshot() if worker else None
    if state:
        diagnostics.update(workerId=state['workerId'],workerPhase=state['phase'],phaseElapsedSeconds=round(max(0,state['elapsed']),1),
                           progressElapsedSeconds=round(max(0,state['progressElapsed']),1),
                           lastProgressAt=state['progressAt'],lastWorkerSuccessAt=state['successAt'],
                           errorMetadataUnavailable=state['metadataError'],shutdownTimedOut=state['shutdownTimeout'])
        if state['alive'] and not state['shutdownTimeout']:
            limit=PHASE_LIMITS.get(state['phase'],0)
            no_progress=state['phase'] in {'starting','scanning','capacity','transport','writing'} and state['progressElapsed']>120
            checks['worker']='stuck' if state['elapsed']>limit or no_progress else 'error' if state['error'] else 'ok'
    try:
        meta=read_snapshot(DB)
        checks['storage']='ok'
        wall=time.time()
        completed,stamp=freshness(meta.get('lastCompletedAt'),wall,120)
        diagnostics['lastCompletedAt']=stamp
        pending=meta.get('pendingFull')
        if meta.get('complete') is not True or completed=='invalid':
            checks['ticketData']='incomplete' if meta.get('complete') is not True else 'invalid'
            unusable=True
        elif pending:
            page,page_stamp=freshness(meta.get('lastPageAt'),wall,120)
            head,head_stamp=freshness(meta.get('lastHeadAt'),wall,120)
            diagnostics.update(pendingFull=True,lastPageAt=page_stamp,lastHeadAt=head_stamp)
            checks['ticketData']='fresh' if page==head=='fresh' else 'stale'
        else:
            diagnostics['pendingFull']=False
            checks['ticketData']=completed
        if meta.get('error') and checks['worker']=='ok': checks['worker']='error'
    except (sqlite3.Error,ValueError,TypeError,OSError):
        unusable=True
    path=os.environ.get('INBOX_PROJECTION_PATH',projection.DEFAULT_PATH)
    try:
        meta=read_snapshot(path,canonical=True)
        if meta.get('version')!=projection.VERSION: raise ValueError('Invalid projection version')
        fresh,stamp=freshness(meta.get('generatedAtEpoch'),time.time(),180,numeric=True)
        diagnostics['projectionGeneratedAt']=stamp
        if fresh=='invalid':
            checks['projection']='invalid';unusable=True
        else:
            checks['projection']='stale' if Path(path).with_suffix('.error').exists() else fresh
    except (sqlite3.Error,ValueError,TypeError,OSError):
        unusable=True
    ready=checks=={'storage':'ok','worker':'ok','ticketData':'fresh','projection':'fresh'}
    return {'status':'unusable' if unusable else 'ready' if ready else 'degraded',
            'readOnly':True,'checks':checks,'diagnostics':diagnostics}


@app.get('/ready')
def readiness():
    result=assess_readiness()
    return JSONResponse(result,status_code=200 if result['status']=='ready' else 503)


@app.post('/inbox/api/helpdesk')
async def invoke(request:Request):
    if request.headers.get('content-type','').split(';')[0].strip()!='application/json':
        return JSONResponse({'ok':False,'message':'Use application/json.'},status_code=415)
    try:
        raw=bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw)>8192: return JSONResponse({'ok':False,'message':'Request too large.'},status_code=413)
        invocation=Invocation.model_validate_json(bytes(raw));schema=SCHEMAS.get(invocation.tool)
        if not schema: return JSONResponse({'ok':False,'message':'This inbox only supports reading Gorgias data.'},status_code=403)
        args=schema.model_validate(invocation.arguments).model_dump()
        if invocation.tool=='helpdesk.capabilities': return {'ok':True,'source':'gorgias_api','readOnly':True,'capabilities':{'listTickets':True,'getTicket':True,'sendReply':False,'createTicket':False}}
        if invocation.tool=='helpdesk.list_tickets': return await run_in_threadpool(list_tickets,args)
        number=int(args['ticketId'].split(':')[1])
        if invocation.tool=='helpdesk.get_ticket': return {'ok':True,'source':'gorgias_api','ticket':await run_in_threadpool(get_ticket,number)}
        def older():
            with MCP() as client: return messages_page(client,number,args['cursor'])
        return {'ok':True,'source':'gorgias_api',**await run_in_threadpool(older)}
    except (ValidationError,ValueError): return JSONResponse({'ok':False,'message':'Invalid read request.'},status_code=400)
    except Gone: return JSONResponse({'ok':False,'message':'This ticket is no longer available in Gorgias.'},status_code=404)
    except Exception: return JSONResponse({'ok':False,'message':'Gorgias is temporarily unavailable. Please retry.'},status_code=503)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8767,workers=1,limit_concurrency=24,access_log=False,server_header=False)
