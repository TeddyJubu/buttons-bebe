"""Synthetic filesystem/service adapters; never call live WhatsApp or systemd."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent/'ops'))
import whatsapp_dependency_switch as switch

class WhatsAppSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.live=self.root/'live';self.live.mkdir();self.candidate=self.root/'candidate';self.candidate.mkdir();self.backups=self.root/'backups';self.backups.mkdir()
        self.lock=self.root/'deploy.lock';(self.live/'auth').mkdir()
        source=b'const BASE = "/synthetic-private-path";\nconsole.log(`whatsapp-connect listening on 127.0.0.1:${PORT} base=${BASE}`);\n'
        (self.live/'server.js').write_bytes(source)
        for directory,version in ((self.live,'6.15.3'),(self.candidate,'6.16.0')):
            (directory/'package.json').write_text(json.dumps({'dependencies':{'qs':version}}))
            (directory/'package-lock.json').write_text(json.dumps({'packages':{'node_modules/qs':{'version':version},'node_modules/@whiskeysockets/baileys':{'version':'6.7.23'}}}))
            (directory/'node_modules').mkdir();(directory/'node_modules/identity').write_text(version)
        self.plan=switch.inventory(self.live,self.candidate);self.active=True;self.calls=[]
        self.original={name:(self.live/name).read_bytes() for name in switch.FILES}
    def service(self,action):
        self.calls.append(action)
        if action=='is-active':return self.active
        self.active=action=='start'
    def run_switch(self,ready=None,state=lambda:'qr'):
        return switch.apply(self.plan,self.live,self.candidate,self.backups,self.lock,self.service,state,ready or (lambda service,state:None))
    def test_success_only_changes_reviewed_modules_manifests_and_log(self):
        result=self.run_switch()
        self.assertEqual(result['status'],'verified');self.assertFalse(result['auth_state_touched'])
        self.assertEqual((self.live/'node_modules/identity').read_text(),'6.16.0')
        self.assertEqual((self.live/'server.js').read_bytes(),self.original['server.js'].replace(b' base=${BASE}',b''))
        self.assertEqual(list((self.live/'auth').iterdir()),[])
        backup=next(self.backups.iterdir());self.assertEqual((backup/'node_modules/identity').read_text(),'6.15.3')
        self.assertEqual(json.loads((backup/'switch.json').read_text())['phase'],'verified')
        self.assertEqual([x for x in self.calls if x!='is-active'],['stop','start'])
    def test_failed_candidate_restores_original_files_modules_and_preserves_new_auth(self):
        attempts=0
        def ready(service,state):
            nonlocal attempts
            attempts+=1
            if attempts==1:
                # Simulate auth data arriving independently; rollback must never delete it.
                (self.live/'auth/new-session').write_text('synthetic session')
                raise RuntimeError('candidate not ready')
        with self.assertRaisesRegex(RuntimeError,'old modules/source restored'):self.run_switch(ready)
        self.assertEqual(attempts,2);self.assertTrue(self.active)
        for name in ('package.json','package-lock.json'):self.assertEqual((self.live/name).read_bytes(),self.original[name])
        self.assertEqual((self.live/'server.js').read_bytes(),switch.patched_server(self.original['server.js']))
        self.assertNotIn(b' base=${BASE}',(self.live/'server.js').read_bytes())
        self.assertIn(b'const BASE = "/synthetic-private-path";',(self.live/'server.js').read_bytes())
        self.assertEqual((self.live/'node_modules/identity').read_text(),'6.15.3')
        self.assertEqual((self.live/'auth/new-session').read_text(),'synthetic session')
        backup=next(self.backups.iterdir());self.assertEqual((backup/'failed-node_modules/identity').read_text(),'6.16.0')
        receipt=json.loads((backup/'switch.json').read_text())
        self.assertEqual(receipt['phase'],'rolled-back')
        self.assertEqual(receipt['rollback_server_sha256'],self.plan['patched_server_sha256'])
        self.assertEqual(receipt['rollback_server_variant'],'original-with-reviewed-startup-log-redaction')
        self.assertEqual((backup/'server.js').read_bytes(),self.original['server.js'])
    def test_drift_linked_state_and_nonempty_auth_refuse_before_stop(self):
        (self.live/'package.json').write_text('changed')
        with self.assertRaises(ValueError):self.run_switch()
        self.assertNotIn('stop',self.calls)
        (self.live/'package.json').write_bytes(self.original['package.json'])
        with self.assertRaises(ValueError):self.run_switch(state=lambda:'connected')
        self.assertNotIn('stop',self.calls)
        (self.live/'auth/session').write_text('existing')
        with self.assertRaises(ValueError):self.run_switch()
        self.assertNotIn('stop',self.calls)
        self.assertEqual((self.live/'server.js').read_bytes(),self.original['server.js'])
        self.assertNotIn('start',self.calls)
    def test_auth_arrival_after_stop_retains_redaction_before_any_candidate_changes(self):
        stops=0
        def service(action):
            nonlocal stops
            result=self.service(action)
            if action=='stop':
                stops+=1
                if stops==1:(self.live/'auth/new-session').write_text('arrived during stop')
            return result
        with self.assertRaisesRegex(RuntimeError,'old modules/source restored'):
            switch.apply(self.plan,self.live,self.candidate,self.backups,self.lock,service,lambda:'qr',lambda service,state:None)
        self.assertTrue(self.active)
        self.assertEqual((self.live/'node_modules/identity').read_text(),'6.15.3')
        self.assertEqual((self.live/'server.js').read_bytes(),switch.patched_server(self.original['server.js']))
        self.assertEqual((self.live/'auth/new-session').read_text(),'arrived during stop')
        self.assertTrue((self.candidate/'node_modules').is_dir())

    def test_unexpected_server_edit_is_not_clobbered_or_restarted(self):
        unexpected=b'operator concurrent edit; preserve for manual review'
        def ready(service,state):
            (self.live/'server.js').write_bytes(unexpected)
            raise RuntimeError('candidate failed')
        with self.assertRaisesRegex(RuntimeError,'rollback requires operator review'):self.run_switch(ready)
        self.assertEqual((self.live/'server.js').read_bytes(),unexpected)
        self.assertFalse(self.active)
        self.assertEqual(self.calls.count('start'),1)
        backup=next(self.backups.iterdir())
        self.assertEqual(json.loads((backup/'switch.json').read_text())['phase'],'manual-recovery-required')

    def test_unexpected_package_edit_is_not_clobbered_before_rollback(self):
        for name in ('package.json','package-lock.json'):
            with self.subTest(name=name):
                # A separate fixture per variant preserves the atomic rename preconditions.
                case=WhatsAppSwitchTests(methodName='runTest');case.setUp()
                self.addCleanup(case.doCleanups)
                unexpected=b'concurrent manifest edit; preserve'
                def ready(service,state):
                    (case.live/name).write_bytes(unexpected)
                    raise RuntimeError('candidate failed')
                with self.assertRaisesRegex(RuntimeError,'rollback requires operator review'):case.run_switch(ready)
                self.assertEqual((case.live/name).read_bytes(),unexpected)
                self.assertEqual((case.live/'node_modules/identity').read_text(),'6.16.0')
                self.assertFalse(case.active)
                self.assertEqual(case.calls.count('start'),1)
                backup=next(case.backups.iterdir())
                self.assertEqual(json.loads((backup/'switch.json').read_text())['phase'],'manual-recovery-required')

    def test_shared_deploy_lock_prevents_any_switch(self):
        with switch.deployment_lock(self.lock):
            with self.assertRaises(BlockingIOError):self.run_switch()
        self.assertEqual(self.calls,[])
    def test_external_module_symlink_and_missing_hash_refused(self):
        (self.candidate/'node_modules/outside').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):switch.inventory(self.live,self.candidate)
        bad={**self.plan,'candidate':{}}
        with self.assertRaises(ValueError):switch.validate_plan(bad)
    def test_log_patch_never_alters_route_value_and_refuses_ambiguous_source(self):
        source=self.original['server.js'];patched=switch.patched_server(source)
        self.assertIn(b'const BASE = "/synthetic-private-path";',patched)
        for invalid in (source+source,b'const BASE = "secret";'):
            with self.assertRaises(ValueError):switch.patched_server(invalid)

    def test_log_patch_passes_the_deployed_no_secret_pino_format_through(self):
        # The live server.js logs via pino now: no BASE in the startup line, so
        # patched_server must return it unchanged instead of refusing to run.
        current=b'const BASE = "/connect-whatsapp/${TOKEN}";\nlog.info("whatsapp-connect listening on 127.0.0.1:%d", PORT);\n'
        self.assertEqual(switch.patched_server(current),current)

if __name__=='__main__':unittest.main()
