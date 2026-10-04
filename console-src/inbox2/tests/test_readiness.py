"""Synthetic local readiness and worker faults. No providers or private data."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_api as target


def stamp(seconds=0):
    return datetime.fromtimestamp(time.time()-1-seconds, timezone.utc).isoformat()


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.live=self.root/'live.sqlite3';self.projection=self.root/'projection.sqlite3'
        self.worker=target.Worker();self.worker.thread=MagicMock()
        self.worker.thread.is_alive.return_value=True
        self.worker.update('sleeping')
        for mocked in (patch.object(target,'DB',self.live),patch.object(target,'WORKER',self.worker),
                       patch.dict(target.os.environ,{'INBOX_PROJECTION_PATH':str(self.projection)}),
                       patch.object(target,'MCP',side_effect=AssertionError('Readiness must not call MCP'))):
            mocked.start();self.addCleanup(mocked.stop)
        self.client=TestClient(target.app)
        with closing(sqlite3.connect(self.live)) as db,db:
            db.execute('CREATE TABLE tickets(id TEXT PRIMARY KEY, updated REAL, payload TEXT, generation TEXT)')
            db.execute('CREATE TABLE meta(id INTEGER PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO meta VALUES(1,?)',(json.dumps({'complete':True,'lastCompletedAt':stamp()}),))
        with closing(sqlite3.connect(self.projection)) as db,db:
            db.execute('CREATE TABLE tickets(id TEXT PRIMARY KEY,observed_at TEXT,summary TEXT,detail TEXT)')
            db.execute('CREATE TABLE metadata(id INTEGER PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO metadata VALUES(1,?)',(json.dumps({'version':1,'generatedAtEpoch':time.time()-1}),))

    def meta(self, **values):
        with closing(sqlite3.connect(self.live)) as db,db:
            meta=target.get_meta(db);meta.update(values);target.set_meta(db,meta)

    def projection_meta(self, **values):
        with closing(sqlite3.connect(self.projection)) as db,db:
            meta=json.loads(db.execute('SELECT payload FROM metadata').fetchone()[0]);meta.update(values)
            db.execute('UPDATE metadata SET payload=?',(json.dumps(meta),))

    def test_completed_empty_snapshots_are_ready_and_liveness_is_constant(self):
        result=self.client.get('/ready')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['checks'],{'storage':'ok','worker':'ok','ticketData':'fresh','projection':'fresh'})
        self.assertEqual(self.client.get('/health').json(),{'ok':True,'readOnly':True})
        self.assertEqual(result.headers['cache-control'],'no-store')
        self.assertEqual(result.json()['diagnostics']['workerId'],self.worker.worker_id)

    def test_missing_database_is_unusable_and_is_never_created(self):
        self.live.unlink()
        result=self.client.get('/ready')
        self.assertEqual(result.status_code,503);self.assertEqual(result.json()['status'],'unusable')
        self.assertFalse(self.live.exists())

    def test_missing_projection_or_schema_is_unusable(self):
        for table,path in [('tickets',self.projection),('meta',self.live)]:
            with closing(sqlite3.connect(path)) as db,db:db.execute('DROP TABLE '+table)
            result=self.client.get('/ready')
            self.assertEqual(result.status_code,503);self.assertEqual(result.json()['status'],'unusable')
        self.projection.unlink()
        self.assertEqual(self.client.get('/ready').json()['status'],'unusable')
        self.assertFalse(self.projection.exists())

    def test_oversized_metadata_is_unusable(self):
        for text in ['x'*65537,'\U0001f600'*20000]:
            with self.subTest(text_length=len(text)):
                with closing(sqlite3.connect(self.live)) as db,db:
                    db.execute('UPDATE meta SET payload=?',(json.dumps({'padding':text},ensure_ascii=False),))
                result=self.client.get('/ready')
                self.assertEqual(result.status_code,503);self.assertEqual(result.json()['status'],'unusable')
                self.assertNotIn('padding',result.text)

    def test_locked_storage_finishes_with_a_bounded_safe_failure(self):
        with closing(sqlite3.connect(self.live)) as db:
            db.execute('BEGIN EXCLUSIVE')
            started=time.monotonic();result=self.client.get('/ready')
            self.assertLess(time.monotonic()-started,1)
            self.assertEqual(result.status_code,503);self.assertEqual(result.json()['status'],'unusable')
            self.assertNotIn(str(self.root),result.text)

    def test_first_and_resumed_partial_scan_cannot_pass_on_a_fresh_head(self):
        self.meta(complete=False,generatedAt=stamp(),lastPageAt=stamp(),lastHeadAt=stamp(),
                  pendingFull={'cursor':'synthetic','generation':'test'})
        self.assertEqual(self.client.get('/ready').json()['status'],'unusable')
        self.meta(complete=True,lastCompletedAt=stamp(21600))
        self.assertEqual(self.client.get('/ready').json()['status'],'ready')
        self.meta(lastPageAt=stamp(300),generatedAt=stamp(),lastHeadAt=stamp())
        result=self.client.get('/ready')
        self.assertEqual(result.status_code,503);self.assertEqual(result.json()['status'],'degraded')
        self.assertEqual(result.json()['checks']['ticketData'],'stale')

    def test_invalid_missing_and_future_completed_times_fail_closed(self):
        for value in [None,'nonsense','2026-01-01T00:00:00',datetime.fromtimestamp(time.time()+60,timezone.utc).isoformat()]:
            with self.subTest(value=value):
                self.meta(lastCompletedAt=value)
                self.assertEqual(self.client.get('/ready').json()['status'],'unusable')

    def test_stale_data_and_export_errors_are_degraded(self):
        self.meta(lastCompletedAt=stamp(300))
        self.assertEqual(self.client.get('/ready').json()['status'],'degraded')
        self.meta(lastCompletedAt=stamp())
        self.projection_meta(generatedAtEpoch=time.time()-400)
        self.assertEqual(self.client.get('/ready').json()['checks']['projection'],'stale')
        self.projection_meta(generatedAtEpoch=time.time()-1)
        self.projection.with_suffix('.error').write_text('synthetic private upstream detail')
        result=self.client.get('/ready')
        self.assertEqual(result.json()['status'],'degraded')
        self.assertNotIn('private',result.text)

    def test_invalid_projection_timestamps_are_unusable(self):
        for value in [None,'bad',True,float('nan'),time.time()+60]:
            with self.subTest(value=value):
                self.projection_meta(generatedAtEpoch=value)
                self.assertEqual(self.client.get('/ready').json()['status'],'unusable')

    def test_dead_stuck_and_error_workers_remain_visible_with_readable_data(self):
        self.worker.thread.is_alive.return_value=False
        self.assertEqual(self.client.get('/ready').json()['status'],'degraded')
        self.worker.thread.is_alive.return_value=True
        for phase in ['capacity','transport','writing','scanning','sleeping','backoff']:
            with self.subTest(phase=phase):
                self.worker.update(phase)
                self.worker.phase_since=time.monotonic()-target.PHASE_LIMITS[phase]-1
                self.assertEqual(self.client.get('/ready').json()['checks']['worker'],'stuck')
        self.worker.update('backoff',error=True,metadata_error=True)
        result=self.client.get('/ready').json()
        self.assertEqual(result['checks']['worker'],'error')
        self.assertTrue(result['diagnostics']['errorMetadataUnavailable'])

    def test_recent_transport_phase_cannot_hide_stalled_progress(self):
        self.worker.update('transport')
        self.worker.progress_since=time.monotonic()-121
        self.assertEqual(self.client.get('/ready').json()['checks']['worker'],'stuck')

    def test_normal_backoff_is_not_mislabeled_as_a_stuck_thread(self):
        self.worker.update('backoff',error=True)
        self.worker.phase_since=time.monotonic()-299
        self.assertEqual(self.client.get('/ready').json()['checks']['worker'],'error')

    def test_first_sync_failure_retries_in_five_seconds_and_stays_unready_until_success(self):
        client=MagicMock();client.__enter__.return_value=client
        observations=[];reads=[]
        def read(_tool,_arguments):
            reads.append(target.assess_readiness()['checks']['worker'])
            if len(reads)==1:raise target.Unavailable()
            return {'data':[]}
        def wait(delay):
            response=self.client.get('/ready')
            observations.append((delay,response.status_code,response.json()['checks']['worker']))
            if delay==30:self.worker.stop.set()
            return self.worker.stop.is_set()
        client.call.side_effect=read
        with patch.object(target,'MCP',return_value=client),\
             patch.object(self.worker.stop,'wait',side_effect=wait):
            target.sync_loop(self.worker)
        self.assertEqual(observations,[(5,503,'error'),(30,200,'ok')])
        self.assertEqual(reads,['ok','error'])
        self.assertEqual(client.call.call_count,2)
        self.assertFalse(self.worker.error)
        self.assertIsNotNone(self.worker.success_at)
        with closing(sqlite3.connect(self.live)) as db:
            self.assertFalse(target.get_meta(db)['error'])


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for mocked in (patch.object(target,'DB',self.root/'live.sqlite3'),
                       patch.object(target.customer_details,'database',side_effect=AssertionError('No Shopify initialization')),
                       patch.object(target,'WORKER',None)):
            mocked.start();self.addCleanup(mocked.stop)
        with closing(target.database()) as db,db:
            db.execute('CREATE TABLE tickets(id TEXT PRIMARY KEY,updated REAL,payload TEXT,generation TEXT)')
            db.execute('CREATE TABLE meta(id INTEGER PRIMARY KEY,payload TEXT)')
            db.execute('INSERT INTO meta VALUES(1,?)',(json.dumps({'complete':True,'lastCompletedAt':stamp(),
                      'fullAt':time.time(),'generation':'old'}),))
            db.execute('INSERT INTO tickets VALUES(?,?,?,?)',('gorgias:1',1,'{}','old'))

    def test_failed_scan_keeps_page_commits_and_completion_timestamp(self):
        with closing(target.database()) as db:before=target.get_meta(db)['lastCompletedAt']
        client=MagicMock();client.__enter__.return_value=client
        client.call.side_effect=[{'data':[{'id':2,'updated_datetime':stamp()}],'meta':{'next_cursor':'next'}},target.Unavailable()]
        worker=target.Worker()
        with closing(target.database()) as db,db:
            meta=target.get_meta(db);meta['fullAt']=0;target.set_meta(db,meta)
        with patch.object(target,'MCP',return_value=client),patch.object(worker.stop,'wait',return_value=False):
            with self.assertRaises(target.Unavailable):target.sync_once(worker)
        with closing(target.database()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tickets').fetchone()[0],2)
            meta=target.get_meta(db)
        self.assertEqual(meta['lastCompletedAt'],before)
        self.assertEqual(meta['pendingFull']['cursor'],'next')

    def test_metadata_error_does_not_kill_worker_and_later_completion_clears_flags(self):
        worker=target.Worker();calls=[];original_sync=target.sync_once;original_set=target.set_meta
        client=MagicMock();client.__enter__.return_value=client;client.call.return_value={'data':[]}
        def persist(db,meta):
            if meta.get('error'):raise sqlite3.OperationalError('synthetic metadata failure')
            original_set(db,meta)
        def sync(state):
            calls.append(state.snapshot())
            if len(calls)==1:raise target.Unavailable()
            original_sync(state);state.stop.set()
        with patch.object(target,'sync_once',side_effect=sync),patch.object(target,'set_meta',side_effect=persist),\
             patch.object(target,'MCP',return_value=client),patch.object(worker.stop,'wait',return_value=False):
            target.sync_loop(worker)
        self.assertEqual(len(calls),2)
        self.assertTrue(calls[1]['error']);self.assertTrue(calls[1]['metadataError'])
        self.assertFalse(worker.error);self.assertFalse(worker.metadata_error)
        with closing(target.database()) as db:
            self.assertFalse(target.get_meta(db)['error'])
            self.assertEqual(db.execute('SELECT count(*) FROM tickets').fetchone()[0],1)

    def test_repeated_failures_get_only_one_quick_retry_before_capped_backoff(self):
        worker=target.Worker();delays=[]
        with closing(target.database()) as db:before=target.get_meta(db)['lastCompletedAt']
        def wait(delay):
            delays.append(delay)
            self.assertTrue(worker.error)
            if len(delays)==6:worker.stop.set()
            return worker.stop.is_set()
        with patch.object(target,'sync_once',side_effect=target.Unavailable) as sync,\
             patch.object(worker.stop,'wait',side_effect=wait):
            target.sync_loop(worker)
        self.assertEqual(delays,[5,60,120,240,300,300])
        self.assertEqual(sync.call_count,6)
        self.assertIsNone(worker.success_at)
        with closing(target.database()) as db:
            meta=target.get_meta(db)
        self.assertTrue(meta['error'])
        self.assertEqual(meta['lastCompletedAt'],before)

    def test_success_resets_the_quick_retry_for_the_next_failure_streak(self):
        worker=target.Worker();delays=[];calls=[];original_sync=target.sync_once
        client=MagicMock();client.__enter__.return_value=client;client.call.return_value={'data':[]}
        def sync(state):
            calls.append(state.snapshot())
            if len(calls)!=3:raise target.Unavailable()
            original_sync(state)
        def wait(delay):
            delays.append(delay)
            if len(delays)==5:worker.stop.set()
            return worker.stop.is_set()
        with patch.object(target,'sync_once',side_effect=sync),patch.object(target,'MCP',return_value=client),\
             patch.object(worker.stop,'wait',side_effect=wait):
            target.sync_loop(worker)
        self.assertEqual(delays,[5,60,30,5,60])
        self.assertEqual(len(calls),5)
        self.assertTrue(calls[2]['error'])
        self.assertFalse(calls[3]['error'])
        self.assertTrue(calls[4]['error'])

    def test_capacity_wait_is_cancelled_without_opening_transport(self):
        capacity=threading.BoundedSemaphore(1);capacity.acquire()
        worker=target.Worker();errors=[]
        def run():
            try:
                with target.MCP(worker):pass
            except Exception as exc:errors.append(exc)
        with patch.object(target,'CAPACITY',capacity),patch.object(target.urllib.request,'build_opener') as opener:
            thread=threading.Thread(target=run);thread.start()
            deadline=time.monotonic()+1
            while worker.phase!='capacity' and time.monotonic()<deadline:time.sleep(.001)
            worker.stop.set();thread.join(1)
            self.assertFalse(thread.is_alive());opener.assert_not_called()
        self.assertIsInstance(errors[0],target.Cancelled)
        capacity.release()

    def test_capacity_wait_has_a_deadline(self):
        capacity=threading.BoundedSemaphore(1);capacity.acquire()
        with patch.object(target,'CAPACITY',capacity),patch.object(target,'CAPACITY_TIMEOUT',.01):
            with self.assertRaises(target.Unavailable):
                with target.MCP(target.Worker()):pass
        capacity.release()

    def test_shutdown_join_timeout_prevents_second_worker_and_new_lifespan_owns_new_event(self):
        release=threading.Event()
        def blocked(worker):release.wait(2)
        with patch.object(target,'init_db'),patch.object(target,'sync_loop',side_effect=blocked),\
             patch.object(target,'SHUTDOWN_TIMEOUT',.01):
            first=target.start_worker()
            try:
                target.stop_worker(first)
                self.assertTrue(first.stop.is_set());self.assertTrue(first.shutdown_timeout)
                with self.assertRaisesRegex(RuntimeError,'Previous'):target.start_worker()
            finally:release.set();first.thread.join(1)
        def cooperative(worker):worker.stop.wait(2)
        with patch.object(target,'init_db'),patch.object(target,'sync_loop',side_effect=cooperative):
            second=target.start_worker();target.stop_worker(second)
            self.assertIsNot(first.stop,second.stop)
            self.assertFalse(second.thread.is_alive())
            self.assertTrue(first.stop.is_set())

    def test_lifespan_finally_stops_exact_worker_after_exception(self):
        import asyncio
        worker=target.Worker();worker.thread=MagicMock();worker.thread.is_alive.return_value=False
        async def run():
            with self.assertRaisesRegex(ValueError,'synthetic'):
                async with target.lifespan(target.app):raise ValueError('synthetic')
        with patch.object(target,'start_worker',return_value=worker):asyncio.run(run())
        self.assertTrue(worker.stop.is_set());worker.thread.join.assert_called_once_with(target.SHUTDOWN_TIMEOUT)


if __name__=='__main__':unittest.main()
