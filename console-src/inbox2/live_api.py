"""Inbox: credential-free Gorgias reads through the local GET-only MCP.

The public API is authenticated by Caddy. Only three named read operations are
accepted. The process has loopback-only network access and read-only source
snapshots. Local SQLite stores synchronization state, never provider mutations.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
import json
import logging
import re
from pathlib import Path
import sqlite3
import sys
import threading
import time
import urllib.request
from urllib.error import HTTPError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool

INBOX_MODULES = Path(__file__).resolve().parent.parent / 'inbox'
if not (INBOX_MODULES / 'projection.py').is_file():
    INBOX_MODULES = Path('/opt/buttonsbebe/inbox/console-src/inbox')
sys.path.insert(0, str(INBOX_MODULES))
from projection import query as projection_query, ProjectionUnavailable
from shop_rail import attach as attach_shop_rail
import customer_details

DB = Path('/var/lib/buttonsbebe-inbox2/live.sqlite3')
MCP_URL = 'http://127.0.0.1:8079/mcp'
STOP = threading.Event()
CAPACITY = threading.BoundedSemaphore(2)
DETAIL_LOCK = threading.Lock()
DETAIL_CACHE = {}

class Unavailable(Exception): pass
class Gone(Exception): pass

def now(): return datetime.now(timezone.utc).isoformat()
def epoch(value):
    try: return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError): return 0

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
        db.execute('CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT)')
        db.execute("INSERT OR IGNORE INTO meta VALUES(1, '{}')")
    with closing(customer_details.database()) as db, db:
        customer_details.initialize(db)

def get_meta(db): return json.loads(db.execute('SELECT payload FROM meta WHERE id=1').fetchone()[0])
def set_meta(db, meta): db.execute('UPDATE meta SET payload=? WHERE id=1', (json.dumps(meta),))

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
            self.rpc('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'inbox-readonly','version':'1'}})
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
            'snippet':t.get('excerpt') or '', 'tags':[x.get('name','') if isinstance(x,dict) else str(x) for x in t.get('tags') or []],
            'spam':bool(t.get('spam')), 'trashed':bool(t.get('trashed_datetime')), 'source':'gorgias_api',
            'customerContext':{'source':'gorgias_api','status':'observed','conflict':False,
                               'identity':{'email':customer.get('email') or '', 'name':customer.get('name') or ''},'observedAt':now()}}

def sync_once():
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
        meta.update(syncing=True,error=False);set_meta(db,meta)
    while not STOP.is_set():
        with MCP() as client: result=client.call('list_inbox_tickets',{'limit':100,**({'cursor':cursor} if cursor else {})})
        rows=result.get('data')
        if not isinstance(rows,list): raise Unavailable()
        next_cursor=(result.get('meta') or {}).get('next_cursor')
        with closing(database()) as db, db:
            for raw in rows:
                ticket=summary(raw);updated=epoch(ticket['updatedAt']);newest=max(newest,updated)
                if ticket['trashed']: db.execute('DELETE FROM tickets WHERE id=?',(ticket['id'],))
                else:
                    db.execute('INSERT INTO tickets VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,payload=excluded.payload,generation=excluded.generation',
                               (ticket['id'],updated,json.dumps(ticket),generation))
            meta=get_meta(db);meta.update(lastPageAt=now(),syncing=True)
            if full:meta['pendingFull']={'cursor':next_cursor,'generation':generation,'watermark':newest}
            set_meta(db,meta)
        reached_old=not full and rows and all(epoch(x.get('updated_datetime') or x.get('created_datetime'))<watermark-120 for x in rows)
        if not next_cursor or reached_old:
            with closing(database()) as db, db:
                if full: db.execute('DELETE FROM tickets WHERE generation<>?',(generation,))
                meta=get_meta(db);meta.update(complete=True,syncing=False,error=False,generatedAt=now(),watermark=newest,generation=generation)
                if full:
                    meta['fullAt']=time.time();meta.pop('pendingFull',None)
                set_meta(db,meta)
            return
        if next_cursor in seen: raise Unavailable()
        seen.add(next_cursor);cursor=next_cursor
        # Keep the newest page fresh during a long historical backfill.
        if full and time.monotonic()-last_head>=30:
            with MCP() as client: head=client.call('list_inbox_tickets',{'limit':100})
            if not isinstance(head.get('data'),list): raise Unavailable()
            with closing(database()) as db, db:
                for raw in head['data']:
                    ticket=summary(raw)
                    if ticket['trashed']:db.execute('DELETE FROM tickets WHERE id=?',(ticket['id'],))
                    else:db.execute('INSERT INTO tickets VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,payload=excluded.payload,generation=excluded.generation',(ticket['id'],epoch(ticket['updatedAt']),json.dumps(ticket),generation))
                meta=get_meta(db);meta['generatedAt']=now();set_meta(db,meta)
            last_head=time.monotonic()
        if STOP.wait(.6): return

def sync_loop():
    delay=30
    while not STOP.is_set():
        try: sync_once();delay=30
        except Exception:
            logging.getLogger('inbox').warning('Gorgias read sync unavailable; retaining last successful data')
            with closing(database()) as db, db:
                meta=get_meta(db);meta.update(error=True,syncing=False);set_meta(db,meta)
            delay=min(delay*2,300)
        STOP.wait(delay)

def display_content(m):
    text=m.get('preferred_content') or ''
    if m.get('preferred_content_field')!='stripped_text' or not text:return text
    lines=text.replace('\r\n','\n').replace('\r','\n').split('\n')
    result=[];previous=''
    for line in lines:
        source_line=previous.rstrip()
        continuation=line.lstrip()
        soft_wrap=(result and 60<=len(source_line)<=90 and continuation
                   and continuation[0].islower() and source_line[-1] not in '.!?;:'
                   and not line.startswith((' ','\t'))
                   and not re.search(r'(?:https?://|www\.)\S+$',source_line))
        if soft_wrap:result[-1]=result[-1].rstrip()+' '+continuation
        else:result.append(line)
        previous=line
    text='\n'.join(result)
    text=re.sub(r'[ \t]{2,}',' ',text)
    return re.sub(r'[ \t]+(?=[.,!?;:])','',text)

def message(m):
    sender=m.get('sender') or {}
    if not isinstance(sender,dict): sender={}
    return {'id':str(m.get('id') or ''),'fromAgent':bool(m.get('from_agent')),
            'from':'agent' if m.get('from_agent') else 'customer',
            'fromName':sender.get('name') or '', 'fromEmail':sender.get('email') or '',
            'body':display_content(m), 'at':m.get('created_datetime'),
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
    for key in ('readonlyDraft','draftReason','draftSuperseded','draftSourceMessageId','draftSourceMessageAt','draftProcessedAt','priority','draftAction'):
        if key in old: ticket[key]=old[key]
    latest=max((epoch(m['at']) for m in ticket['messages'] if not m['internal']),default=0)
    source=epoch(ticket.get('draftSourceMessageAt'))
    if ticket.get('readonlyDraft') and (not source or latest>source): ticket['draftSuperseded']=True
    attach_shop_rail(ticket)
    attach_customer_details(ticket)
    return ticket

def attach_customer_details(ticket):
    try:
        customer_details.attach(ticket, customer_details.database)
    except (sqlite3.Error, OSError):
        if not ticket.get('shopifyRail'):
            ticket['shopifyRail']={'status':'error','refreshError':True}
    return ticket

def get_ticket(number):
    key=str(number)
    with DETAIL_LOCK: cached=DETAIL_CACHE.get(key)
    if cached and time.time()-cached[0]<15: return attach_customer_details(cached[1])
    try:
        with MCP() as client:
            raw=client.call('get_ticket',{'ticket_id':number})
            if str(raw.get('id'))!=key or raw.get('trashed_datetime'): raise Gone()
            page=messages_page(client,number)
        ticket=summary(raw);ticket.update(messages=page['messages'],messagesNextCursor=page['nextCursor'],historyIncomplete=bool(page['nextCursor']),observedMessageCount=len(page['messages']),syncedAt=now(),syncStale=False)
        enrich(ticket)
        with DETAIL_LOCK:
            if len(DETAIL_CACHE)>=128: DETAIL_CACHE.pop(next(iter(DETAIL_CACHE)))
            DETAIL_CACHE[key]=(time.time(),ticket)
        with closing(database()) as db, db:
            row=db.execute('SELECT generation FROM tickets WHERE id=?',(ticket['id'],)).fetchone()
            db.execute('INSERT INTO tickets VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,payload=excluded.payload',
                       (ticket['id'],epoch(ticket['updatedAt']),json.dumps(summary(raw)),row[0] if row else (get_meta(db).get('pendingFull') or {}).get('generation',get_meta(db).get('generation',''))))
        return ticket
    except Gone:
        with DETAIL_LOCK: DETAIL_CACHE.pop(key,None)
        with closing(database()) as db, db: db.execute('DELETE FROM tickets WHERE id=?',('gorgias:'+key,))
        raise
    except Exception:
        if cached: return attach_customer_details({**cached[1],'syncStale':True})
        raise Unavailable()

def list_tickets(args):
    clause=[];params=[]
    if args['view']!='all':
        clause.append("json_extract(payload,'$.status')=?");params.append(args['view'])
    needle=' '.join(args['query'].split()).casefold()
    if needle:
        clause.append("instr(fold(id || ' ' || coalesce(json_extract(payload,'$.subject'),'') || ' ' || coalesce(json_extract(payload,'$.customerName'),'') || ' ' || coalesce(json_extract(payload,'$.fromEmail'),'') || ' ' || coalesce(json_extract(payload,'$.snippet'),'')),?)>0")
        params.append(needle)
    where=' WHERE '+' AND '.join(clause) if clause else ''
    direction='ASC' if args['oldest'] else 'DESC'
    with closing(database()) as db:
        db.create_function('fold',1,lambda value:str(value).casefold(),deterministic=True)
        meta=get_meta(db)
        total=db.execute('SELECT count(*) FROM tickets'+where,params).fetchone()[0]
        rows=db.execute('SELECT payload FROM tickets'+where+' ORDER BY updated '+direction+',id LIMIT ? OFFSET ?',
                        (*params,args['limit'],args['offset'])).fetchall()
    stamp=meta.get('generatedAt') or meta.get('lastPageAt')
    return {'ok':True,'source':'gorgias_api','tickets':[json.loads(row[0]) for row in rows],'total':total,
            'nextOffset':args['offset']+args['limit'] if args['offset']+args['limit']<total else None,
            'projection':{'generatedAt':stamp,'stale':bool(meta.get('error')) or (bool(stamp) and time.time()-epoch(stamp)>120),
                          'syncing':bool(meta.get('syncing')),'complete':bool(meta.get('complete')),'ticketCount':total}}

class Arguments(BaseModel):
    model_config=ConfigDict(extra='forbid')
class ListArguments(Arguments):
    view: StrictStr=Field(default='all',pattern=r'^(all|open|closed)$')
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

@asynccontextmanager
async def lifespan(app):
    init_db();worker=threading.Thread(target=sync_loop,daemon=True);worker.start()
    yield
    STOP.set()
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)

@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response

@app.get('/health')
def health(): return {'ok':True,'readOnly':True}

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
