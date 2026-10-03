"""E4 proof: saved history through a signed fake queue; fabricated follow-ups only."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3

from . import channel_adapter as adapter, delivery, import_store, intake_jobs as jobs, recovery
from .imports import account_name
from .integrity import check_database
from .mail import metadata
from .policy import Conflict, Invalid, install_offline_guard, workspace_directory
from .private_files import private_write
from .records import canonical, digest
from .rehearse import fidelity, read_snapshot
from .store import Store


def table_digest(store, tables):
    with store.connection() as db:
        return digest({name:[tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')] for name in tables})


def rehearse(directory, workspace, account):
    account=account_name(account)
    if recovery.workspace_path(workspace).exists() or recovery.workspace_path(workspace+'-restored').exists():
        raise Invalid('Use fresh proof and restored workspace names; prior evidence is never changed.')
    manifest,files=read_snapshot(directory)
    store=Store(workspace_directory(workspace))
    for data in files:
        planned=import_store.preview(store,data,account)
        import_store.apply_import(store,data,account,planned['digest'])
    checked=fidelity(store,files,account)
    if not checked['passed']:
        raise Invalid('Saved import fidelity failed.')
    checks,failures=Counter(),Counter()
    def check(label,value):
        checks[label]+=1
        if not value: failures[label]+=1
    sources=table_digest(store,['source_records','import_batches'])
    business=['tickets','contacts','messages','attachments','source_records','import_batches']
    initial_business=table_digest(store,business)
    source_tickets=[t for data in files for t in json.loads(data)['tickets']]
    future=max(datetime.fromisoformat(t['updated_datetime']) for t in source_tickets)+timedelta(days=1)
    historical,probes=defaultdict(list),defaultdict(list)
    expected,gaps={},Counter()
    with store.connection() as db:
        for ticket in source_tickets:
            tid=db.execute("SELECT internal_id FROM source_records WHERE kind='ticket' AND account=? AND external_id=?",(account,str(ticket['id']))).fetchone()[0]
            for raw in ticket['messages']:
                message=metadata(raw)
                mailboxes=[message['sender']] if raw['from_agent'] else message['recipients']
                if message['channel']!='email' or not mailboxes or not mailboxes[0]:
                    gaps['unsupported_channel_or_missing_mailbox']+=1
                    continue
                mailbox=mailboxes[0]
                historical[(mailbox,'gorgias')].append({'event_id':'historical-'+str(raw['id']),'provider':'gorgias',
                    'subject':ticket.get('subject') or 'Untitled ticket','message':raw})
                participants=message['recipients'] if raw['from_agent'] else [message['sender']]
                if not message['rfc_id'] or message['header_issue'] or not participants or not participants[0] or message['kind']=='note':
                    gaps['no_usable_thread_anchor']+=1
                    continue
                if participants[0]==mailbox:
                    gaps['self_addressed_anchor']+=1
                    continue
                name='e4-probe-'+str(len(expected)+1)
                probes[(mailbox,'simulation')].append({'event_id':name,'provider':'simulation','subject':'Offline queue follow-up',
                    'message':{'id':name,'channel':'email','public':True,'from_agent':False,'created_datetime':future.isoformat(),
                    'body_text':'Fabricated E4 local test follow-up. Never delivered.',
                    'sender':{'name':'Offline test participant','email':participants[0]},
                    'source':{'from':{'address':participants[0]},'to':[{'address':mailbox}]},
                    'headers':{'Message-ID':'<'+name+'@example.test>','In-Reply-To':'<'+message['rfc_id']+'>'},'attachments':[]}})
                expected[name]=tid
    def enqueue_groups(groups):
        frames=[]
        for (mailbox,provider),events in groups.items():
            channel=adapter.create_channel(store,account,mailbox,provider)
            key=adapter.rotate_key(store,channel)
            for start in range(0,len(events),500):
                blob=canonical({'mode':'offline_replay','account':account,'mailbox':mailbox,'events':events[start:start+500]}).encode()
                frame=adapter.make_fixture(store,key,blob)
                before=table_digest(store,['adapter_envelopes','intake_jobs','events'])
                plan=adapter.enqueue(store,frame,preview=True)
                check('preview_no_writes',before==table_digest(store,['adapter_envelopes','intake_jobs','events']))
                adapter.enqueue(store,frame,plan['digest'])
                check('duplicate_capture_no_new_jobs',adapter.enqueue(store,frame,plan['digest'])['queued']==0)
                frames.append(frame)
        return frames
    enqueue_groups(historical)
    check('admission_does_not_touch_tickets',table_digest(store,business)==initial_business)
    historical_outcomes=jobs.run(store,500)['outcomes']
    check('history_only_duplicates_or_echoes',set(historical_outcomes)<={'duplicate_message','ignored'})
    check('history_business_unchanged',table_digest(store,business)==initial_business)
    check('history_fidelity',fidelity(store,files,account)['passed'])
    frames=enqueue_groups(probes)
    old_claim=jobs.claim(store)
    check('claimed_job_has_no_ticket_effect',table_digest(store,business)==initial_business)
    backup=recovery.backup(store,'e4-queue-checkpoint')
    checkpoint=store.path.parent/'backups/e4-queue-checkpoint'
    check('backup_verifies',recovery.verify(checkpoint)['digest']==backup['digest'])
    restored_result=recovery.restore(checkpoint,workspace+'-restored',backup['digest'])
    restored=Store(workspace_directory(workspace+'-restored'))
    check('restored_queue_held',restored_result['intake_held'])
    try: jobs.claim(restored)
    except Conflict: check('restored_claim_blocked',True)
    else: check('restored_claim_blocked',False)
    try: jobs.process(restored,old_claim)
    except jobs.LeaseLost: check('old_claim_fenced',True)
    else: check('old_claim_fenced',False)
    jobs.resume(restored,jobs.resume(restored)['digest'])
    restored_outcomes=jobs.run(restored,500)['outcomes']
    check('restored_pending_processed',restored_outcomes=={'appended':len(expected)})
    # Original lease stays valid independently; its durable work commits once.
    jobs.process(store,old_claim)
    jobs.run(store,500)
    for target in (store,restored):
        with target.connection() as db:
            rows=list(db.execute("SELECT event_id,result_json FROM intake_jobs WHERE kind='inbound' AND state='done'"))
            for row in rows:
                if row['event_id'] in expected:
                    value=json.loads(row['result_json'])
                    check('followup_correct_original_ticket',value.get('ticket_id')==expected[row['event_id']] and value['outcome']=='appended')
            check('original_ticket_count',db.execute('SELECT count(*) FROM tickets').fetchone()[0]==manifest['counts']['tickets'])
            check('exact_message_count',db.execute('SELECT count(*) FROM messages').fetchone()[0]==manifest['counts']['messages']+len(expected))
        check('original_sources_unchanged',table_digest(target,['source_records','import_batches'])==sources)
        check('all_jobs_complete',jobs.health(target)['jobs']=={'done':sum(map(len,historical.values()))+len(expected)})
        check_database(target.path)
    # Restart + repeated signed admission cannot reapply a completed event.
    before=table_digest(store,business)
    store=Store(workspace_directory(workspace))
    for frame in frames:
        check('repeat_no_new_jobs',adapter.enqueue(store,frame,adapter.enqueue(store,frame,preview=True)['digest'])['queued']==0)
    check('restart_idle',jobs.run(store)['outcomes']=={})
    check('repeat_business_unchanged',table_digest(store,business)==before)
    # Test two saved-source targets with fabricated replies and local receipts.
    with store.connection() as db: tids=[r[0] for r in db.execute('SELECT id FROM tickets ORDER BY number')]
    selected=[tid for tid in tids if store.get_ticket(tid)['reply_context']['available']][:2]
    check('two_receipt_scenarios_available',len(selected)==2)
    for tid,scenario in zip(selected,('accepted_timeout','unknown')):
        reviewed=delivery.review(store,tid,{'mode':delivery.MODE,'operation_id':'e4-'+scenario,
            'revision':store.get_ticket(tid)['revision'],'body':'Fabricated E4 fake delivery only.','scenario':scenario})
        attempt=delivery.confirm(store,tid,{'mode':delivery.MODE,'review_id':reviewed['review_id'],'digest':reviewed['digest'],'confirmed':True})
        check('timeout_retains_uncertainty',attempt['state']=='uncertain')
        if scenario=='accepted_timeout':
            channel=adapter.create_channel(store,account,reviewed['envelope']['from'],'fake-delivery')
            key=adapter.rotate_key(store,channel)
            with store.connection() as db:
                rid=db.execute('SELECT receipt_id FROM fake_dispatches WHERE attempt_id=?',(attempt['attempt_id'],)).fetchone()[0]
            for status in ('delivered','deferred','accepted','bounced'):
                value={'mode':'offline_receipt','attempt_id':attempt['attempt_id'],'review_digest':reviewed['digest'],'receipt_id':rid,'status':status}
                frame=adapter.make_fixture(store,key,canonical(value).encode(),'receipt')
                adapter.enqueue(store,frame,adapter.enqueue(store,frame,preview=True)['digest'])
                jobs.run(store)
            check('late_conflicting_receipts_visible',jobs.health(store)['receipt_states']=={'conflict':1})
            check('callback_does_not_clear_uncertainty',store.get_ticket(tid)['deliveries'][0]['state']=='uncertain')
        result=delivery.reconcile(store,tid,{'mode':delivery.MODE,'attempt_id':attempt['attempt_id'],'confirmed':True})
        check('explicit_reconcile_uses_durable_evidence',result['state']==('simulated_delivered' if scenario=='accepted_timeout' else 'uncertain'))
    final=check_database(store.path)
    check('no_automatic_redispatch',final['tables']['fake_dispatches']==2)
    check('one_fake_outgoing_only',final['tables']['simulated_outgoing']==1)
    check('unknown_still_blocked',final['unresolved_attempts']==1)
    check('sources_unchanged_after_receipts',table_digest(store,['source_records','import_batches'])==sources)
    report={'passed':not failures,'checks':dict(checks),'failures':dict(failures),'source_counts':manifest['counts'],
        'fidelity_checks':sum(checked['checks'].values()),'historical_outcomes':historical_outcomes,'fabricated_followups':len(expected),
        'original_tickets_threaded':len(set(expected.values())),'restored_followups':restored_outcomes,'coverage_gaps':dict(gaps),
        'health':jobs.health(store),'final_counts':final['tables'],'outboundActions':0,
        'limitations':['Saved sample only; no provider integration or production identity proof.',
            'Follow-ups, signatures and receipt observations are fabricated locally.',
            'Real attachment bytes, broader source coverage, real delivery and model quality remain unproven.']}
    private_write(store.path.parent/'channel-reconciliation.json',canonical(report).encode())
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot',type=Path);parser.add_argument('--workspace',required=True);parser.add_argument('--account',required=True)
    args=parser.parse_args();install_offline_guard();os.umask(0o077)
    try: report=rehearse(args.snapshot,args.workspace,args.account)
    except (Invalid,OSError,ValueError,KeyError,sqlite3.Error):
        print('Channel rehearsal failed. Inspect the private workspace; no external action was attempted.')
        return 1
    print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
