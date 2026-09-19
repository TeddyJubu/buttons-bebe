"""Credential-free, read-only canonical ticket snapshot and query contract."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import closing

from shop_rail import attach as attach_shop_rail, connect

DEFAULT_PATH = '/var/lib/buttonsbebe-inbox-projection/projection.sqlite3'
VERSION = 1
class ProjectionUnavailable(Exception): pass


def status(db, path):
    row=db.execute('SELECT payload FROM metadata WHERE id=1').fetchone()
    if not row: raise ProjectionUnavailable('Missing projection metadata')
    meta=json.loads(row[0])
    if meta.get('version') != VERSION: raise ProjectionUnavailable('Unsupported projection schema')
    meta['stale']=time.time()-meta['generatedAtEpoch']>180
    meta['exportError']=Path(path).with_suffix('.error').exists()
    meta['stale']=meta['stale'] or meta['exportError']
    return meta


def query(tool, args, path=None):
    path=path or os.environ.get('INBOX_PROJECTION_PATH',DEFAULT_PATH)
    try:
        with closing(connect(path)) as db:
            meta=status(db,path)
            if tool=='helpdesk.projection_status': return {'ok':True,'projection':meta}
            if tool=='helpdesk.list_tickets':
                limit=args.get('limit',20); offset=args.get('offset',0)
                rows=db.execute('SELECT summary FROM tickets ORDER BY observed_at DESC,id LIMIT ? OFFSET ?',(limit,offset)).fetchall()
                return {'ok':True,'source':'canonical_projection','tickets':[json.loads(r[0]) for r in rows], 'projection':meta,'nextOffset':offset+len(rows) if offset+len(rows)<meta['ticketCount'] else None}
            if tool=='helpdesk.search_tickets':
                # #37: search stays local and read-only. The query is a bound
                # parameter (never SQL), whitespace-collapsed, and clamped so a
                # hostile length cannot dump or stall the snapshot. Matching
                # is a substring over the observed summary fields, LIKE-free.
                needle=' '.join(str(args.get('query','')).split())[:200]
                limit=args.get('limit',20); offset=args.get('offset',0)
                rows=db.execute('SELECT summary FROM tickets ORDER BY observed_at DESC,id').fetchall()
                hits=[]
                for row in rows:
                    summary=json.loads(row[0])
                    haystack=' \n'.join(str(summary.get(key) or '') for key in ('subject','customerName','fromEmail','snippet'))
                    if needle and needle.casefold() in haystack.casefold(): hits.append(summary)
                total=len(hits)
                page=hits[offset:offset+limit]
                return {'ok':True,'source':'canonical_projection','tickets':page,'total':total,'projection':meta,
                        'nextOffset':offset+len(page) if offset+len(page)<total else None}
            row=db.execute('SELECT detail FROM tickets WHERE id=?',(args['ticketId'],)).fetchone()
            if not row: return {'ok':False,'error':'ticket_not_found','message':'Ticket is not in the observed history window.'}
            ticket=json.loads(row[0]);ticket['projection']=meta
            attach_shop_rail(ticket)
            return {'ok':True,'source':'canonical_projection','ticket':ticket,'projection':meta}
    except (sqlite3.Error,ValueError,KeyError,OSError) as exc:
        raise ProjectionUnavailable('Canonical ticket projection is unavailable') from exc
