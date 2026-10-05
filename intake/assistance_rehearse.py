"""Prepare missing-context fixtures on a fresh, reconciled saved-export copy.

This does not evaluate message meaning or invent customer/order facts. Browser
proof subsequently runs only the canned needs_staff result against native IDs.
"""
import argparse
import json
import os
import sqlite3

from . import assistance
from .assistance_demo import make_fixture
from .policy import Invalid, install_offline_guard
from .private_files import private_write, read_bytes, json_bytes
from .records import canonical, digest
from .recovery import workspace_path
from .store import Store

BUSINESS_TABLES = ('tickets','contacts','messages','attachments','source_records','import_batches',
                   'replay_receipts','replay_messages','delivery_reviews','delivery_attempts','fake_dispatches',
                   'simulated_outgoing','attachment_blobs','attachment_files','ticket_assignments','ticket_reads')


def business_digest(store):
    with store.connection() as db:
        db.execute('BEGIN')
        result={table:[dict(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in BUSINESS_TABLES}
        # The saved real sample contains no attachment bytes. Hash bytes rather
        # than serializing them if this proof is later used with a richer sample.
        for row in result['attachment_blobs']:
            from .private_files import checksum
            row['data']=checksum(row['data'])
        return digest(result)


def prepare(store):
    directory=store.path.parent
    original=json_bytes(read_bytes(directory/'reconciliation.json',10*1024*1024))
    if not original.get('passed'):
        raise Invalid('First reconcile the saved export in a fresh proof workspace.')
    with store.connection() as db:
        if db.execute('SELECT count(*) FROM assistance_fixtures').fetchone()[0]:
            raise Invalid('Preserve existing assistance evidence; use a fresh proof workspace.')
        tickets=list(db.execute('SELECT id,origin FROM tickets ORDER BY number'))
        if not 1 <= len(tickets) <= 50 or any(row['origin']!='gorgias_export' for row in tickets):
            raise Invalid('Use a fresh source-only sample of 1–50 saved tickets.')
    before=business_digest(store);cases=[];messages=0
    for row in tickets:
        saved=assistance.read_input(store,row['id']);source=saved['input']
        # Every context source is explicitly unavailable. These are adapter
        # fixtures, not new claims about any actual customer's circumstances.
        value=make_fixture(source,'needs_staff',evidence=False)
        value['label']='Saved-export interface test: context unavailable'
        value['response']['priority']=source['ticket']['priority']
        value['response']['reason']='Offline contract check only. No model ran and no order, return, product or policy lookup was performed.'
        data=canonical(value).encode();planned=assistance.import_fixture(store,data,preview=True)
        assistance.import_fixture(store,data,planned['digest'])
        cases.append({'ticket_id':row['id'],'input_digest':saved['input_digest'],'messages':len(source['messages'])})
        messages+=len(source['messages'])
    if business_digest(store)!=before:
        raise Invalid('Preparing fixtures changed saved business records.')
    report={'prepared':True,'tickets':len(cases),'messages':messages,'business_digest':before,'cases':cases,'modelCalls':0,'outboundActions':0}
    private_write(directory/'assistance-preparation.json',canonical(report).encode())
    return {key:value for key,value in report.items() if key not in ('cases','business_digest')}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workspace',required=True);args=parser.parse_args()
    install_offline_guard();os.umask(0o077)
    try:
        directory=workspace_path(args.workspace)
        if not (directory/'intake.sqlite3').is_file():
            raise Invalid('First create a fresh saved-export proof workspace.')
        with sqlite3.connect((directory/'intake.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            if db.execute('PRAGMA user_version').fetchone()[0]!=7:
                raise Invalid('Keep earlier proof workspaces intact. Rehearse into a fresh schema-7 workspace.')
        print(json.dumps(prepare(Store(directory)),indent=2))
        return 0
    except (Invalid,OSError,sqlite3.Error):
        print('Assistance preparation failed; inspect this private proof workspace. No external action was attempted.')
        return 1


if __name__=='__main__':raise SystemExit(main())
