#!/usr/bin/env python3
"""Operator-only local snapshot export. No environment, provider or network access."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
import grp
import json
import os
import re
from pathlib import Path
import sqlite3
import tempfile
import time
from projection import connect, VERSION, DEFAULT_PATH


def text(value):
    value=value if isinstance(value,str) else ''
    return value[:20000],len(value)>20000


# Gorgias HTTP Integration templates render every value as a string: booleans
# arrive as "True"/"False", absent values as "None", objects as JSON via
# `| tojson`. The webhook parser normalizes these at ingest into bounded
# ticket_* columns; the exporter reads only those columns. The readers below
# serve customer identity only.


def event_ticket(row, raw):
    """Ticket object from one bounded canonical event.

    Returns ``(ticket, mismatch)``: the ticket dict when the payload is usable
    and names this row's ticket, otherwise ``None`` plus whether the payload
    explicitly named a different ticket.
    """
    if not isinstance(raw,str) or len(raw.encode('utf-8')) > 65536: return None,False
    try: payload=json.loads(raw)
    except (ValueError,TypeError,RecursionError): return None,False
    if not isinstance(payload,dict): return None,False
    data=payload.get('data')
    ticket=payload.get('ticket',data.get('ticket',{}) if isinstance(data,dict) else {})
    if not isinstance(ticket,dict) or ticket.get('id') is None: return None,False
    if str(ticket.get('id')) != str(row['ticket_id']): return None,True
    return ticket,False


def rendered_decimal(value, limit):
    if isinstance(value,bool) or not isinstance(value,(str,int)): return None
    digits=str(value)
    return digits if digits.isascii() and digits.isdecimal() and len(digits)<=limit else None


# Mailbox/login addresses and shop display names never become personas. Keep in
# sync with the JS isMailboxName guard (clerk-ticket.js); this exporter-side
# copy keeps the projection honest even if the inbox bundle is stale.
_MAILBOX_NAMES={'demo shop support','demo shop','agentmail','teddyjubu','helpdesk-support'}


def derived_customer_name(email):
    """Gorgias-style display name from the address local part; None when the
    address is a mailbox/login identity or too odd to humanize. Issue #35.
    Gorgias masks addresses in webhook payloads (e***a@gmail.com) — the mask
    asterisks are observed data, so they stay in the derived name."""
    text=email.strip().lower() if isinstance(email,str) else ''
    if not text or text.count('@')!=1: return None
    local,domain=text.split('@')
    if domain=='agentmail.to' and local in _MAILBOX_NAMES: return None
    # Word characters are letters, digits and the mask asterisk; separator
    # runs become single spaces. The display is a plain name, never an address.
    words=re.split(r'[^a-z0-9*]+',local)
    words=[word.capitalize() for word in words if word]
    if not words: return None
    return ' '.join(words)[:200]


def identity_context(row, raw):
    """Only explicit customer fields from the same canonical event; no inference."""
    def field(value, limit):
        return value.strip() if isinstance(value,str) and 0 < len(value.strip()) <= limit else None
    email = field(row.get('customer_email'), 320)
    result = {'source':'canonical_webhook','observedAt':row.get('received_at'),
              'identity':{'name':None,'email':email,'phone':None,'id':None},
              'status':'observed' if email else 'unknown','conflict':False}
    ticket,mismatch=event_ticket(row,raw)
    if mismatch:
        result.update(status='conflict',conflict=True); return result
    if ticket is None: return result
    customer=ticket.get('customer')
    if not isinstance(customer,dict): return result
    observed_email=field(customer.get('email'),320)
    if email and observed_email and email.casefold()!=observed_email.casefold():
        result.update(status='conflict',conflict=True); return result
    result['identity'].update(name=field(customer.get('name'),200),
                              email=email or observed_email,
                              phone=field(customer.get('phone'),80),
                              id=rendered_decimal(customer.get('id'),20))
    result['status']='observed' if any(result['identity'].values()) else 'unknown'
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
            p.channel,p.ticket_status,p.ticket_assignee,p.ticket_tags,p.ticket_priority,
            p.ticket_spam,p.ticket_trashed,p.ticket_snoozed,p.created_at,p.received_at,p.is_customer_message,p.observed_count,
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
        observed_assignee = latest.get('ticket_assignee') if isinstance(latest.get('ticket_assignee'), str) else ''
        observed_assignee = observed_assignee.strip()[:120] if observed_assignee else ''
        raw_tags=latest.get('ticket_tags')
        try:
            parsed_tags=json.loads(raw_tags) if isinstance(raw_tags,str) and raw_tags.strip() else []
        except (ValueError,TypeError):
            parsed_tags=[]
        observed_tags=[tag for tag in parsed_tags if isinstance(tag,str) and tag.strip()][:12]
        raw_priority = latest.get('ticket_priority') if isinstance(latest.get('ticket_priority'), str) else ''
        raw_priority = raw_priority.strip().lower()[:20] if raw_priority else ''
        gorgias_priority = raw_priority if raw_priority and all(ch.isalnum() or ch in ("_", "-") for ch in raw_priority) else ''
        gorgias_spam = latest.get('ticket_spam') == 1
        gorgias_trashed = latest.get('ticket_trashed') == 1
        gorgias_snoozed = latest.get('ticket_snoozed') == 1
        # Views read these as observed flags (trash/spam buckets, snooze view),
        # so the same parsed columns are exposed under the view contract names.
        # Status stays the observed string or "unknown" — never guessed.
        view_status = 'snoozed' if gorgias_snoozed else (observed_status or 'unknown')
        # The exporter holds no operator identity, so it exports the observed
        # assignee address as-is; only the inbox service decides "me".
        # No observed name ⇒ derive one from the address local part (#35); the
        # raw address is never the display name. fromEmail keeps the address.
        ticket={'id':f'gorgias:{ticket_id}','subject':subject,'customerName':derived_customer_name(latest['customer_email']) or 'Customer',
          'customerContext':latest.get('customer_context',identity_context(latest,None)),
          'fromEmail':latest['customer_email'] or '', 'status':view_status,'assignee':observed_assignee or None,'channel':channel,'updatedAt':latest['received_at'],
          'snippet':messages[-1]['body'][:240], 'messages':messages,'statusEvents':[], 'projectionSource':True,
          'historyIncomplete':True,'truncated':bool(truncated),'observedMessageCount':latest['observed_count'],
          'tags':observed_tags,
          'readonlyDraft':draft_text,'draftReason':reason,'draftSuperseded':superseded,
          'draftSourceMessageId':draft['message_id'] if draft else None,'draftSourceMessageAt':(draft['created_at'] or draft['received_at']) if draft else None,'draftProcessedAt':draft['processed_at'] if draft else None,
          'priority':draft['priority'] if draft else None,'draftAction':draft['action'] if draft else None}
        ticket['gorgiasSpam']=bool(gorgias_spam)
        ticket['gorgiasTrashed']=bool(gorgias_trashed)
        ticket['gorgiasSnoozed']=bool(gorgias_snoozed)
        ticket['gorgiasPriority']=gorgias_priority or None
        ticket['spam']=bool(gorgias_spam)
        ticket['trashed']=bool(gorgias_trashed)
        ticket['assigneeEmail']=observed_assignee or None
        # An observed name always wins; a conflicted identity never names.
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
