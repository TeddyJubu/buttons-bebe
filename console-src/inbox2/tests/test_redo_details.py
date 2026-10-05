from contextlib import closing
from pathlib import Path
import sqlite3
import copy
import json
import os
import stat
from unittest.mock import patch
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redo_details as details
import redo_worker as worker

EMAIL = 'person@example.com'
RETURN = {'id': 'r1', 'status': 'open', 'order_name': '#10312345', 'created_at': '2026-10-01', 'tags': ['secret']}


class RedoTests(unittest.TestCase):
    def setUp(self):
        worker.STOP.clear();self.addCleanup(worker.STOP.clear)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.queue = Path(temp.name) / 'queue.sqlite3'
        self.snapshot = Path(temp.name) / 'redo.sqlite3'
        with closing(self.db()) as db, db:
            details.initialize(db)
        self.calls = []
        self.response = {'returns': [RETURN]}
        self.failing = set()

    def db(self):
        return sqlite3.connect(self.queue)

    def call(self, order):
        self.calls.append(order)
        if isinstance(self.response, Exception) or order in self.failing:
            raise self.response if isinstance(self.response, Exception) else RuntimeError('down')
        if self.response == 'echo':
            return {'returns': [{**RETURN, 'order_name': '#' + order}]}
        return self.response

    def attach(self, orders=('#10312345',), email=EMAIL, ticket_id='gorgias:1', now=1000):
        ticket = {'id': ticket_id}
        return details.attach(ticket, email, list(orders), self.db, self.snapshot, now)['redoDetails']

    def run_worker(self, w=None, now=1001):
        w = w or worker.Worker(self.snapshot, call=self.call)
        rows = worker.read_requests(self.queue, now)
        w.process(rows, now)
        return w

    def test_identity_is_required_and_exact(self):
        for orders, email in [((), EMAIL), (('#1031',), 'bad'), (('order 1234',), EMAIL), (('1', '2', '3', '4'), EMAIL)]:
            self.assertEqual(self.attach(orders, email)['status'], 'unavailable')
        self.assertEqual(worker.read_requests(self.queue, 1001), [])

    def test_pending_then_observed_with_allowlisted_fields(self):
        self.assertEqual(self.attach()['status'], 'pending')
        self.run_worker()
        self.assertEqual(self.calls, ['10312345'])
        view = self.attach(now=1002)
        self.assertEqual(view['status'], 'observed')
        ret = view['orders']['10312345']['returns'][0]
        self.assertNotIn('tags', ret)
        self.assertIn('tracking_url', ret['missing'])
        self.assertTrue(view['fetchedAt'])

    def test_all_rejected_is_unavailable_not_empty(self):
        self.response = {'returns': [{**RETURN, 'order_name': '#99999'}, {'id': 'x'}]}
        self.attach()
        self.run_worker()
        view = self.attach(now=1002)
        self.assertEqual(view['status'], 'unavailable')
        self.assertEqual(view['incomplete'], ['10312345'])

    def test_valid_plus_rejected_is_partial(self):
        self.response = {'returns': [RETURN, {**RETURN, 'order_name': '#99999'}]}
        self.attach()
        self.run_worker()
        view = self.attach(now=1002)
        self.assertEqual((view['status'], view['incomplete']), ('partial', ['10312345']))
        self.assertEqual(len(view['orders']['10312345']['returns']), 1)

    def test_true_empty_is_empty(self):
        self.response = {'returns': []}
        self.attach()
        self.run_worker()
        self.assertEqual(self.attach(now=1002)['status'], 'empty')

    def test_multi_order_partial_failure_keeps_per_order_time(self):
        orders, self.response = ('#10312345', '#10312346'), 'echo'
        self.attach(orders)
        w = self.run_worker()
        later = 1000 + details.FOUND_TTL + 2
        self.failing = {'10312346'}
        self.attach(orders, now=later - 1)
        self.run_worker(w, now=later)
        view = self.attach(orders, now=later + 1)
        self.assertEqual(view['status'], 'stale')
        good, bad = view['orders']['10312345'], view['orders']['10312346']
        self.assertEqual((good['observedAtEpoch'], good.get('refreshFailed')), (later, None))
        self.assertEqual((bad['observedAtEpoch'], bad['refreshFailed'], bad['refreshFailedAt']), (1001, True, later))

    def test_new_order_failing_without_prior_is_partial(self):
        orders, self.response, self.failing = ('#10312345', '#10312346'), 'echo', {'10312346'}
        self.attach(orders)
        self.run_worker()
        view = self.attach(orders, now=1002)
        self.assertEqual((view['status'], view['incomplete']), ('partial', ['10312346']))
        self.assertNotIn('10312346', view['orders'])

    def test_nested_structures_are_bounded_and_private_keys_dropped(self):
        nested = {**RETURN, 'refunds': [{'amount': '5.00', 'customer_email': 'x@y.z'}] * 20,
                  'totals': {'refund': '5.00', 'shipping_address': {'line1': 'secret'}},
                  'exchange': {'itemCount': 1, 'name': 'a' * 500, 'unknown': {'private': 'hidden'}},
                  'shipments': [{'tracking': {'carrier': 'USPS'}}], 'notes': 'private', 'order': {'customer': 'x'}}
        self.response = {'returns': [nested]}
        self.attach()
        self.run_worker()
        ret = self.attach(now=1002)['orders']['10312345']['returns'][0]
        self.assertEqual(ret['refunds'][0], {'amount': '5.00'})
        self.assertEqual(len(ret['refunds']), worker.MAX_LIST)
        self.assertEqual(ret['totals'], {'refund': '5.00'})
        self.assertEqual(ret['exchange']['itemCount'], 1)
        self.assertEqual(len(ret['exchange']['name']), worker.MAX_STR)
        self.assertNotIn('unknown', ret['exchange'])
        self.assertEqual(ret['shipments'], [{}])
        self.assertNotIn('notes', ret)
        self.assertNotIn('order', ret)
        self.assertIn('compensation_methods', ret['missing'])
        self.assertNotIn('refunds', ret['missing'])

    def test_lookup_cap_reserves_whole_request(self):
        w = worker.Worker(self.snapshot, call=self.call, publish=lambda *a: None)
        w.window_at, w.lookups = 1001, worker.MAX_LOOKUPS_PER_MINUTE - 1
        request = details.make_request('gorgias:1', EMAIL, ['#10312345', '#10312346'])
        self.assertEqual(w.process([(request, details.request_key(request), 1000)], 1001), 0)
        self.assertEqual((self.calls, w.lookups), ([], worker.MAX_LOOKUPS_PER_MINUTE - 1))

    def test_publish_never_creates_parent(self):
        worker.publish({}, self.snapshot)  # existing empty directory is fine
        self.assertTrue(self.snapshot.exists())
        missing = self.snapshot.parent / 'absent' / 'redo.sqlite3'
        with self.assertRaises(OSError):
            worker.publish({}, missing)
        self.assertFalse(missing.parent.exists())

    def test_defaults_resolve_paths_at_call_time(self):
        self.attach()
        self.run_worker()
        original = details.SNAPSHOT
        self.addCleanup(setattr, details, 'SNAPSHOT', original)
        details.SNAPSHOT = self.snapshot
        self.assertIsNotNone(details.read_snapshot('gorgias:1'))

    def test_failure_without_snapshot_is_unavailable_not_empty(self):
        self.response = ConnectionError('down')
        self.attach()
        w=self.run_worker()
        self.assertEqual(self.attach(now=1002)['status'], 'unavailable')
        self.assertEqual(len(self.calls),1)
        self.response = {'error': 'Redo API 500'}
        self.attach(now=1999)  # New request after backoff, not the old attempted timestamp.
        self.run_worker(w,now=2000)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.attach(now=2001)['status'], 'unavailable')

    def test_failure_with_snapshot_is_stale_with_timestamp(self):
        self.attach()
        w = self.run_worker()
        self.response = RuntimeError('down')
        self.attach(now=1000 + details.FOUND_TTL + 1)
        self.run_worker(w, now=1000 + details.FOUND_TTL + 2)
        view = self.attach(now=1000 + details.FOUND_TTL + 3)
        self.assertEqual(view['status'], 'stale')
        self.assertEqual(view['fetchedAtEpoch'], 1001)

    def test_snapshot_never_crosses_identity(self):
        self.attach()
        self.run_worker()
        self.assertEqual(self.attach(email='other@example.com', now=1002)['status'], 'pending')
        self.assertEqual(self.attach(orders=('#55555',), now=1002)['status'], 'pending')
        self.assertEqual(self.attach(ticket_id='gorgias:2', now=1002)['status'], 'pending')

    def test_bounds_and_tampered_queue(self):
        self.response = {'returns': [{**RETURN, 'id': str(i)} for i in range(15)]}
        self.attach()
        self.run_worker()
        order = self.attach(now=1002)['orders']['10312345']
        self.assertEqual((len(order['returns']), order['truncated']), (worker.MAX_RETURNS, True))
        with closing(self.db()) as db, db:
            db.execute("UPDATE redo_requests SET request='{\"ticketId\":\"gorgias:1\",\"email\":\"x@y.z\",\"orders\":[\"1234\"]}'")
        self.assertEqual(worker.read_requests(self.queue, 1001), [])

    def test_restart_keeps_snapshot(self):
        self.attach()
        self.run_worker()
        self.assertIn('gorgias:1', worker.Worker(self.snapshot, call=self.call).cache)

    def test_worker_has_fixed_target(self):
        self.assertEqual((worker.MCP_URL, worker.TOOL), ('http://127.0.0.1:8078/mcp', 'get_returns_for_order'))
        source = (Path(__file__).resolve().parents[3] / 'tools' / 'redo_mcp.py').read_text()
        self.assertIn('def get_returns_for_order(order_name: str)', source)
        for field in worker.FIELDS + worker.STRUCTURED:
            self.assertIn(f'"{field}"', source)


    def test_publish_failure_preserves_memory_disk_and_same_request_retries(self):
        self.attach();w=self.run_worker();old=copy.deepcopy(w.cache);disk=self.snapshot.read_bytes()
        self.response={'returns':[{**RETURN,'id':'updated'}]}
        self.attach(now=3000);rows=worker.read_requests(self.queue,3001);attempts=[]
        def flaky(cache,destination):
            attempts.append(True)
            if len(attempts)==1:raise OSError('synthetic publication failure')
            worker.publish(cache,destination)
        w.publish=flaky
        with self.assertRaises(OSError):w.process(rows,3001)
        self.assertEqual(w.cache,old);self.assertEqual(self.snapshot.read_bytes(),disk)
        self.assertEqual(w.process(rows,3002),1);self.assertEqual(len(attempts),2)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(w.cache,worker.load_cache(self.snapshot))
        self.assertEqual(self.attach(now=3003)['orders']['10312345']['returns'][0]['id'],'updated')

    def test_directory_fsync_failure_restores_prior_snapshot_and_memory(self):
        self.attach();w=self.run_worker();old=copy.deepcopy(w.cache);disk=self.snapshot.read_bytes()
        self.attach(now=3000);rows=worker.read_requests(self.queue,3001);original=worker._fsync_directory;attempts=[]
        prior_inode=self.snapshot.stat().st_ino
        def failure(directory):
            attempts.append(True)
            if len(attempts)==1:
                self.assertNotEqual(self.snapshot.stat().st_ino,prior_inode)
                raise OSError('synthetic directory fsync failure after replacement')
            original(directory)
        with patch.object(worker,'_fsync_directory',side_effect=failure):
            with self.assertRaises(OSError):w.process(rows,3001)
        self.assertEqual(w.cache,old);self.assertEqual(self.snapshot.read_bytes(),disk)
        self.assertEqual(len(attempts),2);self.assertEqual(self.snapshot.stat().st_ino,prior_inode)
        self.assertEqual(w.process(rows,3002),1);self.assertEqual(len(self.calls),3)

    def test_durable_publish_backup_cleanup_failure_keeps_memory_and_disk_coherent(self):
        self.attach();w=self.run_worker();self.response={'returns':[{**RETURN,'id':'updated'}]}
        self.attach(now=3000);rows=worker.read_requests(self.queue,3001);original=Path.unlink;unlinks=[]
        def unlink(path,*args,**kwargs):
            if path.name.startswith('.redo-before-'):
                unlinks.append(path)
                if len(unlinks)==2:raise OSError('synthetic cleanup failure after durable publication')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink),self.assertLogs(level='WARNING') as logs:
            self.assertEqual(w.process(rows,3001),1)
        self.assertEqual(len(unlinks),2);self.assertTrue(unlinks[-1].exists())
        self.assertEqual(logs.output,['WARNING:root:Redo snapshot backup cleanup deferred'])
        self.assertEqual(w.cache,worker.load_cache(self.snapshot))
        self.assertEqual(self.attach(now=3002)['orders']['10312345']['returns'][0]['id'],'updated')
        self.assertEqual(w.process(rows,3002),0);self.assertEqual(len(self.calls),2)

    def test_consumed_temporary_cleanup_cannot_fail_after_durable_publication(self):
        self.attach();w=self.run_worker();self.response={'returns':[{**RETURN,'id':'updated'}]}
        self.attach(now=3000);rows=worker.read_requests(self.queue,3001);original=Path.unlink;consumed_attempts=[]
        def unlink(path,*args,**kwargs):
            if path.name.startswith('.redo-') and not path.name.startswith('.redo-before-') and not path.exists():
                consumed_attempts.append(path)
                raise PermissionError('synthetic consumed temporary cleanup error')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):self.assertEqual(w.process(rows,3001),1)
        self.assertEqual(consumed_attempts,[])
        self.assertEqual(w.cache,worker.load_cache(self.snapshot))
        self.assertEqual(self.attach(now=3002)['orders']['10312345']['returns'][0]['id'],'updated')
        self.assertEqual(w.process(rows,3002),0);self.assertEqual(len(self.calls),2)

    def test_restart_sql_byte_guard_excludes_unicode_row_before_python_loading(self):
        oversized=json.dumps({'text':'é'*(worker.MAX_CACHE_BYTES//2)},ensure_ascii=False)
        self.assertLess(len(oversized),worker.MAX_CACHE_BYTES)
        self.assertGreater(len(oversized.encode()),worker.MAX_CACHE_BYTES)
        with closing(sqlite3.connect(self.snapshot)) as db:
            db.execute('CREATE TABLE redo(ticket_id TEXT PRIMARY KEY,payload TEXT,updated_at REAL)')
            db.execute('INSERT INTO redo VALUES(?,?,?)',('too-big-unicode',oversized,10))
            db.execute('INSERT INTO redo VALUES(?,?,?)',('valid',json.dumps({'status':'observed'}),1));db.commit()
        original=sqlite3.connect;queries=[];loaded=[]
        def connect(*args,**kwargs):
            db=original(*args,**kwargs);db.set_trace_callback(queries.append)
            def row_factory(cursor,row):
                if len(row)==3:
                    self.assertNotEqual(row[0],'too-big-unicode')
                    loaded.append(row[0])
                return row
            db.row_factory=row_factory;return db
        with patch.object(worker.sqlite3,'connect',side_effect=connect):cache,trimmed=worker._load_cache(self.snapshot)
        self.assertEqual(set(cache),{'valid'});self.assertTrue(trimmed);self.assertEqual(loaded,['valid'])
        self.assertTrue(any('length(CAST(payload AS BLOB))' in query for query in queries))

    def test_snapshot_fsyncs_file_and_parent_after_replace(self):
        seen=[];original=worker.os.fsync
        def record(fd):seen.append(os.fstat(fd).st_mode);original(fd)
        with patch.object(worker.os,'fsync',side_effect=record):worker.publish({},self.snapshot)
        self.assertTrue(any(stat.S_ISREG(mode) for mode in seen))
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in seen))

    def test_cache_budget_keeps_newest_whole_entries_in_memory_and_disk(self):
        self.response='echo';w=worker.Worker(self.snapshot,call=self.call)
        with patch.object(worker,'MAX_CACHE_BYTES',2400),patch.object(worker,'MAX_SNAPSHOTS',3):
            for number in range(8):
                self.attach(ticket_id=f'gorgias:{number+1}',now=1000+number)
                rows=[row for row in worker.read_requests(self.queue,1001+number) if row[0]['ticketId']==f'gorgias:{number+1}']
                self.assertEqual(w.process(rows,1001+number),1)
            disk=worker.load_cache(self.snapshot)
            self.assertEqual(w.cache,disk);self.assertEqual(set(w.cache),{'gorgias:6','gorgias:7','gorgias:8'})
            self.assertLessEqual(sum(worker.serialized_size(k,v) for k,v in w.cache.items()),2400)
        cache={f'gorgias:{n}':{'payload':{'text':'x'*700000},'updated_at':n} for n in range(5)}
        retained=worker.bounded_cache(cache)
        self.assertEqual(set(retained),{'gorgias:3','gorgias:4'})
        self.assertLessEqual(sum(worker.serialized_size(k,v) for k,v in retained.items()),worker.MAX_CACHE_BYTES)

    def test_restart_trims_legacy_rows_before_loading_oversized_payload(self):
        with closing(sqlite3.connect(self.snapshot)) as db:
            db.execute('CREATE TABLE redo(ticket_id TEXT PRIMARY KEY,payload TEXT,updated_at REAL)')
            for number in range(6):db.execute('INSERT INTO redo VALUES(?,?,?)',(str(number),json.dumps({'text':'x'*700000}),number))
            db.execute('INSERT INTO redo VALUES(?,?,?)',('too-big',json.dumps({'text':'x'*(worker.MAX_CACHE_BYTES+1)}),100))
            db.commit()
        sizes=[];original=worker.json.loads
        def loaded(payload):sizes.append(len(payload.encode()));return original(payload)
        with patch.object(worker.json,'loads',side_effect=loaded):w=worker.Worker(self.snapshot,call=self.call)
        self.assertTrue(sizes);self.assertLessEqual(max(sizes),worker.MAX_CACHE_BYTES)
        self.assertEqual(set(w.cache),{'4','5'});self.assertEqual(w.cache,worker.load_cache(self.snapshot))
        with closing(sqlite3.connect(self.snapshot)) as db:self.assertEqual(db.execute('SELECT count(*) FROM redo').fetchone()[0],2)

    def test_stop_between_orders_discards_incomplete_refresh(self):
        orders=('#10312345','#10312346','#10312347');self.response='echo';self.attach(orders);w=self.run_worker()
        old=copy.deepcopy(w.cache);disk=self.snapshot.read_bytes();self.attach(orders,now=3000);self.calls.clear()
        original=self.call
        def stopped(order):
            result=original(order);worker.STOP.set();return result
        w.call=stopped
        self.assertEqual(w.process(worker.read_requests(self.queue,3001),3001),0)
        self.assertEqual(self.calls,['10312345']);self.assertEqual(w.cache,old);self.assertEqual(self.snapshot.read_bytes(),disk)
        worker.STOP.clear();w.call=original
        self.assertEqual(w.process(worker.read_requests(self.queue,3002),3002),1)
        self.assertEqual(self.calls,['10312345','10312345','10312346','10312347'])

    def test_empty_observed_structures_are_not_unavailable_but_filtered_nonempty_are(self):
        empty=worker.summarize('10312345',{'returns':[{**RETURN,'refunds':[],'totals':{}}]},1000)['returns'][0]
        self.assertEqual(empty['structuredFieldsUnavailable'],[])
        filtered=worker.summarize('10312345',{'returns':[{**RETURN,'refunds':[{'private':'hidden'}],'totals':{'unknown':'hidden'}}]},1000)['returns'][0]
        self.assertEqual(set(filtered['structuredFieldsUnavailable']),{'refunds','totals'})
        unsupported=worker.summarize('10312345',{'returns':[{**RETURN,'refunds':'unsupported'}]},1000)['returns'][0]
        self.assertIn('refunds',unsupported['structuredFieldsUnavailable'])

if __name__ == '__main__':
    unittest.main()
