import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from intake import import_store, replay
from intake.policy import Conflict, Invalid
from intake.records import canonical
from intake.store import APPLICATION_ID, Store


def event(name, parent=None, *, sender='customer@example.test', mailbox='support@example.test',
          at='2026-09-01T12:00:00Z', subject='Same subject', headers=None, **fields):
    mail_headers = {'Message-ID': f'<{name}@example.test>'}
    if parent:
        mail_headers['In-Reply-To'] = f'<{parent}@example.test>'
    if headers is not None:
        mail_headers.update(headers)
    message = {'id': name, 'channel': 'email', 'public': True, 'from_agent': False,
               'created_datetime': at, 'sender': {'name': 'Test Customer', 'email': sender},
               'source': {'from': {'address': sender}, 'to': [{'address': mailbox}]},
               'body_text': 'Synthetic message ' + name, 'headers': mail_headers, 'attachments': []}
    message.update(fields)
    return {'event_id': 'event-' + name, 'provider': 'simulation', 'subject': subject, 'message': message}


def payload(events, account='test', mailbox='support@example.test'):
    return canonical({'mode': 'offline_replay', 'account': account, 'mailbox': mailbox, 'events': events}).encode()


def counts(store):
    with store.connection() as db:
        return {r[0]: db.execute('SELECT count(*) FROM ' + r[0]).fetchone()[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def run_events(self, events, **kwargs):
        data = payload(events, **kwargs)
        report = replay.run(self.store, data, preview=True)
        return replay.run(self.store, data, report['digest'])

    def close(self, tid, status='closed', at='2026-09-02T12:00:00Z', priority='normal'):
        ticket = self.store.get_ticket(tid)
        with patch('intake.store.now', return_value=at):
            return self.store.update_ticket(tid, {'operation_id': str(ticket['revision']) + tid,
                'revision': ticket['revision'], 'status': status, 'priority': priority, 'assignee': ''})

    def test_preview_rollback_and_same_subject_separate_tickets(self):
        data = payload([event('root1'), event('root2')])
        before = counts(self.store)
        report = replay.run(self.store, data, preview=True)
        self.assertEqual(report['outcomes'], {'created': 2})
        self.assertEqual(counts(self.store), before)
        committed = replay.run(self.store, data, report['digest'])
        self.assertEqual(len({r['ticket_id'] for r in committed['results']}), 2)
        self.assertEqual(counts(self.store)['messages'], 2)
        with self.assertRaises(Conflict):
            replay.run(self.store, data, 'wrong-digest')

    def test_headers_thread_despite_changed_subject_and_header_case(self):
        first = self.run_events([event('root')])['results'][0]
        reply = event('reply', subject='Completely changed subject', headers={'references': '<root@example.test>'})
        second = self.run_events([reply])['results'][0]
        self.assertEqual(second['ticket_id'], first['ticket_id'])
        self.assertEqual(second['outcome'], 'appended')
        self.assertEqual(counts(self.store)['tickets'], 1)

    def test_restart_repeat_and_parallel_delivery_are_idempotent(self):
        data = payload([event('root'), event('reply', 'root')])
        fingerprint = replay.run(self.store, data, preview=True)['digest']
        with ThreadPoolExecutor(max_workers=5) as pool:
            reports = list(pool.map(lambda _: replay.run(self.store, data, fingerprint), range(5)))
        self.assertEqual(sum(r['outcomes'].get('created', 0) for r in reports), 1)
        self.assertEqual(counts(self.store)['messages'], 2)
        self.store = Store(self.temp.name)
        self.assertEqual(replay.run(self.store, data, fingerprint)['outcomes'], {'duplicate_event': 2})

    def test_rfc_and_transport_dedupe_across_event_ids_persists_alias(self):
        original = event('root')
        first = self.run_events([original])['results'][0]
        duplicate = copy.deepcopy(original)
        duplicate['event_id'] = 'another-event'
        duplicate['provider'] = 'other-provider'
        duplicate['message']['id'] = 'other-transport-id'
        second = self.run_events([duplicate])['results'][0]
        self.assertEqual(second['outcome'], 'duplicate_message')
        self.assertEqual(second['message_id'], first['message_id'])
        self.store = Store(self.temp.name)
        duplicate['event_id'] = 'third-event'
        self.assertEqual(self.run_events([duplicate])['outcomes'], {'duplicate_message': 1})
        duplicate['message']['headers'] = {}
        with self.assertRaises(Conflict):
            self.run_events([duplicate])
        self.assertEqual(counts(self.store)['messages'], 1)

    def test_reused_event_or_message_id_changed_content_rolls_back_whole_batch(self):
        original = event('root')
        self.run_events([original])
        for change_event in (False, True):
            altered = copy.deepcopy(original)
            altered['message']['body_text'] = 'Altered content'
            if change_event:
                altered['event_id'] = 'new-event'
            before = counts(self.store)
            with self.assertRaises(Conflict):
                # Bypass preview to prove commit itself rolls back a preceding write.
                data = payload([event('other'), altered])
                replay.run(self.store, data, replay.prepare(data)[3])
            self.assertEqual(counts(self.store), before)

    def test_failed_storage_rolls_back_receipts_and_retry_succeeds(self):
        data = payload([event('root'), event('reply', 'root')])
        before = counts(self.store)
        real_event = self.store.event
        def fail(db, tid, kind, detail, actor):
            if detail['outcome'] == 'appended':
                raise sqlite3.OperationalError('injected failure')
            return real_event(db, tid, kind, detail, actor)
        with patch.object(self.store, 'event', side_effect=fail), self.assertRaises(sqlite3.OperationalError):
            replay.run(self.store, data, replay.prepare(data)[3])
        self.assertEqual(counts(self.store), before)
        self.assertEqual(self.run_events([event('root'), event('reply', 'root')])['events'], 2)

    def test_unknown_missing_ambiguous_and_malformed_headers_are_reviewable(self):
        self.run_events([event('root1'), event('root2')])
        cases = [('unknown', {'In-Reply-To': '<absent@example.test>'}, 'unknown_reference'),
                 ('missing', {'Message-ID': ''}, 'missing_message_id'),
                 ('ambiguous', {'References': '<root1@example.test> <root2@example.test>'}, 'ambiguous_references'),
                 ('bad', {'Message-ID': '<bad value>'}, 'invalid_headers'),
                 ('case-duplicate', {'message-id': '<different@example.test>'}, 'invalid_headers')]
        for name, headers, expected in cases:
            with self.subTest(name=name):
                result = self.run_events([event(name, headers=headers)])['results'][0]
                self.assertEqual((result['queue'], result['reason']), ('review', expected))
        self.assertEqual(self.store.list_tickets(queue='review')['total'], 5)

    def test_account_mailbox_and_participant_are_boundaries(self):
        self.run_events([event('root')])
        cases = [(event('other-account', 'root'), {'account': 'other'}, 'unknown_reference'),
                 (event('other-box', 'root', mailbox='another@example.test'), {'mailbox': 'another@example.test'}, 'unknown_reference'),
                 (event('stranger', 'root', sender='stranger@example.test'), {}, 'participant_mismatch')]
        for candidate, settings, reason in cases:
            result = self.run_events([candidate], **settings)['results'][0]
            self.assertEqual((result['queue'], result['reason']), ('review', reason))

    def test_genuine_new_replies_reopen_closed_waiting_and_snoozed(self):
        for status in ('closed', 'waiting_customer', 'waiting_team', 'snoozed'):
            root = 'root-' + status
            tid = self.run_events([event(root)])['results'][0]['ticket_id']
            self.close(tid, status)
            result = self.run_events([event('reply-' + status, root, at='2026-09-03T12:00:00Z')])
            self.assertEqual(result['reopened'], 1)
            self.assertEqual(self.store.get_ticket(tid)['status'], 'open')

    def test_delayed_messages_do_not_reopen_or_regress_timestamp(self):
        tid = self.run_events([event('root')])['results'][0]['ticket_id']
        self.close(tid)
        closed = self.store.get_ticket(tid)
        report = self.run_events([event('delayed', 'root', at='2026-09-01T12:00:00.000001Z')])
        self.assertEqual(report['reopened'], 0)
        ticket = self.store.get_ticket(tid)
        self.assertEqual((ticket['status'], ticket['updated_at']), ('closed', closed['updated_at']))
        self.assertEqual(len(ticket['messages']), 2)
        self.close(tid, at='2026-09-04T12:00:00Z', priority='high')
        # Priority-only edit must not postpone the actual status-change barrier.
        self.assertEqual(self.run_events([event('fresh', 'root', at='2026-09-03T12:00:00Z')])['reopened'], 1)

    def test_out_of_order_missing_parent_is_not_later_merged(self):
        results = self.run_events([event('child', 'parent'), event('parent')])['results']
        self.assertEqual(results[0]['queue'], 'review')
        self.assertNotEqual(results[0]['ticket_id'], results[1]['ticket_id'])

    def test_submillisecond_late_delivery_is_ordered_and_cannot_reopen(self):
        tid = self.run_events([event('root', at='2026-09-03T12:00:00Z'),
            event('newer', 'root', at='2026-09-03T12:00:00.000009Z')])['results'][0]['ticket_id']
        # Future-dated source messages can exceed the operator's local clock.
        self.close(tid, at='2026-09-02T12:00:00Z')
        result = self.run_events([event('delayed', 'root', at='2026-09-03T12:00:00.000005Z')])
        self.assertEqual(result['reopened'], 0)
        self.assertEqual([m['body'] for m in self.store.get_ticket(tid)['messages']],
                         ['Synthetic message root', 'Synthetic message delayed', 'Synthetic message newer'])

    def test_spam_automatic_responses_and_echoes_do_not_reopen(self):
        tid = self.run_events([event('root')])['results'][0]['ticket_id']
        self.close(tid)
        spam = event('spam', 'root', at='2026-09-03T12:00:00Z')
        spam['spam'] = True
        report = self.run_events([spam, event('auto', 'root', at='2026-09-03T12:00:00Z', headers={'Auto-Submitted': 'auto-replied'}),
            event('unmatched-auto', headers={'Auto-Submitted': 'auto-generated'}),
            event('bounce', headers={'Content-Type': 'multipart/report; report-type=delivery-status'}),
            event('echo', 'root', from_agent=True), event('note', 'root', public=False)])
        self.assertEqual(report['reopened'], 0)
        self.assertEqual(report['outcomes'], {'created': 3, 'appended': 1, 'ignored': 2})
        self.assertEqual(self.store.get_ticket(tid)['status'], 'closed')
        self.assertEqual(self.store.list_tickets()['total'], 1)
        self.assertEqual(self.store.list_tickets(queue='spam')['total'], 1)
        self.assertEqual(self.store.list_tickets(queue='automatic')['total'], 2)
        self.assertEqual(len(self.store.get_ticket(tid)['messages']), 2)

    def test_replay_retains_raw_and_attachments_without_fetching(self):
        candidate = event('root', attachments=[{'url': 'https://never-fetch.example.test/file', 'name': 'test.png'}])
        tid = self.run_events([candidate])['results'][0]['ticket_id']
        with self.store.connection() as db:
            saved = json.loads(db.execute('SELECT payload_json FROM replay_receipts').fetchone()[0])
        self.assertEqual(saved, candidate)
        self.assertEqual(self.store.get_ticket(tid)['messages'][0]['attachments'], candidate['message']['attachments'])

    def test_bad_envelope_and_late_invalid_event_leave_no_writes(self):
        baseline = counts(self.store)
        for data in (b'{}', b'{"mode":"offline_replay","mode":"offline_replay"}', b'[]',
                     payload([event('root'), event('bad', public='true')]),
                     payload([event('wrong', mailbox='wrong@example.test')]),
                     payload([event('chat', channel='chat')])):
            with self.assertRaises(Invalid):
                replay.run(self.store, data, preview=True)
            self.assertEqual(counts(self.store), baseline)

    def test_imported_outgoing_header_threads_customer_and_history_dedupes(self):
        incoming, outgoing = event('history-in')['message'], event('history-out', 'history-in', from_agent=True)['message']
        outgoing['source'] = {'from': {'address': 'support@example.test'}, 'to': [{'address': 'customer@example.test'}]}
        outgoing['sender'] = {'name': 'Agent', 'email': 'support@example.test'}
        source = {'id': 123, 'subject': 'Imported thread', 'status': 'closed', 'created_datetime': '2026-09-01T12:00:00Z',
                  'updated_datetime': '2026-09-02T12:00:00Z', 'messages_count': 2, 'messages': [incoming, outgoing]}
        data = canonical([source]).encode()
        digest = import_store.preview(self.store, data, 'test')['digest']
        import_store.apply_import(self.store, data, 'test', digest)
        historical = event('history-in')
        historical['provider'] = 'gorgias'
        self.assertEqual(self.run_events([historical])['outcomes'], {'duplicate_message': 1})
        result = self.run_events([event('follow-up', 'history-out', at='2026-09-03T12:00:00Z')])
        self.assertEqual(result['outcomes'], {'appended': 1})
        self.assertEqual(result['reopened'], 1)
        self.assertEqual(counts(self.store)['tickets'], 1)

    def test_version_one_migration_preserves_records(self):
        with tempfile.TemporaryDirectory() as directory:
            schema = Path('intake/schema.sql').read_text().split('CREATE TABLE IF NOT EXISTS replay_receipts')[0]
            schema = schema.replace(",\n    queue TEXT NOT NULL DEFAULT 'inbox' CHECK(queue IN ('inbox','review','spam','automatic')),\n    review_reason TEXT NOT NULL DEFAULT '', status_changed_at TEXT NOT NULL DEFAULT ''", '')
            db = sqlite3.connect(Path(directory) / 'intake.sqlite3')
            db.executescript(schema)
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute('PRAGMA user_version=1')
            db.execute("""INSERT INTO tickets(id,subject,status,priority,assignee,channel,origin,created_at,updated_at)
                        VALUES ('original','Original','closed','normal','','email','gorgias_export',
                                '2026-09-01T12:00:00Z','2026-09-02T12:00:00Z')""")
            db.commit()
            db.close()
            store = Store(directory)
            ticket = store.get_ticket('original')
            self.assertEqual((ticket['status'], ticket['queue'], ticket['revision']), ('closed', 'inbox', 1))
            self.assertEqual(ticket['status_changed_at'], ticket['updated_at'])
            with store.connection() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 7)


if __name__ == '__main__':
    unittest.main()
