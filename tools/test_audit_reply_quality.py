import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('audit_reply_quality',Path(__file__).with_name('audit_reply_quality.py'))
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


class QualityAuditTests(unittest.TestCase):
    def test_read_only_next_results_exclude_acknowledgments_from_generation_rate(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE draft_generation_attempts(id INTEGER,job_id INTEGER,ticket_id INTEGER,outcome TEXT,finished_at TEXT,error_code TEXT,result_json TEXT)')
                db.execute('CREATE TABLE owner_alert_attempts(job_id INTEGER,status TEXT)')
                for n,state in enumerate(['ready','retry_wait','failed','no_reply','needs_review','ready'],1):
                    db.execute('INSERT INTO draft_generation_attempts VALUES(?,?,?,?,?,?,?)',(n,n,n,state,'now',None,json.dumps({'result':{'draft_text':'Synthetic reply'}})))
                db.execute("INSERT INTO owner_alert_attempts VALUES(6,'accepted')")
            before=path.read_bytes();report=audit.collect(path,1)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(report['observed_results'],4)
            self.assertEqual(report['generation_success_percent'],66.67)
            self.assertFalse(report['collection_complete'])
            self.assertFalse(report['generation_target_met'])
            self.assertEqual(report['records'][-1]['notification_transport'],'accepted')
            self.assertEqual(report['records'][-1]['owner_delivery'],'unconfirmed')
            self.assertIsNone(report['records'][-1]['review']['usable_answer'])
