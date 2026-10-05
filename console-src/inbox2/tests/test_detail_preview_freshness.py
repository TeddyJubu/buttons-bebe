from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import json, sys, tempfile, unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_api as api

T6,T7='2026-10-05T06:00:00Z','2026-10-05T07:00:00Z'
OLD={'id':842000118,'display_text':'Clean detail preview for order #10322954.','created_datetime':T6,'channel':'email','public':True}

class FakeMCP:
    raw={};msgs=[];calls=[];during=None;fail=False
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def call(self,name,args):
        FakeMCP.calls.append(name)
        if FakeMCP.fail: raise OSError('offline')
        if name!='get_ticket' and FakeMCP.during: FakeMCP.during()
        return dict(FakeMCP.raw) if name=='get_ticket' else {'data':list(FakeMCP.msgs),'meta':{}}

def raw(last,excerpt,updated=None,status='open'): return {'id':841999118,'status':status,'excerpt':excerpt,'last_message_datetime':last,'updated_datetime':updated or last}
def old_draft(t): t.update(readonlyDraft='Old AI draft',draftSourceMessageAt=T6,draftSuperseded=False)

class DetailPreviewFreshness(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        for p in (patch.object(api,'DB',Path(tmp.name)/'live.sqlite3'),patch.object(api,'MCP',FakeMCP),
                  patch.object(api,'enrich',old_draft),patch.object(api,'attach_customer_details',lambda t:t)):
            p.start();self.addCleanup(p.stop)
        api.DETAIL_CACHE.clear();FakeMCP.calls=[];FakeMCP.during=None;FakeMCP.fail=False
        with closing(api.database()) as db,db:
            db.execute('CREATE TABLE tickets(id TEXT PRIMARY KEY,updated REAL,payload TEXT,generation TEXT)')
            db.execute('CREATE TABLE meta(id INTEGER PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO meta VALUES(1,?)',(json.dumps({'complete':True,'generation':'g1'}),))
    def sync(self,r):
        with closing(api.database()) as db,db: api.cache_summary(db,api.summary(r),'g1')
    def stored(self):
        with closing(api.database()) as db: row=db.execute('SELECT payload,generation FROM tickets').fetchone()
        return json.loads(row[0]),row[1]
    def detail(self,r,msgs,keep=False):
        FakeMCP.raw,FakeMCP.msgs=r,msgs
        if not keep: api.DETAIL_CACHE.clear()
        return api.get_ticket(841999118)
    def assertStale(self,out,stale=True):
        self.assertEqual((out['syncStale'],out['draftSuperseded']),(stale,stale))
        self.assertEqual(out['readonlyDraft'],'Old AI draft');self.assertTrue(out['messages'])  # still readable

    def test_p17_changed_summary_then_stale_detail_then_same_activity_sync(self):
        self.sync(raw(T6,'List excerpt'));self.detail(raw(T6,'List excerpt'),[OLD])
        self.assertEqual(self.stored()[0]['previewMessageId'],'842000118')   # fresh detail promotes
        self.sync(raw(T7,'New activity'))
        out=self.detail(raw(T7,'New activity'),[OLD])                       # detail page lags provider
        self.assertNotIn('previewMessageId',out);self.assertStale(out)
        self.sync(raw(T7,'New activity'))
        s,gen=self.stored()
        self.assertEqual((s['snippet'],s.get('previewMessageId'),s['lastMessageAt'],gen),('New activity',None,T7,'g1'))
        self.assertEqual(FakeMCP.calls,['get_ticket','get_ticket_messages']*2)

    def test_stale_detail_keeps_the_clean_synced_excerpt_and_provenance(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
        from tools.gorgias_content import curate_summary,curate_ticket
        self.sync(curate_summary(raw(T7,'<p>New activity অর্ডার #10330001 🙏</p><div style="display:none">HIDDEN-NEW</div>')))
        before=self.stored()
        out=self.detail(curate_ticket(raw(T7,'<p>Different lagging excerpt</p><div style="display:none">HIDDEN-DETAIL</div>')),[OLD])
        self.assertStale(out)
        self.assertEqual(self.stored(),before)
        self.assertEqual(out['snippet'],'Different lagging excerpt')
        self.assertEqual(out['previewProvenance']['cleanupVersion'],'intake-1')

    def test_internal_note_activity_permits_latest_public_preview(self):
        note={'id':9,'display_text':'staff only','created_datetime':T7,'channel':'internal-note','public':False}
        out=self.detail(raw(T7,'excerpt'),[OLD,note])
        self.assertEqual((out['previewMessageId'],out['snippet']),('842000118',OLD['display_text']));self.assertStale(out,False)
        self.assertEqual(self.stored()[0]['previewMessageId'],'842000118')

    def test_undated_public_message_cannot_be_hidden_by_a_current_internal_note(self):
        note={'id':9,'display_text':'staff only','created_datetime':T7,'channel':'internal-note','public':False}
        for at in (None,'garbage','','2026-10-05T06:00:00','2026-10-05'):
            with self.subTest(at=at):
                self.sync(raw(T7,'New activity'))
                unknown={**OLD,'id':842000119,'display_text':'Unordered new customer request','created_datetime':at}
                out=self.detail(raw(T7,'New activity'),[OLD,unknown,note])
                self.assertStale(out)
                self.assertNotIn('previewMessageId',out)
                saved,_=self.stored()
                self.assertEqual(saved['snippet'],'New activity')
                self.assertNotIn('previewMessageId',saved)
                self.assertNotIn('841999118',api.DETAIL_CACHE)

    def test_older_detail_does_not_rewind_newer_stored_summary(self):
        self.sync(raw(T7,'New activity'))
        out=self.detail(raw(T6,'List excerpt'),[OLD])
        self.assertEqual(out['previewMessageId'],'842000118')                # detail itself is self-consistent
        self.assertStale(out)                                                 # but older than the synced summary
        s,gen=self.stored()
        self.assertEqual((s['snippet'],s['lastMessageAt'],s.get('previewMessageId'),gen),('New activity',T7,None,'g1'))

    def test_invalid_dates_are_conservative(self):
        for last,at in ((None,T6),('garbage',T6),(T6,None),(T6,'garbage')):
            with self.subTest(last=last,at=at):
                out=self.detail(raw(last,'excerpt'),[{**OLD,'created_datetime':at}])
                self.assertNotIn('previewMessageId',out)
                self.assertEqual(out['syncStale'],bool(api.epoch(last)))         # unknown activity is not claimed stale
        self.sync(raw(T7,'New activity'))
        self.detail(raw('garbage','junk'),[OLD])                              # undated detail cannot overwrite dated row
        self.assertEqual(self.stored()[0]['snippet'],'New activity')

    def test_fresh_head_is_not_stale_and_is_cached(self):
        out=self.detail(raw(T6,'excerpt'),[OLD]);self.assertStale(out,False)
        self.assertIn('841999118',api.DETAIL_CACHE)

    def test_newer_summary_bypasses_detail_cache(self):
        self.detail(raw(T6,'excerpt'),[OLD]);entry=api.DETAIL_CACHE['841999118']
        self.sync(raw(T7,'New activity'));FakeMCP.calls=[]
        new={**OLD,'id':842000119,'display_text':'Newest','created_datetime':T7}
        out=self.detail(raw(T7,'New activity'),[OLD,new],keep=True)          # within 15 s: still re-fetched
        self.assertEqual(FakeMCP.calls,['get_ticket','get_ticket_messages'])
        self.assertEqual((out['previewMessageId'],out['syncStale']),('842000119',False))
        self.assertIsNot(api.DETAIL_CACHE['841999118'],entry)

    def test_newer_summary_with_failed_fetch_fails_closed(self):
        self.detail(raw(T6,'excerpt'),[OLD]);entry=api.DETAIL_CACHE['841999118']
        self.sync(raw(T7,'New activity'));FakeMCP.fail=True
        self.assertStale(self.detail(raw(T7,'x'),[],keep=True))
        self.assertIs(api.DETAIL_CACHE['841999118'],entry)                   # shared entry not mutated
        self.assertEqual((entry[1]['syncStale'],entry[1]['draftSuperseded']),(False,False))

    def test_newer_summary_during_fetch_is_stale_and_kept(self):
        self.sync(raw(T6,'excerpt'))
        FakeMCP.during=lambda:self.sync(raw(T7,'New activity'))
        out=self.detail(raw(T6,'excerpt'),[OLD]);self.assertStale(out)
        s,gen=self.stored();self.assertEqual((s['snippet'],s['lastMessageAt'],gen),('New activity',T7,'g1'))
        self.assertNotIn('841999118',api.DETAIL_CACHE)

    def test_same_activity_older_metadata_does_not_regress(self):
        self.sync(raw(T6,'excerpt','2026-10-05T08:00:00Z','closed'))
        out=self.detail(raw(T6,'excerpt',T6,'open'),[OLD]);self.assertStale(out)
        self.assertEqual(self.stored()[0]['status'],'closed')

    def test_newer_same_activity_metadata_bypasses_cached_detail(self):
        self.detail(raw(T6,'excerpt'),[OLD]);FakeMCP.calls=[]
        self.sync(raw(T6,'excerpt',T7,'closed'))
        out=self.detail(raw(T6,'excerpt',T7,'closed'),[OLD],keep=True)
        self.assertEqual(FakeMCP.calls,['get_ticket','get_ticket_messages'])
        self.assertEqual(out['status'],'closed');self.assertStale(out,False)

    def test_unreadable_or_malformed_summary_keeps_cache_readable_but_not_actionable(self):
        for failure in ('sqlite','json','shape'):
            with self.subTest(failure=failure):
                self.detail(raw(T6,'excerpt'),[OLD]);entry=api.DETAIL_CACHE['841999118']
                FakeMCP.calls=[]
                if failure=='sqlite':
                    with patch.object(api,'database',side_effect=api.sqlite3.OperationalError('unavailable')):
                        out=api.get_ticket(841999118)
                else:
                    with closing(api.database()) as db,db:
                        db.execute('UPDATE tickets SET payload=?',('invalid-json' if failure=='json' else '[]',))
                    out=api.get_ticket(841999118)
                    with closing(api.database()) as db,db: db.execute('DELETE FROM tickets')
                self.assertStale(out)
                self.assertEqual(FakeMCP.calls,[])
                self.assertIs(api.DETAIL_CACHE['841999118'],entry)
                self.assertFalse(entry[1]['syncStale']);self.assertFalse(entry[1]['draftSuperseded'])

    def test_expired_cache_with_failed_fetch_cannot_offer_old_draft(self):
        self.detail(raw(T6,'excerpt'),[OLD]);entry=api.DETAIL_CACHE['841999118']
        api.DETAIL_CACHE['841999118']=(0,entry[1]);FakeMCP.fail=True
        self.assertStale(api.get_ticket(841999118))
        self.assertFalse(entry[1]['syncStale']);self.assertFalse(entry[1]['draftSuperseded'])

    def test_fresh_detail_replaces_same_activity_preview_but_sync_cannot(self):
        self.detail(raw(T6,'excerpt'),[{**OLD,'display_text':'Body A'}])
        self.assertEqual(self.stored()[0]['snippet'],'Body A')
        out=self.detail(raw(T6,'excerpt','2026-10-05T06:30:00Z'),[{**OLD,'display_text':'Body B'}])  # edited body, same id/activity
        self.assertStale(out,False)
        self.assertEqual((self.stored()[0]['snippet'],self.stored()[0]['previewMessageId']),('Body B','842000118'))
        self.sync(raw(T6,'raw excerpt','2026-10-05T06:45:00Z'))              # summary-only refresh, same activity
        self.assertEqual(self.stored()[0]['snippet'],'Body B')

if __name__=='__main__': unittest.main()
