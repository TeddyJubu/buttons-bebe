"""Execute the receiver against temporary fake services, never the real VPS.

Linux CI supplies util-linux flock. The same harness can be run in any isolated
Linux directory; constants and all service/network commands are redirected.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
SHARED_INTAKE_UNITS = (
    'buttonsbebe-webhook.service',
    'buttonsbebe-processor.service',
    'buttonsbebe-gorgias-mcp.service',
)

@unittest.skipUnless(shutil.which('flock') and shutil.which('sha256sum'), 'Linux deployment toolchain required')
class ReceiverRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.live = self.root / 'live'
        self.web = self.root / 'web'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.write(self.live / 'webhook/app.py', 'old code')
        self.write(self.live / 'webhook/data/webhook.db', 'accepted before deployment')
        self.write(self.root / 'active.json', json.dumps(['buttonsbebe-webhook']))
        self.write(self.root / 'applied-config', 'approved config')
        config_digest = hashlib.sha256(b'approved config').hexdigest()
        self.canonical_units = {
            name: (ROOT / 'deploy/systemd' / name).read_bytes()
            for name in SHARED_INTAKE_UNITS
        }
        self.unit_paths = {name: self.root / 'units' / name for name in SHARED_INTAKE_UNITS}
        for name, body in self.canonical_units.items():
            self.write(self.unit_paths[name], body.decode())
        systemd_files = {'approved.txt': b'config', **self.canonical_units}
        tree_digest = hashlib.sha256(''.join(
            hashlib.sha256(body).hexdigest() + '  ./' + name + '\n'
            for name, body in sorted(systemd_files.items())
        ).encode()).hexdigest()
        caddy_digest = hashlib.sha256((hashlib.sha256(b'config').hexdigest() + '  ./approved.txt\n').encode()).hexdigest()
        self.write(self.root / 'approved', f'deploy/systemd {tree_digest}\ndeploy/caddy {caddy_digest}\n{self.root}/applied-config {config_digest}\n')
        self.approve_consumer_units()
        helper = (ROOT / 'deploy/cd/source_release.py').read_text().replace('/var/lib/buttonsbebe-deploy/source-manifest.json', str(self.root / 'manifest.json')).replace('/opt/buttonsbebe/inbox', str(self.root / 'inbox'))
        # Defense in depth: even a missing receiver substitution must never
        # reach a real service tree. Reject every root outside this test's temp.
        helper = helper.replace('    args = parser.parse_args()', '''
    args = parser.parse_args()
    harness_root = Path(os.environ['HARNESS_ROOT']).resolve()
    for candidate in (args.live, args.web, args.inbox, args.journal, args.state):
        if not candidate.resolve().is_relative_to(harness_root):
            raise SystemExit('HARNESS refused non-temporary deployment target')
    with (harness_root / 'helper-calls').open('a') as out:
        out.write(args.action + '\\n')
    if args.action in {'apply', 'rollback', 'services', 'commit'}:
        check = json.loads((args.journal / 'journal.json').read_text())
        for key in ('live', 'web', 'inbox', 'release'):
            if not Path(check[key]).resolve().is_relative_to(harness_root):
                raise SystemExit('HARNESS refused non-temporary journal root')
''')
        # Root defaults must also be temporary for helper subcommands which
        # read paths only from the validated journal.
        helper = helper.replace('/root/Buttonsbebe Agent', str(self.live)).replace('/var/www/console', str(self.web))
        self.write(self.root / 'helper.py', helper)
        receiver = (ROOT / 'deploy/cd/buttonsbebe-deploy-receive.sh').read_text()
        replacements = {'/root/Buttonsbebe Agent': str(self.live), '/opt/buttonsbebe/releases': str(self.root / 'releases'),
            '/opt/buttonsbebe/backups': str(self.root / 'backups'), '/var/www/console': str(self.web),
            '/opt/buttonsbebe/inbox': str(self.root / 'inbox'),
            '/var/lib/buttonsbebe-deploy/source-manifest.json': str(self.root / 'manifest.json'),
            '/etc/buttonsbebe-deploy-approved-config.sha256': str(self.root / 'approved'),
            '/etc/caddy/sites/support.caddy': str(self.root / 'applied-config'),
            '/etc/systemd/system/helpdesk-inbox2.service': str(self.root / 'applied-config'),
            '/etc/systemd/system/buttonsbebe-inbox-projection.service': str(self.root / 'applied-config'),
            '/etc/systemd/system/buttonsbebe-inbox-projection.timer': str(self.root / 'applied-config'),
            '/usr/local/lib/buttonsbebe-deploy/source_release.py': str(self.root / 'helper.py'),
            '/run/lock/buttonsbebe-deploy.lock': str(self.root / 'deploy.lock'),
            '/var/tmp/buttonsbebe-release': str(self.root / 'archive'),
            'readonly readiness_attempts=10': 'readonly readiness_attempts=1'}
        replacements.update({
            '/etc/systemd/system/' + name: str(path)
            for name, path in self.unit_paths.items()
        })
        for before, after in replacements.items():
            receiver = receiver.replace(before, after)
        for forbidden in ('/root/Buttonsbebe Agent', '/var/www/console', '/opt/buttonsbebe/inbox', '/var/lib/buttonsbebe-deploy'):
            self.assertNotIn(forbidden, receiver)
            self.assertNotIn(forbidden, helper)
        self.write(self.root / 'receiver.sh', receiver)
        self.write(self.bin / 'systemctl', '''#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['HARNESS_ROOT'])
state=root/'active.json'; active=set(json.loads(state.read_text())); verb=sys.argv[1]; name=sys.argv[-1]
with (root/'service-invocations').open('a') as out: out.write(' '.join(sys.argv[1:])+'\\n')
if verb=='is-active': sys.exit(0 if name in active else 3)
with (root/'calls').open('a') as out: out.write(verb+' '+name+'\\n')
if verb=='stop': active.discard(name)
if verb=='start':
 active.add(name)
 (root/'live/webhook/data/webhook.db').write_text('accepted after deployment began')
state.write_text(json.dumps(sorted(active)))
''')
        self.write(self.bin / 'curl', '''#!/usr/bin/env python3
import os,pathlib,sys
root=pathlib.Path(os.environ['HARNESS_ROOT'])
sys.exit(22 if (root/'live/webhook/app.py').read_text()=='new code' else 0)
''')
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'], HARNESS_ROOT=str(self.root))
        self.sha = 'a' * 40
        self.archive = io.BytesIO()
        components = ['feedback', 'webhook', 'processor', 'tools', 'kb', 'kb-admin', 'whatsapp-connect', 'console-src/inbox', 'console-src/helpdesk-agent']
        files = {c + '/app.py': b'new code' for c in components}
        # Include the production manifest's complete required-file contract.
        import importlib.util
        spec = importlib.util.spec_from_file_location('harness_source_policy', ROOT / 'deploy/cd/source_release.py')
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        for component, required in policy.REQUIRED_FILES.items():
            for filename in required:
                files[component + '/' + filename] = b'required source'
                if Path(filename).name in policy.DEPENDENCIES:
                    target_root = self.root / 'inbox' if component.startswith('console-src/') else self.live
                    self.write(target_root / policy.COMPONENTS[component][0] / filename, 'required source')
        files.update({'console-src/index.html': b'html', 'console-src/login.html': b'login',
            'deploy/caddy/approved.txt': b'config', 'deploy/systemd/approved.txt': b'config',
            '.buttonsbebe-release.json': json.dumps({'commit': self.sha, 'generation': 1}).encode()})
        files.update({'deploy/systemd/' + name: body for name, body in self.canonical_units.items()})
        with tarfile.open(fileobj=self.archive, mode='w:gz') as archive:
            for name, body in files.items():
                info = tarfile.TarInfo(name); info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
        self.payload = self.archive.getvalue()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        path.chmod(0o755)

    def run_receiver(self):
        return subprocess.run(['bash', str(self.root / 'receiver.sh'), self.sha, hashlib.sha256(self.payload).hexdigest()],
                              input=self.payload, capture_output=True, env=self.env, timeout=20)

    def approve_consumer_units(self):
        approval = self.root / 'approved'
        lines = [line for line in approval.read_text().splitlines()
                 if not any(line.startswith(str(path) + ' ') for path in self.unit_paths.values())]
        lines.extend(str(path) + ' ' + hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in self.unit_paths.values())
        self.write(approval, '\n'.join(lines) + '\n')

    def assert_consumer_guard_refuses_before_prepare(self, expected_error):
        # Release staging may occur; managed source/data and all service/helper
        # commands must remain untouched when configuration approval fails.
        watched = [self.live, self.web, self.root / 'inbox', self.root / 'inbox2',
                   self.root / 'shared', self.root / 'manifest.json',
                   self.root / 'active.json', self.root / 'approved', self.root / 'units']
        def snapshot():
            result = {}
            for path in watched:
                files = path.rglob('*') if path.is_dir() else [path]
                for candidate in files:
                    if candidate.is_file():
                        result[str(candidate)] = (candidate.read_bytes(), candidate.stat().st_mode)
            return result
        before = snapshot()
        result = self.run_receiver()
        self.assertNotEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(expected_error, result.stderr)
        self.assertEqual(snapshot(), before)
        self.assertFalse((self.root / 'helper-calls').exists())
        self.assertFalse((self.root / 'service-invocations').exists())
        self.assertFalse((self.root / 'calls').exists())
        self.assertEqual(list((self.root / 'backups').glob('*/journal.json')), [])

    def remove_consumer_approval(self, *names):
        approval = self.root / 'approved'
        lines = approval.read_text().splitlines()
        self.write(approval, '\n'.join(line for line in lines if not any(
            line.startswith(str(self.unit_paths[name]) + ' ') for name in names)) + '\n')

    def test_missing_all_shared_intake_unit_fingerprints_refuses_before_prepare(self):
        self.remove_consumer_approval(*SHARED_INTAKE_UNITS)
        self.assert_consumer_guard_refuses_before_prepare(b'Shared intake consumer unit applied fingerprints are required')

    def test_missing_webhook_unit_fingerprint_refuses_before_prepare(self):
        self.remove_consumer_approval('buttonsbebe-webhook.service')
        self.assert_consumer_guard_refuses_before_prepare(b'Shared intake consumer unit applied fingerprints are required')

    def test_missing_processor_unit_fingerprint_refuses_before_prepare(self):
        self.remove_consumer_approval('buttonsbebe-processor.service')
        self.assert_consumer_guard_refuses_before_prepare(b'Shared intake consumer unit applied fingerprints are required')

    def test_missing_gorgias_unit_fingerprint_refuses_before_prepare(self):
        self.remove_consumer_approval('buttonsbebe-gorgias-mcp.service')
        self.assert_consumer_guard_refuses_before_prepare(b'Shared intake consumer unit applied fingerprints are required')

    def test_approved_prior_consumer_units_refuse_before_prepare(self):
        for name, body in self.canonical_units.items():
            old = body.replace(b'/opt/buttonsbebe/shared:', b'')
            old = old.replace(b'Environment=PYTHONPATH=/opt/buttonsbebe/shared\n', b'')
            self.write(self.unit_paths[name], old.decode())
        self.approve_consumer_units()
        self.assert_consumer_guard_refuses_before_prepare(b'Shared intake consumer unit differs from reviewed release')

    def test_consumer_unit_drift_refuses_before_prepare(self):
        path = self.unit_paths['buttonsbebe-processor.service']
        self.write(path, path.read_text() + '# unapproved change\n')
        self.assert_consumer_guard_refuses_before_prepare(b'Applied configuration drift')

    def test_each_approved_noncanonical_consumer_refuses_before_prepare(self):
        for name, body in self.canonical_units.items():
            with self.subTest(unit=name):
                self.write(self.unit_paths[name], body.decode() + '# approved host variant\n')
                self.approve_consumer_units()
                self.assert_consumer_guard_refuses_before_prepare(
                    b'Shared intake consumer unit differs from reviewed release')
                self.write(self.unit_paths[name], body.decode())

    def test_failed_readiness_restores_code_not_new_database_work(self):
        result = self.run_receiver()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Prior source restored', result.stderr, result.stderr.decode())
        self.assertEqual((self.live / 'webhook/app.py').read_text(), 'old code')
        self.assertEqual((self.live / 'webhook/data/webhook.db').read_text(), 'accepted after deployment began')
        self.assertEqual(json.loads((self.root / 'active.json').read_text()), ['buttonsbebe-webhook'])
        self.assertNotIn('buttonsbebe-processor', (self.root / 'calls').read_text())
        self.assertFalse((self.root / 'manifest.json').exists())

    def test_success_records_exact_installed_baseline_and_temporary_roots(self):
        self.write(self.bin / 'curl', '#!/bin/sh\nexit 0\n')
        result = self.run_receiver()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn('start buttonsbebe-inbox-projection.service', (self.root / 'calls').read_text())
        manifest = json.loads((self.root / 'manifest.json').read_text())
        self.assertEqual(manifest['generation'], 1)
        self.assertEqual(manifest['commit'], self.sha)
        for key, entry in manifest['files'].items():
            prefix, relative = key.split('/', 1)
            target = {'app': self.live, 'web': self.web, 'inbox': self.root / 'inbox', 'inbox2': self.root / 'inbox2', 'inbox2web': self.web.parent / 'inbox2', 'shared': self.root / 'shared'}[prefix] / relative
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), entry['sha256'])
            self.assertTrue(target.resolve().is_relative_to(self.root.resolve()))
        self.assertNotIn('app/webhook/data/webhook.db', manifest['files'])
        journal = next((self.root / 'backups').glob('*/journal.json'))
        journal_data = json.loads(journal.read_text())
        for key in ('live', 'web', 'inbox', 'release'):
            self.assertTrue(Path(journal_data[key]).resolve().is_relative_to(self.root.resolve()))

    def test_projection_timer_is_restored_on_rollback(self):
        self.write(self.root / 'active.json', json.dumps([
            'buttonsbebe-webhook', 'buttonsbebe-inbox-projection.timer']))
        result = self.run_receiver()  # synthetic readiness fails -> rollback
        self.assertNotEqual(result.returncode, 0)
        calls = (self.root / 'calls').read_text()
        self.assertIn('stop buttonsbebe-inbox-projection.timer', calls)
        self.assertIn('start buttonsbebe-inbox-projection.timer', calls)
        self.assertIn('buttonsbebe-inbox-projection.timer', json.loads((self.root / 'active.json').read_text()))

    def projection_fixture(self, fail_export=False, fail_ready=False, readiness=None, recover_after=None):
        self.write(self.root / 'active.json', json.dumps([
            'buttonsbebe-webhook','helpdesk-inbox2','buttonsbebe-inbox-projection.timer']))
        self.write(self.bin / 'curl', """#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['HARNESS_ROOT'])
url=next(arg for arg in sys.argv if arg.startswith('http://'))
with (root/'calls').open('a') as out:out.write('probe '+url+'\\n')
if url.endswith('/inbox/api/helpdesk'):
 print(json.dumps({'ok':True,'readOnly':True,'capabilities':{'sendReply':False}}))
elif url.endswith(':8767/ready'):
 payload={'status':'ready','readOnly':True,'checks':{'storage':'ok','worker':'ok','ticketData':'fresh','projection':'fresh'}}
 if (root/'live/webhook/app.py').read_text()=='new code':
  if READINESS is not None:payload=READINESS
  if FAIL_READY:payload['status']='degraded';payload['checks']['projection']='stale'
  if RECOVER_AFTER is not None:
   elapsed=int((root/'elapsed').read_text()) if (root/'elapsed').exists() else 0
   with (root/'calls').open('a') as out:out.write('inbox-ready-at '+str(elapsed)+'\\n')
   if elapsed<RECOVER_AFTER:payload['status']='degraded';payload['checks']['worker']='error'
 print(json.dumps(payload))
 if FAIL_READY and (root/'live/webhook/app.py').read_text()=='new code':sys.exit(22)
""".replace('FAIL_READY',repr(fail_ready)).replace('READINESS',repr(readiness))
    .replace('RECOVER_AFTER',repr(recover_after)))
        script=(self.bin/'systemctl').read_text()
        script=script.replace("if verb=='start':", """if verb=='start' and name=='buttonsbebe-inbox-projection.service':
 if FAIL_EXPORT and (root/'live/webhook/app.py').read_text()=='new code':sys.exit(1)
 (root/'snapshot-refreshed').write_text('derived snapshot')
 sys.exit(0)
if verb=='start':""".replace('FAIL_EXPORT',repr(fail_export)))
        self.write(self.bin/'systemctl',script)

    def test_projection_refresh_precedes_inbox_readiness_and_restores_timer(self):
        self.projection_fixture()
        result=self.run_receiver()
        self.assertEqual(result.returncode,0,result.stderr.decode())
        calls=(self.root/'calls').read_text()
        self.assertLess(calls.index('start buttonsbebe-webhook'),calls.index('start buttonsbebe-inbox-projection.service'))
        self.assertLess(calls.index('probe http://127.0.0.1:8000/ready'),calls.index('start buttonsbebe-inbox-projection.service'))
        self.assertLess(calls.index('start buttonsbebe-inbox-projection.service'),calls.index('probe http://127.0.0.1:8767/ready'))
        self.assertLess(calls.index('probe http://127.0.0.1:8767/ready'),calls.index('start buttonsbebe-inbox-projection.timer'))

    def test_first_sync_failure_recovers_within_receiver_wait_without_rollback(self):
        self.projection_fixture(recover_after=5)
        receiver=(self.root/'receiver.sh').read_text().replace(
            'readonly readiness_attempts=1','readonly readiness_attempts=3')
        self.write(self.root/'receiver.sh',receiver)
        self.write(self.bin/'sleep','''#!/usr/bin/env python3
import os,pathlib,sys
root=pathlib.Path(os.environ['HARNESS_ROOT']);clock=root/'elapsed'
elapsed=int(clock.read_text()) if clock.exists() else 0
delay=int(sys.argv[1]);clock.write_text(str(elapsed+delay))
with (root/'calls').open('a') as out:out.write('wait '+str(delay)+'\\n')
''')
        result=self.run_receiver()
        self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertNotIn(b'Prior source restored',result.stderr)
        calls=(self.root/'calls').read_text().splitlines()
        self.assertEqual([line for line in calls if line.startswith('inbox-ready-at ')],
                         ['inbox-ready-at 0','inbox-ready-at 3','inbox-ready-at 6'])
        self.assertEqual([line for line in calls if line.startswith('wait ')],['wait 3','wait 3'])
        self.assertEqual((self.live/'webhook/app.py').read_text(),'new code')
        self.assertEqual(json.loads((self.root/'manifest.json').read_text())['commit'],self.sha)
        self.assertIn('buttonsbebe-inbox-projection.timer',json.loads((self.root/'active.json').read_text()))

    def test_failed_projection_export_rolls_back_source_without_rewinding_data(self):
        self.projection_fixture(fail_export=True)
        result=self.run_receiver()
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'Prior source restored',result.stderr)
        self.assertEqual((self.live/'webhook/app.py').read_text(),'old code')
        self.assertEqual((self.live/'webhook/data/webhook.db').read_text(),'accepted after deployment began')
        self.assertIn('buttonsbebe-inbox-projection.timer',json.loads((self.root/'active.json').read_text()))
        self.assertTrue((self.root/'snapshot-refreshed').is_file())

    def test_failed_inbox_ready_cannot_pass_on_send_lock_alone(self):
        self.projection_fixture(fail_ready=True)
        result=self.run_receiver()
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'Prior source restored',result.stderr)
        self.assertEqual((self.live/'webhook/app.py').read_text(),'old code')

    def test_inbox_unusable_record_rolls_back_even_when_http_succeeds(self):
        self.projection_fixture(readiness={'status':'unusable','readOnly':True,
            'checks':{'storage':'ok','worker':'ok','ticketData':'fresh','projection':'unavailable'}})
        result=self.run_receiver()
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'Prior source restored',result.stderr)
        self.assertEqual((self.live/'webhook/data/webhook.db').read_text(),'accepted after deployment began')

    def test_ready_label_without_fresh_projection_cannot_pass(self):
        self.projection_fixture(readiness={'status':'ready','readOnly':True,
            'checks':{'storage':'ok','worker':'ok','ticketData':'fresh','projection':'stale'}})
        result=self.run_receiver()
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'Prior source restored',result.stderr)
        self.assertEqual((self.live/'webhook/app.py').read_text(),'old code')

    def test_active_projection_aborts_without_stopping_export_or_app(self):
        self.write(self.root / 'active.json', json.dumps([
            'buttonsbebe-webhook', 'buttonsbebe-inbox-projection.timer',
            'buttonsbebe-inbox-projection.service']))
        result = self.run_receiver()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Inbox projection active', result.stderr)
        calls = (self.root / 'calls').read_text()
        self.assertNotIn('stop buttonsbebe-webhook', calls)
        self.assertNotIn('stop buttonsbebe-inbox-projection.service', calls)
        self.assertIn('start buttonsbebe-inbox-projection.timer', calls)
        self.assertEqual((self.live / 'webhook/app.py').read_text(), 'old code')

    def test_poison_guard_refuses_an_accidentally_unpatched_production_root(self):
        result = subprocess.run(['python3', str(self.root / 'helper.py'), 'prepare',
                                 '--journal', str(self.root / 'test-journal'),
                                 '--live', '/root/Buttonsbebe Agent'],
                                env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'HARNESS refused non-temporary', result.stderr)

    def test_second_deployment_exits_before_receiving_or_stopping_services(self):
        import fcntl
        with (self.root / 'deploy.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_receiver()
        self.assertEqual(result.returncode, 75, result.stderr.decode())
        self.assertFalse((self.root / 'calls').exists())
        self.assertFalse((self.root / 'backups').exists())

if __name__ == '__main__':
    unittest.main()
