from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from intake import delivery, fake_delivery, replay
from intake.policy import Conflict, Invalid
from intake.records import canonical
from intake.store import APPLICATION_ID, Store
from intake.tests.test_replay import counts, event, payload


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        data = payload([event('incoming')])
        result = replay.run(self.store, data, replay.run(self.store, data, preview=True)['digest'])
        self.tid = result['results'][0]['ticket_id']
        self.seq = 0

    def review(self, scenario='accepted', body='Synthetic answer', **extra):
        self.seq += 1
        data = {'mode': delivery.MODE, 'operation_id': 'review-' + str(self.seq),
                'revision': self.store.get_ticket(self.tid)['revision'], 'body': body,
                'scenario': scenario, **extra}
        return delivery.review(self.store, self.tid, data)

    def confirm(self, review, **extra):
        return delivery.confirm(self.store, self.tid, {'mode': delivery.MODE, 'review_id': review['review_id'],
            'digest': review['digest'], 'confirmed': True, **extra})

    def reconcile(self, result):
        return delivery.reconcile(self.store, self.tid, {'mode': delivery.MODE,
            'attempt_id': result['attempt_id'], 'confirmed': True})

    def test_review_freezes_envelope_without_dispatch_or_outgoing(self):
        reviewed = self.review()
        self.assertEqual((reviewed['envelope']['from'], reviewed['envelope']['to']),
                         ('support@example.test', 'customer@example.test'))
        self.assertEqual(reviewed['envelope']['in_reply_to'], '<incoming@example.test>')
        self.assertEqual(counts(self.store)['delivery_attempts'], 0)
        self.assertEqual(counts(self.store)['fake_dispatches'], 0)
        self.assertEqual(counts(self.store)['messages'], 1)
        for changes in ({'confirmed': False}, {'confirmed': 'true'}, {'digest': 'wrong'},
                        {'body': 'Changed after review'}, {'recipient': 'wrong@example.test'}, {'mode': 'live'}):
            with self.subTest(changes=changes), self.assertRaises(Invalid):
                self.confirm(reviewed, **changes)
        self.assertEqual(counts(self.store)['fake_dispatches'], 0)

    def test_success_is_one_simulated_message_with_frozen_text_and_headers(self):
        reviewed = self.review()
        result = self.confirm(reviewed)
        self.assertEqual(result['state'], 'simulated_delivered')
        ticket = self.store.get_ticket(self.tid)
        message = ticket['messages'][-1]
        self.assertEqual((message['origin'], message['kind'], message['body']), ('offline_simulation', 'outgoing', reviewed['body']))
        self.assertEqual(json.loads(message['headers_json'])['In-Reply-To'], reviewed['envelope']['in_reply_to'])
        self.assertEqual(ticket['status'], 'open')
        self.assertEqual(result['outboundActions'], 0)
        self.assertEqual(counts(self.store)['fake_dispatches'], 1)
        self.assertEqual(ticket['deliveries'][0]['state'], 'simulated_delivered')

    def test_repeat_confirm_and_restart_never_dispatch_again(self):
        reviewed = self.review()
        first = self.confirm(reviewed)
        before = counts(self.store)
        self.store = Store(self.temp.name)
        with patch.object(fake_delivery, 'dispatch', side_effect=AssertionError('Must not dispatch')):
            self.assertEqual(self.confirm(reviewed), first)
            self.assertEqual(self.reconcile(first), first)
        self.assertEqual(counts(self.store), before)
        with self.assertRaises(Conflict):
            self.review()  # New operation/review IDs cannot duplicate the same content.

    def test_parallel_confirm_and_two_reviews_have_one_dispatch(self):
        first, second = self.review(), self.review(body='Different draft')
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.confirm(first), range(6)))
        self.assertEqual(len({r['attempt_id'] for r in results}), 1)
        self.assertEqual(counts(self.store)['fake_dispatches'], 1)
        with self.assertRaises(Conflict):
            self.confirm(second)

    def test_stale_after_note_edit_or_new_incoming_never_dispatches(self):
        for kind in ('note', 'details', 'incoming'):
            reviewed = self.review(body='For ' + kind)
            ticket = self.store.get_ticket(self.tid)
            if kind == 'note':
                self.store.add_note(self.tid, {'revision': ticket['revision'], 'operation_id': 'note', 'body': 'Changed context'})
            elif kind == 'details':
                self.store.update_ticket(self.tid, {'revision': ticket['revision'], 'operation_id': 'details',
                    'status': 'closed', 'priority': 'normal', 'assignee': ''})
            else:
                data = payload([event('follow-up', 'incoming', at='2026-09-02T12:00:00Z')])
                replay.run(self.store, data, replay.run(self.store, data, preview=True)['digest'])
            with self.assertRaises(Conflict):
                self.confirm(reviewed)
        self.assertEqual(counts(self.store)['fake_dispatches'], 0)

    def test_expired_and_cross_ticket_review_are_refused(self):
        reviewed = self.review()
        with patch('intake.delivery.now', return_value=reviewed['expires_at']), self.assertRaises(Conflict):
            self.confirm(reviewed)
        with self.assertRaises(Conflict):
            delivery.confirm(self.store, 'wrong-ticket', {'mode': delivery.MODE,
                'review_id': reviewed['review_id'], 'digest': reviewed['digest'], 'confirmed': True})
        self.assertEqual(counts(self.store)['delivery_attempts'], 0)

    def test_recipient_evidence_rechecked_even_without_revision_change(self):
        reviewed = self.review()
        with self.store.connection(write=True) as db:
            row = db.execute('SELECT message_id,payload_json FROM replay_messages').fetchone()
            raw = json.loads(row['payload_json'])
            raw['source']['from']['address'] = 'different@example.test'
            db.execute('UPDATE replay_messages SET payload_json=? WHERE message_id=?', (canonical(raw), row['message_id']))
        with self.assertRaises(Conflict):
            self.confirm(reviewed)
        self.assertEqual(counts(self.store)['fake_dispatches'], 0)

    def test_accepted_timeout_stays_uncertain_until_explicit_reconciliation(self):
        reviewed = self.review('accepted_timeout')
        result = self.confirm(reviewed)
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(counts(self.store)['messages'], 1)
        self.assertFalse(self.store.get_ticket(self.tid)['reply_context']['available'])
        with self.assertRaises(Conflict):
            self.review(body='A different reply still must wait')
        with patch.object(fake_delivery, 'dispatch', side_effect=AssertionError('Must not dispatch')):
            self.assertEqual(self.confirm(reviewed)['state'], 'uncertain')
            self.assertEqual(self.reconcile(result)['state'], 'simulated_delivered')
            self.reconcile(result)
        self.assertEqual(counts(self.store)['messages'], 2)
        self.assertEqual(counts(self.store)['fake_dispatches'], 1)

    def test_unknown_receipt_blocks_retry_across_restart(self):
        reviewed = self.review('unknown')
        result = self.confirm(reviewed)
        self.store = Store(self.temp.name)
        for _ in range(2):
            self.assertEqual(self.reconcile(result)['state'], 'uncertain')
            self.assertEqual(self.confirm(reviewed)['state'], 'uncertain')
        with self.assertRaises(Conflict):
            self.review('accepted', retry_of=result['attempt_id'])
        self.assertEqual(counts(self.store)['messages'], 1)
        self.assertEqual(counts(self.store)['fake_dispatches'], 1)

    def test_rejection_requires_explicit_new_reviewed_retry(self):
        original = self.review('rejected')
        rejected = self.confirm(original)
        self.assertEqual(rejected['state'], 'failed')
        self.assertEqual(counts(self.store)['messages'], 1)
        self.assertEqual(self.confirm(original)['state'], 'failed')
        with self.assertRaises(Conflict):
            self.review()
        retry = self.review(retry_of=rejected['attempt_id'])
        self.assertEqual(self.confirm(retry)['state'], 'simulated_delivered')
        self.assertEqual(counts(self.store)['fake_dispatches'], 2)
        self.assertEqual(counts(self.store)['messages'], 2)
        with self.assertRaises(Conflict):
            self.review(retry_of=rejected['attempt_id'])

    def test_crash_before_dispatch_cannot_be_assumed_unsent(self):
        reviewed = self.review()
        with patch.object(fake_delivery, 'dispatch', side_effect=sqlite3.OperationalError('interruption')), self.assertRaises(sqlite3.Error):
            self.confirm(reviewed)
        self.store = Store(self.temp.name)
        with patch.object(fake_delivery, 'dispatch', side_effect=AssertionError('No retry')):
            result = self.confirm(reviewed)
            self.assertEqual(result['state'], 'attempting')
            self.assertEqual(self.reconcile(result)['state'], 'uncertain')
        self.assertEqual(counts(self.store)['fake_dispatches'], 0)
        with self.assertRaises(Conflict):
            self.review(body='Cannot bypass unresolved attempt')

    def test_crash_after_acceptance_reconciles_without_duplicate(self):
        reviewed = self.review()
        original_event = self.store.event
        def interrupted(db, tid, kind, detail, actor):
            if kind == 'reply_simulation_simulated_delivered':
                raise sqlite3.OperationalError('interrupted final commit')
            return original_event(db, tid, kind, detail, actor)
        with patch.object(self.store, 'event', side_effect=interrupted), self.assertRaises(sqlite3.Error):
            self.confirm(reviewed)
        self.assertEqual(counts(self.store)['messages'], 1)  # Message + ledger transaction rolled back.
        self.assertEqual(counts(self.store)['fake_dispatches'], 1)  # Fake acceptance survived.
        self.store = Store(self.temp.name)
        result = self.confirm(reviewed)
        self.assertEqual(result['state'], 'attempting')
        self.assertEqual(self.reconcile(result)['state'], 'simulated_delivered')
        self.assertEqual(counts(self.store)['messages'], 2)
        self.assertEqual(counts(self.store)['fake_dispatches'], 1)

    def test_reply_to_simulated_outgoing_threads_to_original_ticket(self):
        result = self.confirm(self.review())
        msg = self.store.get_ticket(self.tid)['messages'][-1]
        parent = json.loads(msg['headers_json'])['Message-ID']
        incoming = event('reply-to-fake', headers={'In-Reply-To': parent}, at='2030-01-01T12:00:00Z')
        data = payload([incoming])
        outcome = replay.run(self.store, data, replay.run(self.store, data, preview=True)['digest'])
        self.assertEqual(outcome['results'][0]['ticket_id'], self.tid)
        self.assertEqual(counts(self.store)['tickets'], 1)
        self.assertEqual(result['message_id'], msg['id'])

    def test_unsupported_targets_and_arbitrary_recipient_override_are_refused(self):
        for candidate in (event('no-header', headers={'Message-ID': ''}),
                          event('automated', headers={'Auto-Submitted': 'auto-replied'}),
                          event('redirect', headers={'Reply-To': 'someone-else@example.test'}),
                          event('multi', source={'from': {'address': 'customer@example.test'},
                                                'to': [{'address': 'support@example.test'}, {'address': 'other@example.test'}]})):
            data = payload([candidate])
            tid = replay.run(self.store, data, replay.run(self.store, data, preview=True)['digest'])['results'][0]['ticket_id']
            self.assertFalse(self.store.get_ticket(tid)['reply_context']['available'])
            with self.assertRaises(Invalid):
                delivery.review(self.store, tid, {'mode': delivery.MODE, 'operation_id': candidate['event_id'],
                    'revision': 1, 'body': 'Test', 'scenario': 'accepted'})
        with self.assertRaises(Invalid):
            self.review(recipient='arbitrary@example.test')
        self.assertEqual(counts(self.store)['fake_dispatches'], 0)

    def test_receipt_with_wrong_fingerprint_cannot_resolve_uncertainty(self):
        result = self.confirm(self.review('accepted_timeout'))
        with self.store.connection(write=True) as db:
            db.execute("UPDATE fake_dispatches SET digest='wrong'")
        with self.assertRaises(Conflict):
            self.reconcile(result)
        self.assertEqual(counts(self.store)['messages'], 1)

    def test_long_reference_chain_remains_threadable_after_simulation(self):
        with self.store.connection(write=True) as db:
            row = db.execute('SELECT message_id,payload_json FROM replay_messages').fetchone()
            raw = json.loads(row['payload_json'])
            raw['headers']['References'] = ' '.join(f'<ancestor-{n}@example.test>' for n in range(100))
            db.execute('UPDATE replay_messages SET payload_json=? WHERE message_id=?', (canonical(raw), row['message_id']))
        reviewed = self.review()
        self.assertEqual(len(reviewed['envelope']['references'].split()), 100)
        self.confirm(reviewed)
        msg = self.store.get_ticket(self.tid)['messages'][-1]
        parent = json.loads(msg['headers_json'])['Message-ID']
        data = payload([event('long-chain-followup', headers={'In-Reply-To': parent})])
        result = replay.run(self.store, data, replay.run(self.store, data, preview=True)['digest'])
        self.assertEqual(result['results'][0]['ticket_id'], self.tid)

    def test_version_two_upgrade_keeps_existing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            schema = Path('intake/schema.sql').read_text().split('CREATE TABLE IF NOT EXISTS delivery_reviews')[0]
            db = sqlite3.connect(Path(directory) / 'intake.sqlite3')
            db.executescript(schema)
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute('PRAGMA user_version=2')
            db.execute("""INSERT INTO tickets(id,subject,status,priority,assignee,channel,origin,created_at,updated_at,status_changed_at)
                          VALUES ('original','Original','closed','normal','','email','offline_replay',
                          '2026-09-01T12:00:00Z','2026-09-02T12:00:00Z','2026-09-02T12:00:00Z')""")
            db.execute("""INSERT INTO messages(id,ticket_id,kind,author_name,author_email,body,created_at,channel,origin)
                          VALUES ('old-message','original','incoming','Test','customer@example.test','Original content',
                          '2026-09-01T12:00:00Z','email','offline_replay')""")
            db.execute('INSERT INTO replay_messages VALUES (?,?,?,?,?,?)',
                       ('old-message','test','support@example.test','simulation','incoming',canonical(event('incoming')['message'])))
            db.commit()
            db.close()
            upgraded = Store(directory)
            ticket = upgraded.get_ticket('original')
            self.assertEqual((ticket['status'],ticket['messages'][0]['body']),('closed','Original content'))
            self.assertEqual(ticket['deliveries'],[])
            self.assertTrue(ticket['reply_context']['available'])
            with upgraded.connection() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)


if __name__ == '__main__':
    unittest.main()
