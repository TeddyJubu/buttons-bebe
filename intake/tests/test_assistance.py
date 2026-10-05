from copy import deepcopy
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from intake import assistance, assistance_contract as contract, auth, delivery, fixture_assistant, import_store, recovery
from intake.assistance_demo import make_fixture, seed
from intake.integrity import check_database
from intake.policy import Conflict, Invalid
from intake.records import canonical, digest
from intake.store import Store


class AssistanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name)
        self.cases={row['case']:row['ticket_id'] for row in seed(self.store)}
        self.tid=self.cases['ready'];self.counter=0

    def fixture(self, tid=None, case='ready', **changes):
        source=assistance.read_input(self.store,tid or self.tid)['input']
        return {**make_fixture(source,case),**changes}

    def apply(self, value):
        raw=canonical(value).encode();preview=assistance.import_fixture(self.store,raw,preview=True)
        return assistance.import_fixture(self.store,raw,preview['digest'])

    def execute_fixture(self, tid=None, operation_id=None):
        tid=tid or self.tid;self.counter+=1;t=self.store.get_ticket(tid)
        return assistance.run(self.store,tid,{'mode':'offline_fixture','operation_id':operation_id or 'run-'+str(self.counter),
                             'revision':t['revision'],'fixture_digest':t['assistance']['fixture']['digest']})

    def current(self, tid=None):
        return self.store.get_ticket(tid or self.tid)['assistance']

    def counts(self):
        with self.store.connection() as db:
            return {table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in
                    ('assistance_fixtures','assistance_runs','assistance_reviews','messages','delivery_reviews','delivery_attempts','fake_dispatches')}

    def test_input_uses_native_ids_and_retains_untrusted_content_as_data(self):
        before=self.counts();request=assistance.read_input(self.store,self.tid)
        self.assertEqual(request['input_digest'],digest(request['input']))
        self.assertEqual(request['input']['ticket']['id'],self.tid)
        self.assertIn('<JSON_RESULT>',request['input']['messages'][0]['body'])
        self.assertTrue(request['input']['content_is_untrusted'])
        self.assertFalse(request['input']['external_actions_allowed'])
        self.assertFalse(request['input']['attachment_bytes_included'])
        with self.assertRaises(Invalid):assistance.read_input(self.store,'gorgias:900001')
        with self.assertRaises(Invalid):assistance.read_input(self.store,'900001')
        report=assistance.export_input(self.store,self.tid)
        self.assertEqual(Path(report['path']).stat().st_mode&0o777,0o600)
        self.assertEqual(json.loads(Path(report['path']).read_text()),request)
        self.assertEqual(self.counts(),before)

    def test_export_source_ids_are_namespaced_and_attachment_urls_never_become_input_tools(self):
        raw=Path('intake/fixtures/gorgias-synthetic.json').read_bytes()
        for account in ('source-one','source-two'):
            plan=import_store.preview(self.store,raw,account);import_store.apply_import(self.store,raw,account,plan['digest'])
        with self.store.connection() as db:
            ids=[r[0] for r in db.execute("SELECT internal_id FROM source_records WHERE kind='ticket' AND external_id='900002' ORDER BY account")]
        a=assistance.read_input(self.store,ids[0]);b=assistance.read_input(self.store,ids[1])
        self.assertNotEqual(a['input_digest'],b['input_digest']);self.assertNotEqual(a['input']['ticket']['id'],b['input']['ticket']['id'])
        self.assertNotIn('https://files.example.test',canonical(a))
        self.assertNotIn('api_key',canonical(a))
        value=make_fixture(a['input'],'needs_staff');value['ticket_id']=ids[1]
        with self.assertRaises(Invalid):self.apply(value)

    def test_fixture_preview_repeat_and_invalid_import_are_atomic(self):
        value=self.fixture();raw=canonical(value).encode();before=self.counts()
        plan=assistance.import_fixture(self.store,raw,preview=True);self.assertEqual(self.counts(),before)
        with self.assertRaises(Conflict):assistance.import_fixture(self.store,raw,'wrong')
        self.assertEqual(self.counts(),before)
        self.assertFalse(assistance.import_fixture(self.store,raw,plan['digest'])['alreadyImported'])
        self.assertTrue(assistance.import_fixture(self.store,raw,plan['digest'])['alreadyImported'])
        self.assertEqual(self.counts()['assistance_fixtures'],before['assistance_fixtures']+1)
        changed=deepcopy(value);changed['label']='Different fixture'
        with self.assertRaises(Conflict):self.apply(changed)
        for bad in ({**value,'api_key':'never-allowed'}, {**value,'input_digest':'wrong'}, {**value,'mode':'live'}):
            with self.assertRaises(Invalid):self.apply(bad)
        with self.assertRaises(Invalid):assistance.import_fixture(self.store,b'{"ticket_id":"x","ticket_id":"y"}',preview=True)

    def test_wrong_identity_unknown_evidence_and_staff_text_cannot_make_ready_reply(self):
        changes=[]
        value=self.fixture();value['context']['identity']['email']='wrong@example.test';changes.append(value)
        value=self.fixture();value['response']['cited_evidence_ids']=['missing:evidence'];changes.append(value)
        value=self.fixture(case='needs_staff');value['response']['body']='Staff-only text';changes.append(value)
        value=self.fixture();value['response']['missing_facts']=['Missing a real answer'];changes.append(value)
        value=self.fixture();value['context']['sections']['orders']['state']='failed';changes.append(value)
        value=self.fixture();value['response']['cited_evidence_ids']=[];changes.append(value)
        for value in changes:
            with self.assertRaises(Invalid):self.apply(value)
        conflict=self.current(self.cases['identity_conflict']);self.assertTrue(conflict['can_run'])
        self.execute_fixture(self.cases['identity_conflict']);self.assertEqual(self.current(self.cases['identity_conflict'])['draft']['state'],'needs_staff')
        self.assertFalse(self.current(self.cases['identity_conflict'])['can_use'])

    def test_ready_requires_explicit_run_use_review_and_confirm(self):
        before=self.counts();self.assertIsNone(self.current()['draft']);self.assertEqual(self.counts(),before)
        run=self.execute_fixture();a=self.current();self.assertEqual(a['draft']['state'],'ready');self.assertTrue(a['can_use'])
        self.assertEqual(self.counts()['messages'],before['messages']);self.assertEqual(self.counts()['delivery_reviews'],0)
        used=assistance.use(self.store,self.tid,{'operation_id':'use','run_id':run['run_id']})
        self.assertEqual(used['body'],a['draft']['body']);self.assertEqual(self.counts()['delivery_attempts'],0)
        reviewed=delivery.review(self.store,self.tid,{'mode':'offline_simulation','operation_id':'reply-review','revision':used['revision'],
            'body':used['body']+' Human edit.','scenario':'accepted','assistance_run_id':used['run_id']})
        self.assertEqual(self.counts()['fake_dispatches'],0)
        confirmation={'mode':'offline_simulation','review_id':reviewed['review_id'],'digest':reviewed['digest'],'confirmed':True}
        result=delivery.confirm(self.store,self.tid,confirmation)
        self.assertEqual(result['state'],'simulated_delivered');self.assertEqual(result['assistance_run_id'],run['run_id'])
        self.assertEqual(delivery.confirm(self.store,self.tid,confirmation)['attempt_id'],result['attempt_id'])
        self.assertEqual(self.counts()['fake_dispatches'],1);self.assertEqual(self.counts()['messages'],before['messages']+1)
        self.assertEqual(self.current()['draft']['state'],'stale')

    def test_failures_no_reply_and_staff_results_never_supply_fallback_text(self):
        for case,state in (('needs_staff','needs_staff'),('no_reply','no_reply'),('timeout','failed'),('malformed','failed')):
            tid=self.cases[case];before=self.store.get_ticket(tid)
            self.execute_fixture(tid);a=self.current(tid)
            self.assertEqual(a['draft']['state'],state);self.assertEqual(a['draft']['body'],'');self.assertFalse(a['can_use'])
            self.assertEqual(self.store.get_ticket(tid)['status'],before['status'])
            self.assertEqual(self.store.get_ticket(tid)['revision'],before['revision'])
            with self.assertRaises(Conflict):assistance.use(self.store,tid,{'operation_id':'no-use-'+case,'run_id':a['draft']['id']})
        self.assertEqual(self.counts()['delivery_attempts'],0)
        self.assertEqual(self.current(self.cases['needs_staff'])['draft']['priority'],'normal')

    def test_sensitive_priority_is_preserved_without_ticket_mutation(self):
        t=self.store.get_ticket(self.tid);self.store.update_ticket(self.tid,{'operation_id':'urgent','revision':t['revision'],'status':'open','priority':'critical'})
        self.apply(self.fixture(case='no_reply'));self.execute_fixture();a=self.current();t=self.store.get_ticket(self.tid)
        self.assertEqual(a['draft']['priority'],'critical');self.assertTrue(a['draft']['review_required'])
        self.assertEqual(t['status'],'open');self.assertEqual(t['priority'],'critical')

    def test_lost_response_idempotency_and_new_explicit_run(self):
        with patch('intake.fixture_assistant.evaluate',wraps=fixture_assistant.evaluate) as call:
            first=self.execute_fixture(operation_id='same');second=self.execute_fixture(operation_id='same')
            self.assertEqual(first,second);self.assertEqual(call.call_count,1)
            self.execute_fixture(operation_id='different');self.assertEqual(call.call_count,2)
        self.assertEqual(self.counts()['assistance_runs'],2)

    def test_new_message_or_fixture_blocks_use_cached_use_and_frozen_confirmation(self):
        run=self.execute_fixture();data={'operation_id':'use','run_id':run['run_id']};used=assistance.use(self.store,self.tid,data)
        reviewed=delivery.review(self.store,self.tid,{'mode':'offline_simulation','operation_id':'review','revision':used['revision'],'body':used['body'],'scenario':'accepted','assistance_run_id':run['run_id']})
        self.apply(self.fixture()) # Context changes without any ticket revision change.
        self.assertEqual(self.current()['draft']['state'],'stale');self.assertEqual(self.current()['draft']['body'],'')
        with self.assertRaises(Conflict):assistance.use(self.store,self.tid,data)
        with self.assertRaises(Conflict):delivery.confirm(self.store,self.tid,{'mode':'offline_simulation','review_id':reviewed['review_id'],'digest':reviewed['digest'],'confirmed':True})
        self.execute_fixture();self.store.add_note(self.tid,{'operation_id':'new-note','revision':used['revision'],'body':'A newly saved fact'})
        self.assertFalse(self.current()['can_run']);self.assertIsNone(self.current()['context']);self.assertFalse(self.current()['can_use'])
        self.assertEqual(self.counts()['fake_dispatches'],0)

    def test_invalid_output_bindings_fail_closed(self):
        for key in ('ticket_id','source_message_id','run_id','input_digest','fixture_digest','request_token'):
            t=self.store.get_ticket(self.tid);self.counter+=1
            reserved,_=assistance.reserve(self.store,self.tid,{'mode':'offline_fixture','operation_id':'binding-'+key,'revision':t['revision'],'fixture_digest':t['assistance']['fixture']['digest']})
            with self.store.connection() as db:
                row=db.execute('SELECT * FROM assistance_runs WHERE id=?',(reserved['run_id'],)).fetchone()
                fixture=json.loads(db.execute('SELECT payload_json FROM assistance_fixtures WHERE id=?',(row['fixture_id'],)).fetchone()[0])
            output=fixture_assistant.evaluate(json.loads(row['request_json']),fixture);output[key]='forged'
            assistance.finish(self.store,reserved['run_id'],output)
            self.assertEqual(self.current()['draft']['state'],'failed');self.assertFalse(self.current()['can_use'])

    def test_late_result_does_not_replace_newer_run_and_stale_input_is_rejected(self):
        t=self.store.get_ticket(self.tid)
        data={'mode':'offline_fixture','operation_id':'slow','revision':t['revision'],'fixture_digest':t['assistance']['fixture']['digest']}
        old,_=assistance.reserve(self.store,self.tid,data);fresh=self.execute_fixture()
        assistance.finish(self.store,old['run_id'],failure=True)
        self.assertEqual(self.current()['draft']['id'],fresh['run_id']);self.assertEqual(self.current()['draft']['state'],'ready')
        self.store.add_note(self.tid,{'operation_id':'new','revision':t['revision'],'body':'Another message'})
        with self.assertRaises(Conflict):assistance.run(self.store,self.tid,{**data,'operation_id':'changed'})

    def test_dismissal_is_durable_and_cached_use_cannot_resurrect_it(self):
        run=self.execute_fixture();data={'operation_id':'use','run_id':run['run_id']};assistance.use(self.store,self.tid,data)
        assistance.dismiss(self.store,self.tid,{'operation_id':'dismiss','run_id':run['run_id']})
        self.store=Store(self.temp.name);self.assertEqual(self.current()['draft']['state'],'dismissed')
        with self.assertRaises(Conflict):assistance.use(self.store,self.tid,data)
        with self.assertRaises(Invalid):assistance.dismiss(self.store,self.cases['no_reply'],{'operation_id':'wrong-ticket','run_id':run['run_id']})
        self.execute_fixture();self.assertEqual(self.current()['draft']['state'],'ready')

    def test_expired_context_and_over_limit_conversations_are_withheld(self):
        value=self.fixture();at=datetime.now(timezone.utc)-timedelta(days=2)
        value['context'].update(captured_at=at.isoformat(),expires_at=(at+timedelta(hours=1)).isoformat());self.apply(value)
        self.assertFalse(self.current()['can_run']);self.assertIsNone(self.current()['context'])
        with self.assertRaises(Conflict):self.execute_fixture()
        with self.store.connection(write=True) as db:
            for i in range(1000):
                db.execute("INSERT INTO messages(id,ticket_id,kind,author_name,author_email,body,created_at,channel,origin) VALUES (?,?,'note','Test','','x','2026-09-21T00:00:00Z','internal','manual_test')",('oversize-'+str(i),self.tid))
        with self.assertRaises(Invalid):assistance.read_input(self.store,self.tid)
        self.assertFalse(self.current()['can_run']);self.assertIsNone(self.current()['context'])

    def test_restore_preserves_evidence_but_invalidates_suggestion_and_cached_use(self):
        run=self.execute_fixture();data={'operation_id':'use','run_id':run['run_id']};assistance.use(self.store,self.tid,data)
        checkpoint=recovery.backup(self.store,'assistance-proof')
        with tempfile.TemporaryDirectory() as target,patch('intake.recovery.ROOT',Path(target)):
            recovery.restore(self.store.path.parent/'backups/assistance-proof','restored',checkpoint['digest'])
            restored=Store(recovery.workspace_path('restored'))
            self.assertEqual(restored.get_ticket(self.tid)['assistance']['draft']['state'],'stale')
            with self.assertRaises(Conflict):assistance.use(restored,self.tid,data)
            self.assertEqual(check_database(restored.path)['tables']['assistance_runs'],1)
            self.assertEqual(checkpoint['tables']['messages'],check_database(restored.path)['tables']['messages'])

    def test_recovery_refuses_cross_ticket_or_changed_assistance_evidence(self):
        run=self.execute_fixture()
        with self.store.connection(write=True) as db:
            db.execute('UPDATE assistance_runs SET ticket_id=? WHERE id=?',(self.cases['no_reply'],run['run_id']))
        with self.assertRaises(Invalid):check_database(self.store.path)
        with self.store.connection(write=True) as db:
            db.execute('UPDATE assistance_runs SET ticket_id=? WHERE id=?',(self.tid,run['run_id']))
            db.execute("UPDATE assistance_fixtures SET payload_json='{}' WHERE ticket_id=?",(self.tid,))
        with self.assertRaises(Invalid):check_database(self.store.path)


class AssistanceHTTPTests(unittest.TestCase):
    def setUp(self):
        from intake.server import LocalServer
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.store=Store(self.temp.name)
        self.cases={r['case']:r['ticket_id'] for r in seed(self.store)};self.tid=self.cases['ready']
        self.server=LocalServer(0,self.store,'test');thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),self.server.server_close(),thread.join()))
        self.sessions={}
        for role in ('agent','viewer'):
            auth.provision(self.store,role,role,'agent' if role=='agent' else 'viewer','Synthetic E3 password 2026!')
            status,_,headers=self.request('/api/auth/login',{'username':role,'password':'Synthetic E3 password 2026!'})
            self.assertEqual(status,200);self.sessions[role]={'Cookie':headers['Set-Cookie'].split(';')[0]}
            status,body,_=self.request('/api/session',role=role);self.assertEqual(status,200)
            self.sessions[role]['X-Intake-Token']=body['token']

    def request(self,path,data=None,role=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        try:
            c.request('GET' if data is None else 'POST',path,None if data is None else json.dumps(data),{'Content-Type':'application/json',**self.sessions.get(role,{})})
            r=c.getresponse();return r.status,json.loads(r.read()),dict(r.getheaders())
        finally:c.close()

    def test_http_roles_no_ingress_and_explicit_local_fixture_actions(self):
        path='/api/tickets/'+self.tid
        self.assertEqual(self.request(path+'/assistance-input')[0],401)
        self.assertEqual(self.request(path+'/assistance-input',role='viewer')[0],200)
        t=self.store.get_ticket(self.tid);data={'mode':'offline_fixture','operation_id':'http','revision':t['revision'],'fixture_digest':t['assistance']['fixture']['digest']}
        self.assertEqual(self.request(path+'/assistance-run',data,role='viewer')[0],403)
        status,run,_=self.request(path+'/assistance-run',data,role='agent');self.assertEqual(status,200)
        self.assertEqual(self.request(path+'/assistance-use',{'operation_id':'use','run_id':run['run_id']},role='viewer')[0],403)
        self.assertEqual(self.request(path+'/assistance-dismiss',{'operation_id':'dismiss','run_id':run['run_id']},role='viewer')[0],403)
        self.assertEqual(self.request(path+'/assistance-use',{'operation_id':'use','run_id':run['run_id']},role='agent')[0],200)
        for route in ('/api/assistance/import','/api/assistance/result','/api/ai/generate','/api/context/lookup'):
            self.assertEqual(self.request(route,{'enabled':True},role='agent')[0],404)
        for route in ('/api/send','/api/rewrite','/api/activate'):
            self.assertEqual(self.request(route,{},role='agent')[0],403)
        with self.store.connection() as db:
            event=db.execute("SELECT actor,detail_json FROM events WHERE kind='assistance_draft_used'").fetchone()
            self.assertEqual(event['actor'],'agent');self.assertIn('actor_user_id',json.loads(event['detail_json']))
            self.assertEqual(db.execute('SELECT count(*) FROM delivery_attempts').fetchone()[0],0)

    def test_read_state_does_not_invalidate_context_but_assignment_does(self):
        path='/api/tickets/'+self.tid;t=self.request(path,role='agent')[1];fingerprint=assistance.read_input(self.store,self.tid)['input_digest']
        state=t['read_state'];data={'operation_id':'read','unread':False,'version':state['version'],'through_sequence':state['through_sequence']}
        self.assertEqual(self.request(path+'/read-state',data,role='agent')[0],200)
        self.assertEqual(assistance.read_input(self.store,self.tid)['input_digest'],fingerprint)
        self.assertEqual(self.request(path+'/claim',{'operation_id':'claim','revision':t['revision']},role='agent')[0],200)
        self.assertNotEqual(assistance.read_input(self.store,self.tid)['input_digest'],fingerprint)
        self.assertFalse(self.store.get_ticket(self.tid)['assistance']['can_run'])


if __name__=='__main__':unittest.main()
