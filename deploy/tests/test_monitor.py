"""Synthetic monitor faults: no live services, databases, or alerts."""
from datetime import datetime,timedelta,timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('ops_monitor',ROOT/'tools/ops/monitor.py')
target=importlib.util.module_from_spec(spec);spec.loader.exec_module(target)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=datetime(2026,9,7,tzinfo=timezone.utc)
        for mocked in (patch.object(target,'STATUS',self.root/'status.json'),patch.object(target,'BACKUP',self.root/'backup.json')):
            mocked.start();self.addCleanup(mocked.stop)

    def test_backup_distinguishes_missing_corrupt_failed_stale_future_and_current(self):
        self.assertEqual(target.backup(self.now),'missing')
        cases=[({'status':'failed'},'attention'),({'status':'ok'},'missing'),
               ({'status':'ok','last_success':(self.now-timedelta(hours=9)).isoformat()},'stale'),
               ({'status':'ok','last_success':(self.now+timedelta(hours=1)).isoformat()},'unavailable'),
               ({'status':'ok','last_success':self.now.isoformat()},'ok')]
        for state,expected in cases:
            target.BACKUP.write_text(json.dumps(state));self.assertEqual(target.backup(self.now),expected)
        target.BACKUP.write_text('corrupt')
        self.assertEqual(target.safe(lambda:target.backup(self.now)),'unavailable')

    def test_latest_failed_backup_job_is_not_hidden_by_active_timer(self):
        with patch.object(target,'command',return_value=(0,'exit-code')):
            self.assertEqual(target.last_result('buttonsbebe-backup'),'unavailable')
        with patch.object(target,'command',return_value=(0,'success')):
            self.assertEqual(target.last_result('buttonsbebe-backup'),'ok')

    def test_progress_requires_completion_markers_not_any_journal_activity(self):
        with patch.object(target,'command',return_value=(0,'')) as command:
            self.assertEqual(target.progress(),'stale')
            self.assertIn('Processor idle heartbeat|Job completed',command.call_args.args)
            self.assertNotIn('Job processor starting',command.call_args.args)
        with patch.object(target,'command',return_value=(1,'')):self.assertEqual(target.progress(),'unavailable')
        with patch.object(target,'command',return_value=(0,json.dumps({'level':'INFO','logger':'orchestrator','msg':'Job completed'}))):self.assertEqual(target.progress(),'ok')

    def test_error_containing_marker_is_not_loop_progress(self):
        output=json.dumps({'level':'ERROR','logger':'orchestrator','msg':'Customer said Job completed'})
        with patch.object(target,'command',return_value=(0,output)):
            self.assertEqual(target.progress(),'stale')

    def test_queue_readiness_distinguishes_missing_counts_and_old_work(self):
        payload={'status':'ready'}
        response=MagicMock();response.__enter__.return_value=response
        with patch.object(target.urllib.request,'urlopen',return_value=response):
            response.read.return_value=json.dumps(payload).encode()
            self.assertEqual(target.readiness(8000),'unavailable')
            payload['diagnostics']={'pending_jobs':0,'processing_jobs':0}
            response.read.return_value=json.dumps(payload).encode()
            self.assertEqual(target.readiness(8000),'ok')
            payload['diagnostics'].update(pending_jobs=1,oldest_pending_seconds=1900)
            response.read.return_value=json.dumps(payload).encode()
            self.assertEqual(target.readiness(8000),'attention')
            payload['diagnostics']['oldest_pending_seconds']=None
            response.read.return_value=json.dumps(payload).encode()
            self.assertEqual(target.readiness(8000),'attention')

    def test_inbox_monitor_checks_the_active_readonly_service(self):
        response=MagicMock();response.__enter__.return_value=response
        with patch.object(target.urllib.request,'urlopen',return_value=response) as request:
            response.read.return_value=b'{"ok":true,"readOnly":true}'
            self.assertEqual(target.readiness(8767),'ok')
            self.assertEqual(request.call_args.args[0], 'http://127.0.0.1:8767/health')
            response.read.return_value=b'{"ok":true,"readOnly":false}'
            self.assertEqual(target.readiness(8767),'unavailable')
        self.assertIn('helpdesk-inbox2',target.SERVICES)
        self.assertIn('buttonsbebe-inbox2-shop',target.SERVICES)
        self.assertNotIn('helpdesk-inbox',target.SERVICES)

    def test_component_failure_cannot_be_hidden_by_other_healthy_services(self):
        with patch.object(target,'active',return_value='ok'),patch.object(target,'last_result',return_value='ok'),patch.object(target,'tcp',return_value='ok'),patch.object(target,'readiness',return_value='ok'),patch.object(target,'backup',return_value='ok'),patch.object(target,'disk',return_value='ok'),patch.object(target,'progress',return_value='stale'):
            result=target.collect(self.now)
        self.assertEqual(result['status'],'attention');self.assertEqual(result['checks']['processor_progress'],'stale')
        self.assertEqual(result['notification_transport'],'local_only')

    def test_failure_details_are_not_written_to_status(self):
        with patch.object(target,'active',side_effect=RuntimeError('synthetic-secret')),patch.object(target,'last_result',return_value='ok'),patch.object(target,'tcp',return_value='ok'),patch.object(target,'readiness',return_value='ok'),patch.object(target,'backup',return_value='ok'),patch.object(target,'disk',return_value='ok'),patch.object(target,'progress',return_value='ok'):
            result=target.collect(self.now);target.write(result)
        text=target.STATUS.read_text();self.assertNotIn('synthetic-secret',text)
        self.assertEqual(json.loads(text)['status'],'attention')
        self.assertEqual(target.STATUS.stat().st_mode & 0o777,0o600)


if __name__=='__main__':unittest.main()
