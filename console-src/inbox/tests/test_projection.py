import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from export_projection import export, identity_context
from projection import query, connect, ProjectionUnavailable

class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source.db';self.dest=self.root/'projection.sqlite3';self.now=time.time()
        with sqlite3.connect(self.source) as db:
            db.execute('CREATE TABLE parsed_messages(ticket_id INTEGER,message_id TEXT,author_type TEXT,author_email TEXT,customer_email TEXT,ticket_subject TEXT,channel TEXT,ticket_status TEXT,ticket_assignee TEXT,ticket_tags TEXT,created_at TEXT,received_at TEXT,is_customer_message INTEGER,message_text TEXT)')
            db.execute('CREATE TABLE webhook_events(ticket_id INTEGER,message_id TEXT,raw_payload TEXT)')
            db.execute('CREATE TABLE ticket_results(ticket_id INTEGER,message_id TEXT,draft_text TEXT,priority TEXT,action TEXT,reason TEXT,processed_at TEXT)')
            db.execute("INSERT INTO parsed_messages VALUES(1,'m1','customer','qa@example.com','qa@example.com','<script>title</script>','email','closed','agent@example.com','[\"vip\"]','2099-01-01','2099-01-01',1,?)",('<img onerror=alert(1)>'+('x'*21000),))
            db.execute("INSERT INTO ticket_results VALUES(1,'m1','Draft only','high','sensitive_draft','Review','2099-01-01')")
    def tearDown(self):self.tmp.cleanup()
    def test_identity_allowlist_from_same_event_never_exports_other_payload_fields(self):
        payload={'ticket':{'id':1,'customer':{'id':987,'name':'Synthetic Customer','email':'qa@example.com',
                  'phone':'+1 synthetic','password':'NEVER-EXPORT','orders':[{'total':999}],
                  'address':'NEVER-EXPORT'}}}
        with sqlite3.connect(self.source) as db:
            db.execute('INSERT INTO webhook_events VALUES(?,?,?)',(1,'m1',json.dumps(payload)))
        before=self.source.read_bytes();export(self.source,self.dest,now=self.now)
        self.assertEqual(before,self.source.read_bytes())
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        context=ticket['customerContext']
        self.assertEqual(context['identity'],{'id':'987','name':'Synthetic Customer','email':'qa@example.com','phone':'+1 synthetic'})
        self.assertEqual(context['source'],'canonical_webhook')
        self.assertEqual(context['status'],'observed')
        self.assertEqual(ticket['customerName'],'Synthetic Customer')
        self.assertNotIn('NEVER-EXPORT',json.dumps(ticket))

    def test_latest_event_does_not_inherit_prior_customer_identity(self):
        with sqlite3.connect(self.source) as db:
            db.execute('INSERT INTO webhook_events VALUES(?,?,?)',(1,'m1',json.dumps({'ticket':{'id':1,'customer':{'name':'Earlier customer','email':'qa@example.com'}}})))
            db.execute("INSERT INTO parsed_messages VALUES(1,'m2','customer','new@example.com','new@example.com','New message','email',NULL,NULL,NULL,'2099-02-01','2099-02-01',1,'Hello')")
        export(self.source,self.dest,now=self.now)
        context=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']['customerContext']
        self.assertEqual(context['identity']['email'],'new@example.com')
        self.assertIsNone(context['identity']['name'])
        self.assertEqual(context['observedAt'],'2099-02-01')

    def test_unknown_conflicting_malformed_and_oversized_identity_fail_closed(self):
        row={'ticket_id':1,'customer_email':'known@example.com','received_at':'2099-01-01'}
        for raw in ('invalid','x'*65537,json.dumps({'ticket':{'id':2,'customer':{'name':'Wrong'}}}),
                    json.dumps({'ticket':{'id':1,'customer':{'name':'Wrong','email':'other@example.com'}}})):
            context=identity_context(row,raw)
            self.assertIsNone(context['identity']['name'])
            self.assertEqual(context['identity']['email'],'known@example.com')
        conflict=identity_context(row,json.dumps({'ticket':{'id':2}}))
        self.assertTrue(conflict['conflict']);self.assertEqual(conflict['status'],'conflict')
        missing=identity_context({'ticket_id':1},None)
        self.assertEqual(missing['status'],'unknown')
        self.assertTrue(all(v is None for v in missing['identity'].values()))

    def test_readonly_partial_mapping_and_text_limits(self):
        before=self.source.read_bytes();export(self.source,self.dest,now=self.now)
        self.assertEqual(before,self.source.read_bytes())
        result=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)
        ticket=result['ticket'];self.assertEqual(ticket['status'],'closed')
        self.assertEqual(ticket['channel'],'email')
        self.assertTrue(ticket['truncated']);self.assertEqual(len(ticket['messages'][0]['body']),20000)
        self.assertEqual(ticket['readonlyDraft'],'Draft only')
        self.assertFalse(result['projection']['stale'])
        db=connect(self.dest)
        try:
            with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM tickets')
        finally:db.close()
    def test_observed_status_uses_latest_event(self):
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO parsed_messages VALUES(1,'m3','customer','new@example.com','new@example.com','New message','sms','snoozed','agent@example.com','[\"vip\",\"urgent\"]','2099-03-01','2099-03-01',1,'Hello')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['status'],'snoozed')
        self.assertEqual(ticket['channel'],'sms')
        self.assertEqual(ticket['assignee'],'agent@example.com')
        self.assertEqual(ticket['tags'],['vip','urgent'])
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO parsed_messages VALUES(1,'m4','customer','new@example.com','new@example.com','New message','sms',NULL,NULL,NULL,'2099-04-01','2099-04-01',1,'Hello')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['status'],'unknown')
        self.assertIsNone(ticket['assignee'])
        self.assertEqual(ticket['tags'],[])
    def test_atomic_publish_old_readers_and_failed_export_keep_previous(self):
        export(self.source,self.dest,now=self.now)
        db=connect(self.dest);db.execute('BEGIN');db.execute('SELECT * FROM tickets').fetchall()
        export(self.source,self.dest,now=self.now+1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM tickets').fetchone()[0],1);db.close()
        old=self.dest.read_bytes()
        with patch('export_projection.extract',side_effect=sqlite3.OperationalError('private detail')):
            with self.assertRaises(sqlite3.OperationalError):export(self.source,self.dest)
        self.assertEqual(old,self.dest.read_bytes())
        self.assertTrue(query('helpdesk.projection_status',{},self.dest)['projection']['stale'])
        self.assertEqual(self.dest.with_suffix('.error').read_text(),'')
    def test_ticket_and_message_windows_are_bounded(self):
        with sqlite3.connect(self.source) as db:
            for number in range(2,503):
                db.execute("INSERT INTO parsed_messages VALUES(?,?,'customer','','','Subject','email',NULL,NULL,NULL,'2099-01-01','2099-01-01',1,'Hello')",(number,f'm{number}'))
            for number in range(102):
                db.execute("INSERT INTO parsed_messages VALUES(1,?,'customer','','','Subject','email',NULL,NULL,NULL,'2099-02-01','2099-02-01',1,'Hello')",(f'extra{number}',))
        meta=export(self.source,self.dest,now=self.now)
        self.assertEqual(meta['ticketCount'],502);self.assertFalse(meta['truncated']);self.assertIsNone(meta['ticketLimit'])
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(len(ticket['messages']),100);self.assertTrue(ticket['truncated'])
        self.assertEqual(ticket['observedMessageCount'],103)

    def test_draft_lineage_withholds_superseded_customer_reply(self):
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO parsed_messages VALUES(1,'newer','customer','','','Followup','email',NULL,NULL,NULL,'2099-03-01','2099-03-01',1,'New question')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['readonlyDraft'],'')
        self.assertTrue(ticket['draftSuperseded'])
        self.assertEqual(ticket['draftSourceMessageId'],'m1')
        self.assertEqual(ticket['draftReason'],'Review')
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO ticket_results VALUES(1,'newer','Current reply','high','sensitive_draft','Current reason','2099-03-02')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertFalse(ticket['draftSuperseded'])
        self.assertEqual(ticket['readonlyDraft'],'Current reply')
        self.assertEqual(ticket['draftSourceMessageId'],'newer')
        self.assertEqual(ticket['draftSourceMessageAt'],'2099-03-01')

    def test_stale_schema_and_pagination(self):
        export(self.source,self.dest,now=self.now-181)
        self.assertTrue(query('helpdesk.projection_status',{},self.dest)['projection']['stale'])
        self.assertEqual(query('helpdesk.list_tickets',{'offset':1,'limit':1},self.dest)['tickets'],[])
        with sqlite3.connect(self.dest) as db:db.execute("UPDATE metadata SET payload='{}'")
        with self.assertRaises(ProjectionUnavailable):query('helpdesk.list_tickets',{},self.dest)

if __name__=='__main__':unittest.main()
