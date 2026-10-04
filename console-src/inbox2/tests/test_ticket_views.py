from contextlib import closing
from pathlib import Path
import json
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_api as api

class TicketViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'live.sqlite3'
        self.patch=patch.object(api,'DB',self.db);self.patch.start();self.addCleanup(self.patch.stop)
        with closing(api.database()) as db,db:
            db.execute('CREATE TABLE tickets(id TEXT PRIMARY KEY,updated REAL,payload TEXT,generation TEXT)')
            db.execute('CREATE TABLE meta(id INTEGER PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO meta VALUES(1,?)',(json.dumps({'complete':True}),))
            for i,extra in enumerate([{}, {'spam':True}, {'trashed_datetime':'2026-01-01T00:00:00Z'}, {'status':'closed'}, {'assignee_user':{'email':'owner@example.test','name':'Owner'}}, {'snooze_datetime':'2099-01-01T00:00:00Z'}],1):
                raw={'id':i,'status':'open','spam':False,'trashed_datetime':None,'snooze_datetime':None,'priority':'high','channel':'email','tags':[{'name':'Returns'}],**extra}
                row=api.summary(raw)
                db.execute('INSERT INTO tickets VALUES(?,?,?,?)',(row['id'],i,json.dumps(row),'g'))
    def read(self,**args):
        return api.list_tickets(api.ListArguments(**args).model_dump())
    def ids(self,**args):return [r['id'] for r in self.read(**args)['tickets']]
    def test_views_use_observed_values_and_keep_trash(self):
        self.assertEqual(self.ids(view='all'),['gorgias:6','gorgias:5','gorgias:4','gorgias:1'])
        self.assertEqual(self.ids(view='open'),['gorgias:6','gorgias:5','gorgias:1'])
        self.assertEqual(self.ids(view='closed'),['gorgias:4'])
        self.assertEqual(self.ids(view='spam'),['gorgias:2'])
        self.assertEqual(self.ids(view='trash'),['gorgias:3'])
        self.assertEqual(self.ids(view='snoozed'),['gorgias:6'])
    def test_assignment_does_not_invent_operator(self):
        with patch.object(api,'OPERATOR_EMAIL',''):
            self.assertEqual(self.ids(view='assigned'),[])
            self.assertFalse(self.read()['categoryAvailability']['assigned'])
        with patch.object(api,'OPERATOR_EMAIL','owner@example.test'):
            self.assertEqual(self.ids(view='assigned'),['gorgias:5'])
        self.assertEqual(self.ids(view='unassigned'),['gorgias:6','gorgias:4','gorgias:1'])
    def test_observed_snooze_expires_without_changing_the_provider_record(self):
        deadline=api.epoch('2099-01-01T00:00:00Z')
        with closing(api.database()) as db:
            before=db.execute("SELECT payload FROM tickets WHERE id='gorgias:6'").fetchone()[0]
        for now,expected in [(deadline-1,['gorgias:6']),(deadline,[]),(deadline+1,[])]:
            with self.subTest(now=now),patch.object(api.time,'time',return_value=now):
                result=self.read(view='snoozed')
                self.assertEqual([row['id'] for row in result['tickets']],expected)
                self.assertEqual(result['total'],len(expected))
                self.assertIsNone(result['nextOffset'])
                self.assertTrue(result['categoryAvailability']['snoozed'])
                self.assertEqual(self.ids(view='open'),['gorgias:6','gorgias:5','gorgias:1'])
        with closing(api.database()) as db:
            after=db.execute("SELECT payload FROM tickets WHERE id='gorgias:6'").fetchone()[0]
        self.assertEqual(after,before)
    def test_exact_filters_counts_and_pagination(self):
        a=self.read(tag='returns',priority='high',channel='email',limit=2)
        self.assertEqual(a['total'],4);self.assertEqual(a['nextOffset'],2)
        self.assertEqual([r['id'] for r in a['tickets']],['gorgias:6','gorgias:5'])
        self.assertEqual(self.ids(tag='turn'),[])
        self.assertEqual(self.ids(assignee='owner@example.test'),['gorgias:5'])
        self.assertEqual(self.ids(channel='chat'),[])
    def test_absent_category_is_explicit(self):
        row=api.summary({'id':88})
        self.assertEqual(row['categoryAvailability'],{'spam':False,'trash':False,'snoozed':False})
    def test_summary_refresh_keeps_clean_detail_preview_only_for_same_activity(self):
        row=api.summary({'id':1,'last_message_datetime':'2026-01-01T00:00:00Z','excerpt':'Clean excerpt'})
        row.update(snippet='Clean detail',previewMessageId='m1',previewProvenance={'source':'message'})
        with closing(api.database()) as db,db:
            api.cache_summary(db,row,'g')
            fresh=api.summary({'id':1,'last_message_datetime':'2026-01-01T00:00:00Z','excerpt':'Different excerpt'})
            api.cache_summary(db,fresh,'g')
            saved=json.loads(db.execute("SELECT payload FROM tickets WHERE id='gorgias:1'").fetchone()[0])
            self.assertEqual(saved['snippet'],'Clean detail')
            newer=api.summary({'id':1,'last_message_datetime':'2026-01-02T00:00:00Z','excerpt':'New message'})
            api.cache_summary(db,newer,'g')
            saved=json.loads(db.execute("SELECT payload FROM tickets WHERE id='gorgias:1'").fetchone()[0])
            self.assertEqual(saved['snippet'],'New message')
            self.assertNotIn('previewMessageId',saved)

    def test_unknown_operations_stay_refused(self):
        self.assertEqual(set(api.SCHEMAS),{'helpdesk.list_tickets','helpdesk.get_ticket','helpdesk.get_messages','helpdesk.capabilities'})
