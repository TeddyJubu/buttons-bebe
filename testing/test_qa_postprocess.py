import copy
import unittest
from qa_postprocess import replay

class PostprocessTests(unittest.TestCase):
    def test_recorded_model_output_is_revalidated_without_another_model_call(self):
        import json
        record=self.record('What is the sleeve length of this dress?')
        record['scenario']['intent']='product'
        verdict=dict(priority='normal',reason='Need exact product',action='no_kb_match',
                     notify_owner=False,review_required=False,missing_facts=[],staff_next_step='')
        token='0123456789abcdef'
        record.update(model_called=True,process_returncode=0,authenticated_verdict=True,
            run_token=token,hermes_output=f'<DRAFT:{token}>Could you share the product link?</DRAFT:{token}>\nJSON_RESULT[{token}]: '+json.dumps(verdict))
        record['result'].update(generation_state='needs_review',review_required=True)
        original=copy.deepcopy(record)
        result=replay([record],reparse=True)['records'][0]
        self.assertEqual(record,original)
        self.assertEqual(result['console_result']['generation_state'],'ready')
        self.assertEqual(result['console_draft'],'Could you share the product link?')
        self.assertTrue(result['reparsed_stdout'])
        record['run_token']='f'*16
        with self.assertRaises(RuntimeError): replay([record],reparse=True)

    def record(self, message='Please cancel order #10361 before shipping.'):
        return {'id':'QA-1','scenario':{'id':'QA-1','subject':'Order request','message':message,'email':'qa@example.com','intent':'cancel'},
                'result':{'priority':'normal','action':'drafted','notify_owner':False,'draft_text':'Your cancellation request needs staff approval.','gorgias_priority_set':False,'note_posted':False}}

    def test_real_classifier_escalates_and_console_prefixes_without_mutating_raw(self):
        record=self.record(); original=copy.deepcopy(record)
        output=replay([record]); final=output['records'][0]
        self.assertEqual(record,original)
        self.assertEqual(final['payload_scope']['intent_names'],['cancel'])
        self.assertEqual(final['console_result']['action'],'sensitive_draft')
        self.assertIn(final['console_result']['priority'],{'high','critical'})
        self.assertTrue(final['console_result']['notify_owner'])
        self.assertTrue(final['console_draft'].startswith('[SENSITIVE'))
        self.assertEqual(final['captured_persistence_calls'],1)
        self.assertIn('processor/orchestrator.py',output['source_sha256'])
        self.assertIn('processor/draft_cleaner.py',output['source_sha256'])

    def test_rejects_non_synthetic_email(self):
        record=self.record(); record['scenario']['email']='real@customer.invalid'
        with self.assertRaises(RuntimeError): replay([record])

    def test_worker_audit_blocks_network_database_process_and_credentials(self):
        import json, os, subprocess, sys, tempfile
        from pathlib import Path
        from qa_harness import minimal_environment
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'input.json'; source.write_text(json.dumps([self.record()]))
            code='''import sys,json,socket,sqlite3,subprocess
sys.path.insert(0,sys.argv[1])
from qa_postprocess import worker
worker(sys.argv[2])
checks=[lambda:socket.create_connection(('127.0.0.1',8000)), lambda:sqlite3.connect(':memory:'), lambda:subprocess.run(['/bin/true']), lambda:open('.env')]
for check in checks:
 try:check()
 except RuntimeError:pass
 else:raise AssertionError('guard did not reject')
print('guards-ok')
'''
            result=subprocess.run([sys.executable,'-I','-c',code,str(Path(__file__).parent),str(source)],env=minimal_environment(root),cwd=root,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'guards-ok')

    def test_absent_intent_replays_same_empty_name_as_runner(self):
        record=self.record(); del record['scenario']['intent']
        final=replay([record])['records'][0]
        self.assertEqual(final['payload_scope']['intent_names'],[''])
        self.assertEqual(final['console_result']['action'],'sensitive_draft')
