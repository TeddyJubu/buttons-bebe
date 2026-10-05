import concurrent.futures
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from intake import auth, delivery, import_store, recovery, team
from intake.policy import Conflict, Invalid
from intake.server import LocalServer
from intake.store import Store

PASSWORD = 'Synthetic accounts only 2026!'


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.users = {role: auth.provision(self.store, role, role.title() + ' Test', role, PASSWORD)
                      for role in ('admin', 'agent', 'viewer')}
        self.server = LocalServer(0, self.store, 'test')
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join()))
        self.sessions = {role: self.login(role) for role in self.users}
        self.tid = self.store.create_ticket({'operation_id': 'initial', 'subject': 'Shared test', 'body': 'Initial private note'})['id']

    def request(self, path, data=None, session=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            values = {'Content-Type': 'application/json'}
            if session:
                values.update({'Cookie': session['cookie'], 'X-Intake-Token': session.get('token', '')})
            values.update(headers or {})
            client.request('GET' if data is None else 'POST', path, None if data is None else json.dumps(data), values)
            response = client.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            client.close()

    def login(self, username):
        status, _, headers = self.request('/api/auth/login', {'username': username, 'password': PASSWORD})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        result = {'cookie': headers['Set-Cookie'].split(';')[0]}
        status, body, _ = self.request('/api/session', session=result)
        self.assertEqual(status, 200)
        return {**result, **body}

    def ticket(self, role='agent'):
        status, body, _ = self.request('/api/tickets/' + self.tid, session=self.sessions[role])
        self.assertEqual(status, 200)
        return body

    def post(self, action, data, role='agent'):
        return self.request('/api/tickets/' + self.tid + '/' + action, data, self.sessions[role])

    def read_data(self, ticket, operation='read', unread=False):
        state = ticket['read_state']
        return {'operation_id': operation, 'version': state['version'], 'through_sequence': state['through_sequence'], 'unread': unread}

    def test_all_private_routes_need_cookie_and_per_session_csrf(self):
        for path in ('/api/session', '/api/team', '/api/tickets', '/api/imports', '/api/tickets/' + self.tid):
            self.assertEqual(self.request(path)[0], 401)
        self.assertEqual(self.request('/api/tickets', {'operation_id': 'no', 'subject': 'x', 'body': 'x'})[0], 401)
        self.assertEqual(self.request('/api/tickets', session={'token': self.sessions['admin']['token'], 'cookie': ''})[0], 401)
        self.assertEqual(self.request('/api/tickets', session=self.sessions['admin'], headers={'X-Intake-Token': self.sessions['agent']['token']})[0], 403)
        for headers in ({'Origin': 'https://attacker.test'}, {'Host': 'attacker.test'}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('/api/auth/login', {'username': 'admin', 'password': PASSWORD}, headers=headers)[0], 403)
        for session in self.sessions.values():
            self.assertFalse(any(session['capabilities'][k] for k in ('liveIntake','sendReply','providerReads','aiGeneration','notifications')))
        with self.store.connection() as db:
            stored = ' '.join(row[0] for row in db.execute('SELECT id FROM sessions'))
        for session in self.sessions.values():
            self.assertNotIn(session['cookie'].split('=')[1], stored)

    def test_invalid_password_input_cannot_authenticate_the_dummy_literal(self):
        sentinel = 'invalid-password-input'
        auth.provision(self.store, 'admin', 'Admin Test', 'admin', sentinel)
        with self.store.connection() as db:
            before = db.execute('SELECT count(*) FROM sessions').fetchone()[0]
            saved_hash = db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
        for invalid in ('', 'short', None, True, [], 'x' * 1025):
            with self.subTest(value_type=type(invalid).__name__):
                self.assertFalse(auth.matches(invalid, saved_hash))
                status, _, headers = self.request('/api/auth/login', {'username':'admin','password':invalid})
                self.assertEqual(status, 401)
                self.assertNotIn('Set-Cookie', headers)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], before)
        self.assertTrue(auth.matches(sentinel, saved_hash))
        self.assertEqual(self.request('/api/auth/login', {'username':'admin','password':sentinel})[0], 200)

    def test_password_change_requires_actual_current_password_for_dummy_literal(self):
        sentinel = 'invalid-password-input'
        auth.provision(self.store, 'admin', 'Admin Test', 'admin', sentinel)
        status, _, headers = self.request('/api/auth/login', {'username':'admin','password':sentinel})
        self.assertEqual(status, 200)
        session = {'cookie':headers['Set-Cookie'].split(';')[0]}
        session.update(self.request('/api/session', session=session)[1])
        replacement = 'A different synthetic password'
        with self.store.connection() as db:
            before = db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
        for invalid in ('', 'short', None, True, []):
            self.assertEqual(self.request('/api/auth/password',
                {'password':invalid,'new_password':replacement}, session)[0], 403)
            self.assertEqual(self.request('/api/session',session=session)[0], 200)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0], before)
        self.assertEqual(self.request('/api/auth/password',
            {'password':sentinel,'new_password':replacement}, session)[0], 200)
        self.assertEqual(self.request('/api/session',session=session)[0], 401)
        self.assertEqual(self.request('/api/auth/login',{'username':'admin','password':replacement})[0], 200)

    def test_roles_cannot_be_bypassed_by_direct_calls_or_spoofed_identity(self):
        t = self.ticket('viewer')
        calls = [('notes', {'operation_id': 'spoof', 'revision': t['revision'], 'body': 'x', 'role': 'admin'}),
                 ('update', {'operation_id': 'x', 'revision': 1, 'status': 'closed', 'priority': 'normal'}),
                 ('claim', {'operation_id': 'x', 'revision': 1}),
                 ('simulation-review', {}), ('simulation-confirm', {}), ('simulation-reconcile', {})]
        for action, data in calls:
            self.assertEqual(self.post(action, data, 'viewer')[0], 403)
        for role in ('viewer', 'agent'):
            for path in ('/api/import/preview','/api/import/commit'):
                self.assertEqual(self.request(path, {}, self.sessions[role])[0], 403)
            self.assertEqual(self.request('/api/imports', session=self.sessions[role])[0], 403)
        self.assertEqual(self.post('read-state', self.read_data(t), 'viewer')[0], 200)
        self.assertEqual(self.post('notes', {'operation_id': 'note', 'revision': 1, 'body': 'Verified note', 'actor': 'Forged Admin'})[0], 200)
        saved = self.ticket()
        self.assertEqual(saved['messages'][-1]['author_name'], 'Agent Test')
        event = saved['events'][0]
        self.assertEqual(event['actor'], 'Agent Test')
        self.assertEqual(json.loads(event['detail_json'])['actor_user_id'], self.users['agent']['id'])

    def test_per_user_idempotency_and_unread_watermarks_do_not_leak(self):
        for role in ('admin','agent'):
            response = self.post('notes', {'operation_id': 'same-id', 'revision': self.ticket(role)['revision'], 'body': role + ' note'}, role)
            self.assertEqual(response[0], 200)
        self.assertEqual(len(self.ticket()['messages']), 3)
        snapshot = self.ticket()
        mark = self.read_data(snapshot)
        self.assertEqual(self.post('read-state', mark)[0], 200)
        self.assertEqual(self.post('read-state', mark)[0], 200)
        self.assertFalse(self.ticket()['read_state']['unread'])
        self.assertTrue(self.ticket('admin')['read_state']['unread'])
        self.assertTrue(self.ticket('viewer')['read_state']['unread'])
        again = self.login('agent')
        self.assertFalse(self.request('/api/tickets/'+self.tid, session=again)[1]['read_state']['unread'])
        self.assertEqual(self.post('read-state', self.read_data(snapshot, 'stale'))[0], 409)
        current = self.ticket()
        self.assertEqual(self.post('read-state', self.read_data(current, 'manual-unread', True))[0], 200)
        self.assertTrue(self.ticket()['read_state']['unread'])
        self.assertEqual(self.request('/api/tickets?view=unread', session=self.sessions['agent'])[1]['total'], 1)

    def test_arriving_messages_remain_unread_and_foreign_future_watermarks_fail(self):
        snapshot = self.ticket()
        self.store.add_note(self.tid, {'operation_id': 'arriving', 'revision': snapshot['revision'], 'body': 'Arrived after display'})
        self.assertEqual(self.post('read-state', self.read_data(snapshot))[0], 200)
        self.assertTrue(self.ticket()['read_state']['unread'])
        current = self.ticket()
        self.assertEqual(self.post('read-state', self.read_data(current, 'latest'))[0], 200)
        self.assertFalse(self.ticket()['read_state']['unread'])
        foreign = self.store.create_ticket({'operation_id': 'foreign', 'subject': 'Other', 'body': 'Other'})['id']
        wrong = self.read_data(self.ticket(), 'bad')
        wrong['through_sequence'] = self.store.get_ticket(foreign)['messages'][0]['sequence']
        self.assertEqual(self.post('read-state', wrong)[0], 400)
        wrong['through_sequence'] += 100
        self.assertEqual(self.post('read-state', wrong)[0], 400)
        # Status changes do not create unread messages.
        self.post('update', {'operation_id': 'close', 'revision': self.ticket()['revision'], 'status': 'closed', 'priority': 'normal'})
        self.assertFalse(self.ticket()['read_state']['unread'])

    def test_concurrent_claim_has_one_owner_and_assignment_is_local(self):
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda role: self.post('claim', {'operation_id': 'claim', 'revision': 1}, role), ('admin','agent')))
        self.assertEqual(sorted(r[0] for r in results), [200,409])
        ticket = self.ticket()
        winner = ticket['team_assignee']['role']
        loser = 'agent' if winner == 'admin' else 'admin'
        self.assertEqual(self.request('/api/tickets?view=mine', session=self.sessions[winner])[1]['total'], 1)
        self.assertEqual(self.request('/api/tickets?view=mine', session=self.sessions[loser])[1]['total'], 0)
        self.assertEqual(self.request('/api/tickets?view=unassigned', session=self.sessions['agent'])[1]['total'], 0)
        data = {'operation_id':'assign','revision':ticket['revision'],'status':'waiting_team','priority':'high','assignee_id':self.users[loser]['id']}
        self.assertEqual(self.post('update', data)[0], 200)
        self.assertEqual(self.ticket()['team_assignee']['id'], self.users[loser]['id'])
        self.assertEqual(self.ticket()['assignee'], '')
        for bad in ('made-up', self.users['viewer']['id']):
            data.update(operation_id=bad,revision=self.ticket()['revision'],assignee_id=bad)
            self.assertEqual(self.post('update', data)[0], 400)
        data.update(operation_id='clear',assignee_id='')
        self.assertEqual(self.post('update', data)[0], 200)
        self.assertIsNone(self.ticket()['team_assignee'])

    def test_user_edit_disable_expiry_logout_and_password_reset_revoke_sessions(self):
        auth.provision(self.store, 'agent', 'Agent Test', 'viewer', active=True)
        self.assertEqual(self.request('/api/tickets', session=self.sessions['agent'])[0], 401)
        new = self.login('agent')
        self.assertEqual(self.request('/api/tickets', {'operation_id': 'no'}, new)[0], 403)
        auth.provision(self.store, 'agent', 'Agent Test', 'viewer', active=False)
        self.assertEqual(self.request('/api/tickets', session=new)[0], 401)
        self.assertEqual(self.request('/api/auth/login', {'username':'agent','password':PASSWORD})[0], 401)
        self.assertEqual(self.request('/api/auth/logout', {}, self.sessions['viewer'])[0], 200)
        self.assertEqual(self.request('/api/session', session=self.sessions['viewer'])[0], 401)
        self.assertEqual(self.request('/api/auth/password', {'password': PASSWORD, 'new_password': 'A new synthetic test password'}, self.sessions['admin'])[0], 200)
        self.assertEqual(self.request('/api/session', session=self.sessions['admin'])[0], 401)
        self.assertEqual(self.request('/api/auth/login', {'username':'admin','password':PASSWORD})[0], 401)
        with self.assertRaises(Conflict):
            auth.provision(self.store, 'admin', 'Admin', 'agent', active=True)
        with self.store.connection(write=True) as db:
            db.execute('UPDATE sessions SET expires_at=0')
        self.assertEqual(self.request('/api/tickets', session=self.sessions['agent'])[0], 401)

    def test_session_recheck_inside_write_prevents_revoked_identity_and_reuse_after_restart(self):
        raw = self.sessions['agent']['cookie'].split('=')[1]
        actor = auth.identity(self.store, raw, self.server.audience)
        auth.provision(self.store, 'agent', 'Agent Test', 'viewer')
        with auth.acting(actor, 'work'), self.assertRaises(auth.Unauthorized):
            self.store.add_note(self.tid, {'operation_id': 'revoked', 'revision': 1, 'body': 'Forbidden'})
        with self.assertRaises(auth.Unauthorized):
            auth.identity(self.store, self.sessions['admin']['cookie'].split('=')[1], 'another-server')
        self.assertEqual(len(self.store.get_ticket(self.tid)['messages']),1)

    def test_absolute_session_expiry_and_password_hashes(self):
        with self.store.connection() as db:
            hashes = [row[0] for row in db.execute('SELECT password_hash FROM users')]
        self.assertEqual(len(set(hashes)), 3)
        self.assertTrue(all(PASSWORD not in value for value in hashes))
        self.assertEqual(self.request('/api/tickets', session=self.sessions['agent'])[0], 200)
        with patch('intake.auth.time.time', return_value=time.time()+auth.SESSION_SECONDS+1):
            self.assertEqual(self.request('/api/tickets', session=self.sessions['agent'])[0], 401)
            self.assertEqual(self.post('notes', {'operation_id':'expired','revision':1,'body':'No'})[0], 401)

    def test_failed_logins_are_generic_and_throttled(self):
        for username in ('missing','agent'):
            for _ in range(5):
                status, body, _ = self.request('/api/auth/login', {'username':username,'password':'Wrong synthetic password'})
                self.assertEqual(status,401)
                self.assertEqual(body['error'],'Invalid sandbox username or password.')
            self.assertEqual(self.request('/api/auth/login', {'username':username,'password':PASSWORD})[0],429)
        with patch('intake.auth.time.time', return_value=time.time()+61):
            self.login('agent')

    def test_fake_reviews_require_same_user_and_keep_shared_uncertainty_block(self):
        payload = Path('intake/fixtures/gorgias-synthetic.json').read_bytes()
        plan = import_store.preview(self.store, payload, 'team-test')
        import_store.apply_import(self.store, payload, 'team-test', plan['digest'])
        with self.store.connection() as db:
            self.tid = db.execute("SELECT internal_id FROM source_records WHERE kind='ticket' AND external_id='900001'").fetchone()[0]
        body = {'mode':'offline_simulation','operation_id':'review','revision':1,'body':'Local test reply','scenario':'accepted_timeout'}
        status, reviewed, _ = self.post('simulation-review', body)
        self.assertEqual(status,200)
        confirmation = {'mode':'offline_simulation','review_id':reviewed['review_id'],'digest':reviewed['digest'],'confirmed':True}
        self.assertEqual(self.post('simulation-confirm', confirmation, 'admin')[0],403)
        status, attempt, _ = self.post('simulation-confirm', confirmation)
        self.assertEqual(status,200)
        self.assertEqual(attempt['state'],'uncertain')
        body.update(revision=self.ticket()['revision'],operation_id='other')
        self.assertEqual(self.post('simulation-review',body,'admin')[0],409)
        self.assertEqual(self.post('simulation-reconcile',{'mode':'offline_simulation','attempt_id':attempt['attempt_id'],'confirmed':True},'admin')[0],200)
        self.assertEqual(self.ticket()['messages'][-1]['author_name'],'Agent Test')
        self.assertEqual(self.post('simulation-confirm',confirmation,'admin')[0],403)

    def test_recovery_preserves_team_state_but_revokes_sessions_and_pending_reviews(self):
        self.post('claim', {'operation_id':'claim','revision':1})
        self.post('read-state', self.read_data(self.ticket()))
        before = self.ticket()
        checkpoint = recovery.backup(self.store, 'team-proof')
        with tempfile.TemporaryDirectory() as directory, patch('intake.recovery.ROOT', Path(directory)):
            result = recovery.restore(self.store.path.parent/'backups/team-proof','restored',checkpoint['digest'])
            restored = Store(recovery.workspace_path('restored'))
            with restored.connection() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],3)
                self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT user_id FROM ticket_assignments').fetchone()[0],self.users['agent']['id'])
                self.assertEqual(db.execute('SELECT seen_sequence FROM ticket_reads').fetchone()[0],before['read_state']['through_sequence'])
            raw = auth.login(restored,'agent',PASSWORD,'new-process')
            self.assertEqual(auth.identity(restored,raw,'new-process')['id'],self.users['agent']['id'])
            self.assertTrue(result['sessions_revoked'])

    def test_v4_migration_keeps_historical_rows_and_has_no_default_account(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'intake.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript(Path('intake/schema-v4.sql').read_text())
                db.execute('PRAGMA application_id=0x4242494E')
                db.execute('PRAGMA user_version=4')
                db.execute("INSERT INTO sandbox_meta VALUES ('mode','offline_sandbox')")
            from intake.integrity import check_database
            self.assertEqual(check_database(path)['tables']['tickets'],0)
            store = Store(directory)
            with store.connection() as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7)
                self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],0)
            self.assertEqual(check_database(path)['tables']['users'],0)


if __name__ == '__main__':
    unittest.main()
