#!/usr/bin/env python3
"""Operator-only local snapshot export. No environment, provider or network access."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
import grp
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from projection import connect, VERSION, DEFAULT_PATH


def text(value):
    value=value if isinstance(value,str) else ''
    return value[:20000],len(value)>20000


def identity_context(row, raw):
    """Only explicit customer fields from the same canonical event; no inference."""
    def field(value, limit):
        return value.strip() if isinstance(value,str) and 0 < len(value.strip()) <= limit else None
    email = field(row.get('customer_email'), 320)
    result = {'source':'canonical_webhook','observedAt':row.get('received_at'),
              'identity':{'name':None,'email':email,'phone':None,'id':None},
              'status':'observed' if email else 'unknown','conflict':False}
    if not isinstance(raw,str) or len(raw.encode('utf-8')) > 65536:
        return result
    try:
        payload=json.loads(raw)
        if not isinstance(payload,dict): return result
        data=payload.get('data')
        ticket=payload.get('ticket',data.get('ticket',{}) if isinstance(data,dict) else {})
        if not isinstance(ticket,dict): return result
        if ticket.get('id') is None: return result
        if str(ticket.get('id')) != str(row['ticket_id']):
            result.update(status='conflict',conflict=True); return result
        customer=ticket.get('customer')
        if not isinstance(customer,dict): return result
        observed_email=field(customer.get('email'),320)
        if email and observed_email and email.casefold()!=observed_email.casefold():
            result.update(status='conflict',conflict=True); return result
        customer_id=customer.get('id')
        if isinstance(customer_id,bool) or not isinstance(customer_id,(str,int)):
            customer_id=None
        else:
            customer_id=str(customer_id)
            if not customer_id.isascii() or not customer_id.isdecimal() or len(customer_id)>20:
                customer_id=None
        result['identity'].update(name=field(customer.get('name'),200),
                                  email=email or observed_email,
                                  phone=field(customer.get('phone'),80),id=customer_id)
        result['status']='observed' if any(result['identity'].values()) else 'unknown'
    except (ValueError,TypeError,RecursionError): pass
    return result


def extract(source, now):
    cutoff=(datetime.fromtimestamp(now,timezone.utc)-timedelta(days=90)).isoformat()
    with closing(connect(source)) as db:
        deadline=time.monotonic()+5
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
        db.execute('BEGIN')
        cursor=db.execute('''WITH ranked AS (
            SELECT *,ROW_NUMBER() OVER(PARTITION BY ticket_id ORDER BY received_at DESC,message_id DESC) n,
            COUNT(*) OVER(PARTITION BY ticket_id) observed_count
            FROM parsed_messages WHERE received_at>=?)
            SELECT p.ticket_id,p.message_id,p.author_type,p.author_email,p.customer_email,p.ticket_subject,
            p.channel,p.ticket_status,p.created_at,p.received_at,p.is_customer_message,p.observed_count,
            substr(p.message_text,1,20001) message_text, substr(r.draft_text,1,20001) draft_text,
            r.priority,r.action,substr(r.reason,1,20001) reason,r.processed_at
            FROM ranked p LEFT JOIN ticket_results r ON r.ticket_id=p.ticket_id AND r.message_id=p.message_id
            WHERE p.n<=100 ORDER BY p.ticket_id,p.received_at,p.message_id''',(cutoff,))
        rows=[];size=0
        for row in cursor:
            record=dict(row);size+=sum(len(v.encode('utf-8')) for v in record.values() if isinstance(v,str))
            if size>32_000_000:raise ValueError('Projection exceeds bounded snapshot size')
            rows.append(record)
        # Only one bounded identity source per ticket, never whole ticket history.
        # Optional identity enrichment cannot consume unlimited raw event payloads.
        seen=set();identity_bytes=0
        for record in reversed(rows):
            if record['ticket_id'] in seen: continue
            seen.add(record['ticket_id'])
            raw=None
            if identity_bytes < 8_000_000:
                event=db.execute('SELECT substr(raw_payload,1,65537) FROM webhook_events WHERE ticket_id=? AND message_id=?',
                                 (record['ticket_id'],record['message_id'])).fetchone()
                if event:
                    raw=event[0];identity_bytes+=len(raw.encode('utf-8')) if isinstance(raw,str) else 0
            record['customer_context']=identity_context(record,raw)
        return rows,False


def build(rows):
    grouped={}
    for row in rows:grouped.setdefault(row['ticket_id'],[]).append(row)
    tickets=[]
    for ticket_id,items in grouped.items():
        latest=items[-1];messages=[];truncated=latest['observed_count']>100
        for r in items:
            body,cut=text(r['message_text']);truncated|=cut
            agent=not bool(r['is_customer_message'])
            messages.append({'id':r['message_id'],'from':'agent' if agent else 'customer','fromAgent':agent,
              'fromName':r['author_email'] or ('Observed agent' if agent else 'Customer'),'fromEmail':r['author_email'] or '',
              'body':body,'at':r['created_at'] or r['received_at'],'truncated':cut,'via':'gorgias'})
        draft_rows=[r for r in items if r['draft_text']]
        draft=max(draft_rows,key=lambda r:r['processed_at'] or '') if draft_rows else None
        latest_customer=next((r for r in reversed(items) if r['is_customer_message']),None)
        superseded=bool(draft and (not latest_customer or draft['message_id']!=latest_customer['message_id']))
        draft_text,cut=text(draft['draft_text'] if draft and not superseded else '');truncated|=cut
        reason,reason_cut=text(draft['reason'] if draft else '');truncated|=reason_cut
        subject,subject_cut=text(latest['ticket_subject']);truncated|=subject_cut
        raw_channel = latest.get('channel') if isinstance(latest.get('channel'), str) else ''
        channel = raw_channel.strip()[:40]
        observed_status = latest.get('ticket_status') if isinstance(latest.get('ticket_status'), str) else ''
        observed_status = observed_status.strip()[:30] if observed_status else ''
        ticket={'id':f'gorgias:{ticket_id}','subject':subject,'customerName':latest['customer_email'] or 'Customer',
          'customerContext':latest.get('customer_context',identity_context(latest,None)),
          'fromEmail':latest['customer_email'] or '', 'status':observed_status or 'unknown','assignee':None,'channel':channel,'updatedAt':latest['received_at'],
          'snippet':messages[-1]['body'][:240], 'messages':messages,'statusEvents':[], 'projectionSource':True,
          'historyIncomplete':True,'truncated':bool(truncated),'observedMessageCount':latest['observed_count'],
          'readonlyDraft':draft_text,'draftReason':reason,'draftSuperseded':superseded,
          'draftSourceMessageId':draft['message_id'] if draft else None,'draftSourceMessageAt':(draft['created_at'] or draft['received_at']) if draft else None,'draftProcessedAt':draft['processed_at'] if draft else None,
          'priority':draft['priority'] if draft else None,'draftAction':draft['action'] if draft else None}
        if ticket['customerContext']['identity']['name'] and not ticket['customerContext']['conflict']:
            ticket['customerName']=ticket['customerContext']['identity']['name']
        tickets.append(ticket)
    return tickets


def export(source, destination, *, now=None, group=None):
    now=time.time() if now is None else now
    destination=Path(destination);directory=destination.parent
    if not directory.is_dir() or directory.is_symlink():raise ValueError('Projection directory must exist')
    temporary=None
    try:
        rows,truncated=extract(source,now);tickets=build(rows)
        fd,name=tempfile.mkstemp(prefix='.projection-',suffix='.sqlite3',dir=directory);os.close(fd);temporary=Path(name)
        with closing(sqlite3.connect(temporary)) as db:
            db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE metadata(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE tickets(id TEXT PRIMARY KEY,observed_at TEXT NOT NULL,summary TEXT NOT NULL,detail TEXT NOT NULL)')
            db.execute('CREATE INDEX ticket_order ON tickets(observed_at DESC,id)')
            metadata={'version':VERSION,'generatedAtEpoch':now,'generatedAt':datetime.fromtimestamp(now,timezone.utc).isoformat(),
                      'ticketCount':len(tickets),'windowDays':90,'ticketLimit':None,'messageLimit':100,'truncated':truncated,'historyIncomplete':True,
                      'sourceWatermark':max((t['updatedAt'] for t in tickets),default=None)}
            db.execute('INSERT INTO metadata VALUES(1,?)',(json.dumps(metadata),))
            for ticket in tickets:
                summary={k:v for k,v in ticket.items() if k not in ('messages','readonlyDraft')}
                db.execute('INSERT INTO tickets VALUES(?,?,?,?)',(ticket['id'],ticket['updatedAt'],json.dumps(summary),json.dumps(ticket)))
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid projection')
        os.chmod(temporary,0o640)
        if group is not None:os.chown(temporary,0,grp.getgrnam(group).gr_gid)
        with temporary.open('rb') as handle:os.fsync(handle.fileno())
        os.replace(temporary,destination);temporary=None
        destination.with_suffix('.error').unlink(missing_ok=True)
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        return metadata
    except Exception:
        # No exception text/customer fields in the error marker.
        marker=destination.with_suffix('.error');marker.touch(mode=0o640,exist_ok=True)
        if group is not None:os.chown(marker,0,grp.getgrnam(group).gr_gid)
        raise
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--destination',default=DEFAULT_PATH)
    parser.add_argument('--group',default='bb-inbox');args=parser.parse_args()
    try:
        result=export(args.source,args.destination,group=args.group)
        print(json.dumps({'ok':True,'ticketCount':result['ticketCount'],'truncated':result['truncated']}))
    except Exception:
        print('{"ok":false,"error":"projection_export_failed"}')
        raise SystemExit(1)
