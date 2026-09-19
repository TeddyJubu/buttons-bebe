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
            db.execute('CREATE TABLE parsed_messages(ticket_id INTEGER,message_id TEXT,author_type TEXT,author_email TEXT,customer_email TEXT,ticket_subject TEXT,channel TEXT,ticket_status TEXT,ticket_assignee TEXT,ticket_tags TEXT,ticket_priority TEXT,ticket_spam INTEGER,ticket_trashed INTEGER,ticket_snoozed INTEGER,created_at TEXT,received_at TEXT,is_customer_message INTEGER,message_text TEXT)')
            db.execute('CREATE TABLE webhook_events(ticket_id INTEGER,message_id TEXT,raw_payload TEXT)')
            db.execute('CREATE TABLE ticket_results(ticket_id INTEGER,message_id TEXT,draft_text TEXT,priority TEXT,action TEXT,reason TEXT,processed_at TEXT)')
            db.execute("INSERT INTO parsed_messages VALUES(1,'m1','customer','qa@example.com','qa@example.com','<script>title</script>','email','closed','agent@example.com','[\"vip\"]','urgent',0,0,0,'2099-01-01','2099-01-01',1,?)",('<img onerror=alert(1)>'+('x'*21000),))
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
            db.execute("INSERT INTO parsed_messages VALUES(1,'m2','customer','new@example.com','new@example.com','New message','email',NULL,NULL,NULL,NULL,0,0,0,'2099-02-01','2099-02-01',1,'Hello')")
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
            db.execute("INSERT INTO parsed_messages VALUES(1,'m3','customer','new@example.com','new@example.com','New message','sms','snoozed','agent@example.com','[\"vip\",\"urgent\"]','low',1,0,1,'2099-03-01','2099-03-01',1,'Hello')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['status'],'snoozed')
        self.assertEqual(ticket['channel'],'sms')
        self.assertEqual(ticket['assignee'],'agent@example.com')
        self.assertEqual(ticket['tags'],['vip','urgent'])
        self.assertEqual(ticket['gorgiasPriority'],'low')
        self.assertEqual(ticket['priority'],'high')
        self.assertTrue(ticket['gorgiasSpam'])
        self.assertFalse(ticket['gorgiasTrashed'])
        self.assertTrue(ticket['gorgiasSnoozed'])
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO parsed_messages VALUES(1,'m4','customer','new@example.com','new@example.com','New message','sms',NULL,NULL,NULL,NULL,0,1,0,'2099-04-01','2099-04-01',1,'Hello')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['status'],'unknown')
        self.assertIsNone(ticket['assignee'])
        self.assertEqual(ticket['tags'],[])
        self.assertIsNone(ticket['gorgiasPriority'])
        self.assertFalse(ticket['gorgiasSpam'])
        self.assertTrue(ticket['gorgiasTrashed'])
        self.assertFalse(ticket['gorgiasSnoozed'])

    def test_exported_ticket_carries_view_state_and_never_an_assignee_identity(self):
        """Views read spam/trashed/assigneeEmail from the same observed columns.

        `assignee` carries the observed address verbatim (no operator identity
        lives here); only the inbox service compares it to the operator and
        decides "me". An unseen assignee is null, never guessed.
        """
        export(self.source,self.dest,now=self.now)
        listed=query('helpdesk.list_tickets',{},self.dest)['tickets'][0]
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        for row in (listed,ticket):
            self.assertEqual(row['status'],'closed')
            self.assertEqual(row['assigneeEmail'],'agent@example.com')
            self.assertFalse(row['spam'])
            self.assertFalse(row['trashed'])
            # The observed address is exported verbatim; "me" is not decided here.
            self.assertEqual(row['assignee'],'agent@example.com')
        # The snooze column drives the snoozed view even while status reads
        # open, and trash/spam stay flag buckets, never status strings.
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO parsed_messages VALUES(1,'m5','customer','new@example.com','new@example.com','New','sms','open','agent@example.com',NULL,NULL,1,0,1,'2099-05-01','2099-05-01',1,'Hello')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['status'],'snoozed')
        self.assertTrue(ticket['spam']);self.assertFalse(ticket['trashed'])
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO parsed_messages VALUES(1,'m6','customer','new@example.com','new@example.com','New','sms',NULL,NULL,NULL,NULL,0,1,0,'2099-06-01','2099-06-01',1,'Hello')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['status'],'unknown')
        self.assertTrue(ticket['trashed'])
        self.assertIsNone(ticket['assigneeEmail'])
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
                db.execute("INSERT INTO parsed_messages VALUES(?,?,'customer','','','Subject','email',NULL,NULL,NULL,NULL,0,0,0,'2099-01-01','2099-01-01',1,'Hello')",(number,f'm{number}'))
            for number in range(102):
                db.execute("INSERT INTO parsed_messages VALUES(1,?,'customer','','','Subject','email',NULL,NULL,NULL,NULL,0,0,0,'2099-02-01','2099-02-01',1,'Hello')",(f'extra{number}',))
        meta=export(self.source,self.dest,now=self.now)
        self.assertEqual(meta['ticketCount'],502);self.assertFalse(meta['truncated']);self.assertIsNone(meta['ticketLimit'])
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(len(ticket['messages']),100);self.assertTrue(ticket['truncated'])
        self.assertEqual(ticket['observedMessageCount'],103)

    def test_metadata_counts_flagged_tickets_for_view_totals(self):
        """#33: All-count must subtract flagged rows even beyond a loaded prefix."""
        with sqlite3.connect(self.source) as db:
            # Two spam tickets and one trashed ticket beyond ticket 1.
            db.execute("INSERT INTO parsed_messages VALUES(2,'s1','customer','','','Spam one','email',NULL,NULL,NULL,NULL,1,0,0,'2099-01-02','2099-01-02',1,'Hello')")
            db.execute("INSERT INTO parsed_messages VALUES(3,'s2','customer','','','Spam two','email',NULL,NULL,NULL,NULL,1,0,0,'2099-01-03','2099-01-03',1,'Hello')")
            db.execute("INSERT INTO parsed_messages VALUES(4,'t1','customer','','','Trashed','email',NULL,NULL,NULL,NULL,0,1,0,'2099-01-04','2099-01-04',1,'Hello')")
        meta=export(self.source,self.dest,now=self.now)
        self.assertEqual(meta['ticketCount'],4)
        self.assertEqual(meta['spamCount'],2)
        self.assertEqual(meta['trashCount'],1)
        status=query('helpdesk.projection_status',{},self.dest)['projection']
        self.assertEqual(status['spamCount'],2)
        self.assertEqual(status['trashCount'],1)

    def test_draft_lineage_withholds_superseded_customer_reply(self):
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO parsed_messages VALUES(1,'newer','customer','','','Followup','email',NULL,NULL,NULL,NULL,0,0,0,'2099-03-01','2099-03-01',1,'New question')")
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

    def test_customer_name_derives_from_email_when_none_observed(self):
        # Issue #35: no observed name ⇒ derive a Gorgias-style display name
        # from the address local part; an observed name always wins.
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        # qa@example.com has no observed name in the event payload used here.
        self.assertEqual(ticket['customerName'],'Qa')
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO parsed_messages VALUES(1,'m35','customer','estywa.s@gmail.com','estywa.s@gmail.com','Re order','email',NULL,NULL,NULL,NULL,0,0,0,'2099-07-01','2099-07-01',1,'Hi')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['customerName'],'Estywa S')
        # Observed name (identity_context) still wins over the derivation.
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO webhook_events VALUES(?,?,?)",(1,'m35',json.dumps({'ticket':{'id':1,'customer':{'name':'Esty Real','email':'estywa.s@gmail.com'}}})))
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['customerName'],'Esty Real')
        # A conflicted identity never provides a name; derivation applies.
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE webhook_events SET raw_payload=? WHERE ticket_id=1 AND message_id='m35'",(json.dumps({'ticket':{'id':2,'customer':{'name':'Other','email':'other@example.com'}}}),))
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['customerName'],'Estywa S')

    def test_customer_name_derivation_skips_mailbox_and_aliased_addresses(self):
        # Mailbox/login addresses and shop display names never become personas.
        for email,expected in (
            ('helpdesk-support@agentmail.to','Customer'),
            ('teddyjubu@agentmail.to','Customer'),
        ):
            with sqlite3.connect(self.source) as db:
                db.execute("INSERT INTO parsed_messages VALUES(1,'mx','customer',?,?, 'Re order','email',NULL,NULL,NULL,NULL,0,0,0,'2099-08-01','2099-08-01',1,'Hi')",(email,email))
            export(self.source,self.dest,now=self.now)
            ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
            self.assertEqual(ticket['customerName'],expected,email)
        # Bare local-part-only values never crash the derivation.
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO parsed_messages VALUES(1,'mz','customer','plain','plain','Re order','email',NULL,NULL,NULL,NULL,0,0,0,'2099-09-01','2099-09-01',1,'Hi')")
        export(self.source,self.dest,now=self.now)
        ticket=query('helpdesk.get_ticket',{'ticketId':'gorgias:1'},self.dest)['ticket']
        self.assertEqual(ticket['customerName'],'Customer')

if __name__=='__main__':unittest.main()
