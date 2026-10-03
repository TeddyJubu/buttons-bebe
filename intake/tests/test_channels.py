from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from intake import auth, channel_adapter as adapter, delivery, import_store, intake_jobs as jobs, recovery, replay
from intake.integrity import check_database
from intake.policy import Conflict, Invalid
from intake.private_files import checksum
from intake.records import canonical
from intake.store import APPLICATION_ID, Store
from intake.tests.test_replay import counts, event, payload


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.clock = patch('time.time', return_value=1_000_000)
        self.time = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.channel = adapter.create_channel(self.store, 'test', 'support@example.test', 'simulation')
        self.key = adapter.rotate_key(self.store, self.channel)

    def frame(self, events=None, **kw):
        return adapter.make_fixture(self.store, self.key, payload(events or [event('one')]), **kw)

    def enqueue(self, frame=None):
        frame = frame or self.frame()
        return adapter.enqueue(self.store, frame, adapter.enqueue(self.store, frame, preview=True)['digest'])

    def test_authentication_tampering_freshness_rotation_and_no_partial_admission(self):
        good = self.frame()
        original = json.loads(good)
        for field, value in (('body', original['body']+' '), ('delivery_id', 'changed'), ('purpose', 'receipt'),
            ('channel_id', 'other'), ('key_id', 'other'), ('signed_at', True), ('signature', 'é'*64), ('format', 'live')):
            bad = {**original, field:value}
            before = counts(self.store)
            with self.subTest(field=field), self.assertRaises(Invalid):
                adapter.enqueue(self.store, canonical(bad).encode(), preview=True)
            self.assertEqual(before, counts(self.store))
        for shift in (-301, 301):
            self.time.return_value = 1_000_000 + shift
            with self.assertRaises(Invalid):
                self.enqueue(good)
        self.time.return_value = 1_000_000
        next_key = adapter.rotate_key(self.store, self.channel)
        self.enqueue(good)  # Overlap during rotation remains valid.
        adapter.rotate_key(self.store, self.channel, revoke_previous=True)
        with self.assertRaises(Invalid):
            self.enqueue(good)
        with self.assertRaises(Invalid):
            adapter.make_fixture(self.store, next_key, payload([event('two')]))
        check_database(self.store.path)  # Revocation does not erase captured evidence.

    def test_verified_signature_cannot_cross_account_mailbox_provider_or_live_mode(self):
        bodies = [payload([event('one')], account='other'), payload([event('one')], mailbox='other@example.test'),
            payload([{**event('one'), 'provider':'other'}]), payload([event('one')]).replace(b'offline_replay',b'live')]
        for body in bodies:
            frame = adapter.make_fixture(self.store,self.key,body)
            before = counts(self.store)
            with self.assertRaises(Invalid):
                self.enqueue(frame)
            self.assertEqual(before,counts(self.store))

    def test_browser_identity_cannot_invoke_local_operator_mutations(self):
        frame=self.frame()
        token=auth.current.set({'id':'browser-session'})
        try:
            for action in (lambda: adapter.enqueue(self.store,frame,preview=True),
                lambda: adapter.rotate_key(self.store,self.channel), lambda: jobs.claim(self.store),
                lambda: jobs.resume(self.store),lambda: jobs.retry_dead(self.store,'anything')):
                with self.assertRaises(auth.Denied): action()
        finally: auth.current.reset(token)

    def test_malformed_signed_body_and_raw_unicode_fail_without_writes(self):
        malformed=payload([event('one')]).replace(b'Synthetic message one',b'\\ud800')
        frame=adapter.make_fixture(self.store,self.key,malformed)
        before=counts(self.store)
        with self.assertRaises(Invalid): self.enqueue(frame)
        bad=json.loads(self.frame()); bad['body']='\ud800'
        with self.assertRaises(Invalid): adapter.enqueue(self.store,json.dumps(bad).encode(),preview=True)
        self.assertEqual(before,counts(self.store))

    def test_preview_repeat_restart_and_concurrent_workers_apply_once(self):
        frame = self.frame([event('one'), event('two','one')])
        before = counts(self.store)
        adapter.enqueue(self.store,frame,preview=True)
        self.assertEqual(before,counts(self.store))
        with ThreadPoolExecutor(max_workers=5) as pool:
            reports = list(pool.map(lambda _: self.enqueue(frame),range(5)))
        self.assertEqual(sum(r['queued'] for r in reports),2)
        self.store=Store(self.tmp.name)
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda _: jobs.run(self.store), range(5)))
        self.assertEqual((counts(self.store)['tickets'], counts(self.store)['messages']), (1,2))
        self.assertEqual(jobs.health(self.store)['jobs'],{'done':2})
        self.assertEqual(self.enqueue(frame)['duplicates'],2)
        self.assertEqual(jobs.run(self.store)['outcomes'],{})
        check_database(self.store.path)

    def test_ack_only_after_capture_and_jobs_commit(self):
        original = self.store.event
        def crash(*args,**kwargs):
            if args[2]=='fake_envelope_queued':
                raise sqlite3.OperationalError('disk full with private contents')
            return original(*args,**kwargs)
        before=counts(self.store)
        with patch.object(self.store,'event',side_effect=crash), self.assertRaises(sqlite3.OperationalError):
            self.enqueue()
        self.assertEqual(before,counts(self.store))
        self.assertEqual(self.enqueue()['queued'],1)

    def test_capacity_refuses_new_batch_but_accepts_idempotent_redelivery(self):
        frame=self.frame()
        with patch.object(adapter,'MAX_PENDING',1):
            self.enqueue(frame)
            self.assertEqual(self.enqueue(frame)['duplicates'],1)
            with self.assertRaises(Conflict): self.enqueue(self.frame([event('two')]))
        self.assertEqual(counts(self.store)['intake_jobs'],1)
        self.assertEqual(counts(self.store)['adapter_envelopes'],1)

    def test_reused_capture_and_event_ids_changed_content_roll_back_batch(self):
        frame=self.frame(delivery_id='stable')
        self.enqueue(frame)
        changed=event('one'); changed['message']['body_text']='changed'
        for candidate in (self.frame([changed],delivery_id='stable'), self.frame([event('new'),changed])):
            before=counts(self.store)
            with self.assertRaises(Conflict):
                adapter.enqueue(self.store,candidate,checksum(candidate))
            self.assertEqual(before,counts(self.store))

    def test_provider_namespaces_do_not_collide_on_event_id(self):
        self.enqueue()
        other=adapter.create_channel(self.store,'test','support@example.test','other')
        key=adapter.rotate_key(self.store,other)
        second=event('second'); second['provider']='other'; second['event_id']='event-one'
        self.enqueue(adapter.make_fixture(self.store,key,payload([second])))
        self.assertEqual(jobs.run(self.store)['outcomes'],{'created':2})
        check_database(self.store.path)

    def test_expired_lease_is_fenced_across_restart(self):
        self.enqueue()
        first=jobs.claim(self.store)
        self.assertIsNone(jobs.claim(self.store))
        self.time.return_value+=61
        self.store=Store(self.tmp.name)
        second=jobs.claim(self.store)
        with self.assertRaises(jobs.LeaseLost):
            jobs.process(self.store,first)
        with self.assertRaises(jobs.LeaseLost):
            jobs.fail(self.store,first,'temporary_failure',transient=True)
        self.assertEqual(jobs.process(self.store,second)['outcome'],'created')
        with self.assertRaises(jobs.LeaseLost):
            jobs.process(self.store,second)
        self.assertEqual(counts(self.store)['messages'],1)

    def test_one_active_worker_per_channel_preserves_parent_order(self):
        self.enqueue(self.frame([event('one'),event('two','one')]))
        first=jobs.claim(self.store)
        self.assertIsNone(jobs.claim(self.store))
        channel=adapter.create_channel(self.store,'other','support@example.test','simulation')
        key=adapter.rotate_key(self.store,channel)
        other=adapter.make_fixture(self.store,key,payload([event('unrelated')],account='other'))
        self.enqueue(other)
        independent=jobs.claim(self.store)
        self.assertIsNotNone(independent)
        jobs.process(self.store,independent)
        parent=jobs.process(self.store,first)
        child=jobs.process(self.store,jobs.claim(self.store))
        self.assertEqual(parent['ticket_id'],child['ticket_id'])
        self.assertEqual(counts(self.store)['tickets'],2)

    def test_repeated_worker_crashes_exhaust_budget(self):
        self.enqueue()
        for _ in range(3):
            self.assertIsNotNone(jobs.claim(self.store))
            self.time.return_value+=61
        self.assertIsNone(jobs.claim(self.store))
        self.assertEqual(jobs.health(self.store)['jobs'],{'dead':1})
        self.assertEqual(counts(self.store)['messages'],0)

    def test_failure_after_ticket_write_rolls_back_and_retries_with_delays(self):
        self.enqueue()
        real=replay.receive
        def broken(*args):
            real(*args)
            raise sqlite3.OperationalError('sensitive database failure')
        with patch.object(replay,'receive',side_effect=broken):
            for i,delay in enumerate((30,120,0)):
                result=jobs.run_once(self.store)
                self.assertEqual(result['outcome'],'retry' if i<2 else 'dead')
                self.assertEqual((counts(self.store)['messages'],counts(self.store)['replay_receipts']),(0,0))
                self.assertEqual(jobs.run_once(self.store)['outcome'],'idle')
                self.time.return_value+=delay
        health=jobs.health(self.store)
        self.assertEqual(health['failure_codes'],{'temporary_failure':1})
        self.assertNotIn('sensitive',canonical(health))
        check_database(self.store.path)

    def test_success_after_transient_failure_and_lost_success_response(self):
        self.enqueue()
        with patch.object(replay,'receive',side_effect=TimeoutError):
            self.assertEqual(jobs.run_once(self.store)['outcome'],'retry')
        self.time.return_value+=30
        claimed=jobs.claim(self.store)
        jobs.process(self.store,claimed)  # Process commits; caller loses the response.
        self.store=Store(self.tmp.name)
        self.assertEqual(jobs.run_once(self.store)['outcome'],'idle')
        self.assertEqual(counts(self.store)['messages'],1)

    def test_processing_past_lease_deadline_rolls_back_everything(self):
        self.enqueue()
        claimed=jobs.claim(self.store)
        real=replay.receive
        def slow(*args):
            result=real(*args)
            self.time.return_value+=61
            return result
        with patch.object(replay,'receive',side_effect=slow), self.assertRaises(jobs.LeaseLost):
            jobs.process(self.store,claimed)
        self.assertEqual(counts(self.store)['messages'],0)
        self.assertEqual(counts(self.store)['replay_receipts'],0)
        self.assertEqual(jobs.run_once(self.store)['outcome'],'created')

    def test_unexpected_worker_error_is_redacted_and_does_not_spin(self):
        self.enqueue()
        with patch.object(replay,'receive',side_effect=RuntimeError('private subject and address')):
            self.assertEqual(jobs.run_once(self.store),{'outcome':'dead','code':'worker_error'})
        self.assertEqual(jobs.run_once(self.store)['outcome'],'idle')
        self.assertNotIn('private subject',canonical(jobs.health(self.store)))

    def test_poison_event_does_not_block_other_jobs_and_manual_retry_is_reviewed(self):
        self.enqueue(self.frame([event('bad'),event('good')]))
        with patch.object(replay,'receive',side_effect=Invalid('private bad data')):
            self.assertEqual(jobs.run_once(self.store)['outcome'],'dead')
        self.assertEqual(jobs.run_once(self.store)['outcome'],'created')
        with self.store.connection() as db:
            jid=db.execute("SELECT id FROM intake_jobs WHERE state='dead'").fetchone()[0]
        plan=jobs.retry_dead(self.store,jid)
        with self.assertRaises(Conflict):
            jobs.retry_dead(self.store,jid,'wrong','Investigated fixture')
        with self.assertRaises(Invalid):
            jobs.retry_dead(self.store,jid,plan['digest'],'')
        jobs.retry_dead(self.store,jid,plan['digest'],'Investigated synthetic failure')
        self.assertEqual(jobs.run_once(self.store)['outcome'],'created')
        self.assertEqual(counts(self.store)['messages'],2)

    def test_changed_message_conflict_does_not_mutate_original(self):
        self.enqueue(); jobs.run(self.store)
        changed=event('one'); changed['event_id']='different'; changed['message']['body_text']='changed'
        self.enqueue(self.frame([changed,event('good')]))
        self.assertEqual(jobs.run(self.store)['outcomes'],{'dead':1,'created':1})
        self.assertEqual(counts(self.store)['messages'],2)
        self.assertEqual(jobs.health(self.store)['failure_codes'],{'identity_conflict':1})

    def test_monitoring_is_redacted_read_only_and_reports_stalled_work(self):
        self.enqueue()
        before=counts(self.store)
        self.time.return_value+=301
        health=jobs.health(self.store)
        self.assertIn('backlog_over_5m',health['problems'])
        self.assertIn('worker_stale',health['problems'])
        self.assertEqual(before,counts(self.store))
        for private in ('@example.test',self.channel,self.key,'Synthetic message'):
            self.assertNotIn(private,canonical(health))
        jobs.claim(self.store); self.time.return_value+=61
        self.assertIn('expired_leases',jobs.health(self.store)['problems'])

    def accepted(self, scenario='accepted_timeout'):
        self.enqueue(); jobs.run(self.store)
        tid=self.store.list_tickets()['tickets'][0]['id']
        reviewed=delivery.review(self.store,tid,{'mode':delivery.MODE,'operation_id':'review','revision':self.store.get_ticket(tid)['revision'],
            'body':'Synthetic answer','scenario':scenario})
        attempt=delivery.confirm(self.store,tid,{'mode':delivery.MODE,'review_id':reviewed['review_id'],'digest':reviewed['digest'],'confirmed':True})
        channel=adapter.create_channel(self.store,'test','support@example.test','fake-delivery')
        key=adapter.rotate_key(self.store,channel)
        with self.store.connection() as db:
            receipt=db.execute('SELECT receipt_id FROM fake_dispatches WHERE attempt_id=?',(attempt['attempt_id'],)).fetchone()[0]
        value={'mode':'offline_receipt','attempt_id':attempt['attempt_id'],'review_digest':reviewed['digest'],'receipt_id':receipt or 'missing','status':'delivered'}
        return tid,attempt,key,value

    def test_receipt_order_duplicates_conflicts_never_resend_or_clear_uncertainty(self):
        tid,attempt,key,value=self.accepted()
        frame=adapter.make_fixture(self.store,key,canonical(value).encode(),'receipt')
        self.enqueue(frame); self.enqueue(frame)
        jobs.run(self.store)
        self.assertEqual(jobs.health(self.store)['receipt_states'],{'delivered':1})
        self.assertEqual(jobs.health(self.store)['unresolved_attempts'],1)
        for status in ('deferred','accepted','bounced','complained','delivered'):
            self.enqueue(adapter.make_fixture(self.store,key,canonical({**value,'status':status}).encode(),'receipt'))
            jobs.run(self.store)
        self.assertEqual(jobs.health(self.store)['receipt_states'],{'conflict':1})
        self.assertEqual(counts(self.store)['fake_dispatches'],1)
        self.assertEqual(counts(self.store)['simulated_outgoing'],0)
        delivery.reconcile(self.store,tid,{'mode':delivery.MODE,'attempt_id':attempt['attempt_id'],'confirmed':True})
        self.assertEqual(counts(self.store)['simulated_outgoing'],1)
        check_database(self.store.path)

    def test_receipt_cannot_forge_account_or_acceptance_bindings(self):
        _,_,key,value=self.accepted()
        for field in ('review_digest','receipt_id'):
            self.enqueue(adapter.make_fixture(self.store,key,canonical({**value,field:'wrong'}).encode(),'receipt'))
            self.assertEqual(jobs.run_once(self.store)['outcome'],'dead')
        wrong=adapter.create_channel(self.store,'other','support@example.test','fake-delivery')
        wrong_key=adapter.rotate_key(self.store,wrong)
        self.enqueue(adapter.make_fixture(self.store,wrong_key,canonical(value).encode(),'receipt'))
        self.assertEqual(jobs.run_once(self.store)['outcome'],'dead')
        self.assertEqual(counts(self.store)['delivery_observations'],0)
        self.assertEqual(jobs.health(self.store)['unresolved_attempts'],1)

    def test_missing_receipt_evidence_retries_without_dispatching(self):
        value={'mode':'offline_receipt','attempt_id':'absent','review_digest':'absent','receipt_id':'absent','status':'accepted'}
        self.enqueue(adapter.make_fixture(self.store,self.key,canonical(value).encode(),'receipt'))
        for delay in (30,120,0):
            jobs.run_once(self.store); self.time.return_value+=delay
        self.assertEqual(jobs.health(self.store)['jobs'],{'dead':1})
        self.assertEqual(counts(self.store)['fake_dispatches'],0)

    def test_unknown_dispatch_receipt_cannot_authorize_delivery(self):
        _,_,key,value=self.accepted('unknown')
        self.enqueue(adapter.make_fixture(self.store,key,canonical(value).encode(),'receipt'))
        self.assertEqual(jobs.run_once(self.store)['outcome'],'dead')
        self.assertEqual(jobs.health(self.store)['unresolved_attempts'],1)
        self.assertEqual(counts(self.store)['simulated_outgoing'],0)

    def test_restore_holds_admissions_workers_and_revokes_keys_fences_old_claim(self):
        self.enqueue(self.frame([event('one'),event('two')]))
        jobs.run_once(self.store)
        old=jobs.claim(self.store)
        backup=recovery.backup(self.store,'checkpoint')
        with patch.object(recovery,'ROOT',Path(self.tmp.name)):
            report=recovery.restore(Path(self.tmp.name)/'backups/checkpoint','restored',backup['digest'])
            restored=Store(recovery.workspace_path('restored'))
        self.assertTrue(report['intake_held'])
        self.assertIn('recovery_hold',jobs.health(restored)['problems'])
        with self.assertRaises(Conflict): jobs.claim(restored)
        frame=self.frame()
        with self.assertRaises(Conflict): adapter.enqueue(restored,frame,checksum(frame))
        with self.assertRaises(jobs.LeaseLost): jobs.process(restored,old)
        plan=jobs.resume(restored)
        with self.assertRaises(Conflict): jobs.resume(restored,'stale')
        jobs.resume(restored,plan['digest'])
        self.assertEqual(jobs.run(restored)['outcomes'],{'created':1})
        with self.assertRaises(Invalid): adapter.enqueue(restored,frame,checksum(frame))
        self.assertEqual(counts(restored)['messages'],2)
        check_database(restored.path)
        self.assertEqual(counts(self.store)['messages'],1)

    def test_integrity_detects_job_and_signed_capture_tampering(self):
        self.enqueue(); jobs.run(self.store)
        for table,column,value in (('adapter_envelopes','body','{}'),('intake_jobs','payload_json','{}'),
            ('intake_jobs','result_json','{}'),('intake_jobs','lease_token','unexpected')):
            with self.store.connection(write=True) as db:
                old=db.execute(f'SELECT {column} FROM {table}').fetchone()[0]
                db.execute(f'UPDATE {table} SET {column}=?',(value,))
            with self.assertRaises(Invalid): check_database(self.store.path)
            with self.store.connection(write=True) as db: db.execute(f'UPDATE {table} SET {column}=?',(old,))
        check_database(self.store.path)

    def test_integrity_rejects_missing_job_from_authenticated_capture(self):
        self.enqueue(self.frame([event('one'), event('two')]))
        with self.store.connection(write=True) as db:
            db.execute("DELETE FROM intake_jobs WHERE event_id='event-one'")
        with self.assertRaises(Invalid): check_database(self.store.path)
        with self.assertRaises(Invalid): recovery.backup(self.store, 'incomplete-capture')

    def test_integrity_accepts_redelivery_jobs_bound_to_an_earlier_capture(self):
        self.enqueue(self.frame([event('one')]))
        self.assertEqual(self.enqueue(self.frame([event('one'), event('two')]))['queued'], 1)
        self.assertEqual(self.enqueue(self.frame([event('one')]))['duplicates'], 1)
        self.assertEqual(counts(self.store)['adapter_envelopes'], 3)
        self.assertEqual(counts(self.store)['intake_jobs'], 2)
        check_database(self.store.path)
        self.assertEqual(jobs.run(self.store)['outcomes'], {'created': 2})
        check_database(self.store.path)

    def test_integrity_rejects_missing_native_message_despite_completed_receipt(self):
        self.enqueue(); jobs.run(self.store)
        with self.store.connection(write=True) as db:
            db.execute('DELETE FROM replay_messages')
            db.execute('DELETE FROM messages')
        self.assertEqual(counts(self.store)['replay_receipts'], 1)
        self.assertEqual(jobs.run(self.store)['outcomes'], {})
        with self.assertRaises(Invalid): check_database(self.store.path)
        with self.assertRaises(Invalid): recovery.backup(self.store, 'missing-message')

    def test_integrity_checks_native_projection_parent_and_receipt_payload(self):
        self.enqueue(self.frame([event('one'), event('two')]))
        jobs.run(self.store)
        with self.store.connection() as db:
            first = db.execute('SELECT * FROM messages ORDER BY sequence LIMIT 1').fetchone()
            second = db.execute('SELECT * FROM messages ORDER BY sequence DESC LIMIT 1').fetchone()
        for column, value in (('body', 'Changed body'), ('author_name', 'Changed author'),
            ('author_email', 'other@example.test'), ('created_at', '2026-09-02T12:00:00Z'),
            ('kind', 'note'), ('origin', 'manual_test'), ('headers_json', '{}'), ('ticket_id', second['ticket_id'])):
            with self.subTest(column=column):
                with self.store.connection(write=True) as db:
                    db.execute(f'UPDATE messages SET {column}=? WHERE id=?', (value, first['id']))
                with self.assertRaises(Invalid): check_database(self.store.path)
                with self.store.connection(write=True) as db:
                    db.execute(f'UPDATE messages SET {column}=? WHERE id=?', (first[column], first['id']))
        with self.store.connection(write=True) as db:
            job = db.execute("SELECT * FROM intake_jobs WHERE event_id='event-one'").fetchone()
            receipt_id = jobs.replay_key({'provider': 'simulation'}, 'event-one')
            wrong = {**json.loads(job['result_json']), 'message_id': second['id'], 'ticket_id': second['ticket_id']}
            db.execute('UPDATE intake_jobs SET result_json=? WHERE id=?', (canonical(wrong), job['id']))
            db.execute('UPDATE replay_receipts SET result_json=? WHERE event_id=?', (canonical(wrong), receipt_id))
        with self.assertRaises(Invalid): check_database(self.store.path)
        with self.store.connection(write=True) as db:
            db.execute('UPDATE intake_jobs SET result_json=? WHERE id=?', (job['result_json'], job['id']))
            db.execute('UPDATE replay_receipts SET result_json=? WHERE event_id=?', (job['result_json'], receipt_id))
        check_database(self.store.path)
        with self.store.connection(write=True) as db:
            db.execute("UPDATE replay_receipts SET payload_json='{}'")
        with self.assertRaises(Invalid): check_database(self.store.path)

    def duplicate_provenance(self, imported):
        original = event('saved')
        if imported:
            raw = canonical({'tickets': [{'id': 'saved-ticket', 'status': 'closed', 'subject': 'Saved source',
                'created_datetime': '2026-09-01T12:00:00Z', 'messages_count': 1,
                'customer': {'id': 'saved-customer', 'email': 'customer@example.test'},
                'messages': [original['message']]}]}).encode()
            plan = import_store.preview(self.store, raw, 'test')
            import_store.apply_import(self.store, raw, 'test', plan['digest'])
        else:
            original['provider'] = 'earlier-provider'
            raw = payload([original])
            replay.run(self.store, raw, replay.run(self.store, raw, preview=True)['digest'])
        duplicate = event('saved')
        duplicate['event_id'] = 'redelivered-event'
        duplicate['message']['id'] = 'redelivered-message'
        duplicate['message']['sender']['name'] = 'Another transport display name'
        duplicate['message']['created_datetime'] = '2026-09-02T12:00:00Z'
        self.enqueue(self.frame([duplicate]))
        self.assertEqual(jobs.run(self.store)['outcomes'], {'duplicate_message': 1})
        self.assertEqual(counts(self.store)['messages'], 1)
        check_database(self.store.path)
        with self.store.connection(write=True) as db:
            alias = tuple(db.execute('SELECT * FROM replay_aliases').fetchone())
            db.execute('DELETE FROM replay_aliases')
        with self.assertRaises(Invalid): check_database(self.store.path)
        with self.store.connection(write=True) as db:
            db.execute('INSERT INTO replay_aliases VALUES (?,?,?,?,?,?)', alias)
        check_database(self.store.path)
        with self.store.connection(write=True) as db:
            db.execute("DELETE FROM source_records WHERE kind='message'" if imported else 'DELETE FROM replay_messages')
        # A surviving native row and alias cannot replace its original source.
        with self.assertRaises(Invalid): check_database(self.store.path)

    def test_integrity_duplicate_imported_history_requires_retained_source(self):
        self.duplicate_provenance(imported=True)

    def test_integrity_duplicate_cross_provider_replay_requires_retained_source(self):
        self.duplicate_provenance(imported=False)

    def test_integrity_checks_ignored_result_against_signed_message(self):
        self.enqueue(self.frame([event('one'), event('echo', from_agent=True),
            event('note', public=False), event('self', sender='support@example.test')]))
        self.assertEqual(jobs.run(self.store)['outcomes'], {'created': 1, 'ignored': 3})
        check_database(self.store.path)
        ignored = canonical({'outcome': 'ignored', 'reason': 'agent_echo_or_internal_note', 'reopened': False})
        with self.store.connection(write=True) as db:
            db.execute('UPDATE intake_jobs SET result_json=?', (ignored,))
            db.execute('UPDATE replay_receipts SET result_json=?', (ignored,))
        with self.assertRaises(Invalid): check_database(self.store.path)

    def test_integrity_duplicate_event_keeps_original_native_receipt_binding(self):
        self.enqueue(); jobs.run(self.store)
        # Exercise receipt-level recovery: a pending job finds a durable receipt
        # instead of applying its already committed native writes again.
        with self.store.connection(write=True) as db:
            db.execute("UPDATE intake_jobs SET state='queued',attempts=0,result_json='{}'")
        self.assertEqual(jobs.run(self.store)['outcomes'], {'duplicate_event': 1})
        check_database(self.store.path)
        self.assertEqual(counts(self.store)['messages'], 1)
        with self.store.connection(write=True) as db:
            db.execute('DELETE FROM replay_messages')
        with self.assertRaises(Invalid): check_database(self.store.path)

    def test_schema_six_preserved_and_upgraded_without_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'intake.sqlite3'
            db=sqlite3.connect(path)
            db.executescript(Path('intake/schema-v6.sql').read_text())
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute('PRAGMA user_version=6')
            db.execute("INSERT INTO sandbox_meta VALUES ('mode','offline_sandbox')")
            db.commit(); db.close()
            self.assertNotIn('intake_jobs',check_database(path)['tables'])
            upgraded=Store(directory)
            self.assertEqual(check_database(path)['tables']['intake_jobs'],0)
            self.assertEqual(jobs.health(upgraded)['jobs'],{})


if __name__=='__main__': unittest.main()
