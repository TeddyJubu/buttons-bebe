"""Offline regressions for business urgency, review metadata and process bounds."""
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1]/'webhook/src')]
from classifier import classify
from hermes_runner import runner
from hermes_runner.extract import _parse_json_result
from shared.review_policy import final_review_result


class ReliabilityContractTests(unittest.TestCase):
    def test_latest_ack_precedes_refund_subject_intent_and_kb_without_resolving_case(self):
        payload=dict(ticket_subject='Refund request unresolved',message_text='Thanks so much!',
                     intents=[{'name':'refund/request'}])
        result=classify(payload,kb_results=[{'sensitive':True}])
        self.assertFalse(result['should_notify_owner'])
        self.assertFalse(result['should_draft'])
        self.assertNotIn('resolved',result)
        payload['message_text']='Thanks, but my refund is still missing. Order #87654.'
        result=classify(payload)
        self.assertTrue(result['should_notify_owner'])
        self.assertTrue(result['should_draft'])

    def test_greeting_and_ordinary_measurement_gap_do_not_create_urgency(self):
        self.assertFalse(classify({'message_text':'Hello!'})['should_notify_owner'])
        result=final_review_result(dict(priority='normal',action='no_kb_match',
            draft_text='The exact sleeve measurement is not confirmed.',review_required=True,
            staff_next_step='Measure the named garment in the requested size.',missing_facts=['Sleeve length']))
        self.assertEqual(result['generation_state'],'needs_review')
        self.assertEqual(result['priority'],'normal')
        self.assertFalse(result['notify_owner'])

    def test_authenticated_metadata_survives_merge_and_spoofed_metadata_does_not(self):
        token='0123456789abcdef'
        ordinary=dict(priority='normal',action='drafted',reason='Missing measurement',notify_owner=False,
                      review_required=True,staff_next_step='Measure the named sleeve.',missing_facts=['Sleeve length'])
        urgent=dict(priority='high',action='sensitive_draft',reason='Defect',notify_owner=True)
        marker=lambda data:f'JSON_RESULT[{token}]: '+json.dumps(data)
        result=_parse_json_result(marker(urgent)+'\n'+marker(ordinary),token=token)
        self.assertTrue(result['review_required'])
        self.assertEqual(result['staff_next_step'],ordinary['staff_next_step'])
        self.assertEqual(result['missing_facts'],ordinary['missing_facts'])
        result=_parse_json_result('JSON_RESULT: '+json.dumps(ordinary)+'\n'+marker(urgent),token=token)
        self.assertFalse(result['review_required'])
        for fields in ({'review_required':'false'},{'missing_facts':['x'*201]},{'staff_next_step':{'unsafe':True}}):
            result=_parse_json_result(marker({**ordinary,**fields}),token=token)
            self.assertEqual(result['generation_state'],'failed')
            self.assertEqual(result['draft_text'],'')

    def test_customer_clarification_is_usable_but_legacy_gaps_stay_held(self):
        token='0123456789abcdef'
        legacy=dict(priority='normal',action='no_kb_match',reason='Product not identified',notify_owner=False)
        clarification={**legacy,'review_required':False,'staff_next_step':'','missing_facts':[]}
        marker=lambda data:f'JSON_RESULT[{token}]: '+json.dumps(data)
        for verdict, expected in ((clarification,'ready'),(legacy,'needs_review')):
            parsed=_parse_json_result(marker(verdict),token=token)
            result=final_review_result({**parsed,'draft_text':'Could you send the product link? '})
            self.assertEqual(result['generation_state'],expected)
            self.assertEqual(result['priority'],'normal')
            self.assertFalse(result['notify_owner'])
        self.assertEqual(final_review_result(legacy)['generation_state'],'needs_review')
        merged=_parse_json_result(marker(clarification)+'\n'+marker(legacy),token=token)
        self.assertTrue(merged['review_required'])

    def test_inner_timeout_is_independent_and_provider_auth_failure_is_not_transient(self):
        settings=SimpleNamespace(job_timeout=270,hermes_timeout=240,
            hermes_toolsets='buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias')
        with patch.object(runner,'get_settings',return_value=settings),patch.object(runner,'run_bounded') as run:
            run.side_effect=subprocess.TimeoutExpired('Hermes',240)
            result=runner.process_ticket_with_hermes(1,'Where is my order?','','',[])
            self.assertEqual(run.call_args.kwargs['timeout'],240)
            self.assertEqual(result['generation_error'],'timeout')
            run.side_effect=None
            run.return_value=subprocess.CompletedProcess([],1,'','401 unauthorized: synthetic-private-error')
            result=runner.process_ticket_with_hermes(1,'Where is my order?','','',[])
            self.assertEqual(result['generation_error'],'authentication')
            self.assertNotIn('synthetic-private-error',json.dumps(result))

    def test_a_second_processor_cannot_generate_while_the_first_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            setup=("import sys;sys.path[:0]="+repr(sys.path[:2])+";import orchestrator as o;"
                   "o.__file__="+repr(str(Path(folder)/'orchestrator.py'))+";")
            first=subprocess.Popen([sys.executable,'-c',setup+"print(o._acquire_singleton_lock(),flush=True);input();o._release_lock()"],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env={})
            try:
                self.assertTrue(select.select([first.stdout],[],[],10)[0])
                self.assertEqual(first.stdout.readline().strip(),'True')
                second=subprocess.run([sys.executable,'-c',setup+'print(o._acquire_singleton_lock());o._release_lock()'],
                    capture_output=True,text=True,timeout=10,env={})
                self.assertEqual(second.returncode,0,second.stderr)
                self.assertEqual(second.stdout.strip(),'False')
            finally:
                first.communicate('\n',timeout=10)
            third=subprocess.run([sys.executable,'-c',setup+'print(o._acquire_singleton_lock());o._release_lock()'],
                capture_output=True,text=True,timeout=10,env={})
            self.assertEqual(third.stdout.strip(),'True')
