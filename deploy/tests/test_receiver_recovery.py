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
        tree_digest = hashlib.sha256((hashlib.sha256(b'config').hexdigest() + '  ./approved.txt\n').encode()).hexdigest()
        self.write(self.root / 'approved', f'deploy/systemd {tree_digest}\ndeploy/caddy {tree_digest}\n{self.root}/applied-config {config_digest}\n')
        helper = (ROOT / 'deploy/cd/source_release.py').read_text().replace('/var/lib/buttonsbebe-deploy/source-manifest.json', str(self.root / 'manifest.json')).replace('/opt/buttonsbebe/inbox', str(self.root / 'inbox'))
        # Defense in depth: even a missing receiver substitution must never
        # reach a real service tree. Reject every root outside this test's temp.
        helper = helper.replace('    args = parser.parse_args()', '''
    args = parser.parse_args()
    harness_root = Path(os.environ['HARNESS_ROOT']).resolve()
    for candidate in (args.live, args.web, args.inbox, args.journal, args.state):
        if not candidate.resolve().is_relative_to(harness_root):
            raise SystemExit('HARNESS refused non-temporary deployment target')
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
            target = {'app': self.live, 'web': self.web, 'inbox': self.root / 'inbox', 'inbox2': self.root / 'inbox2', 'inbox2web': self.web.parent / 'inbox2'}[prefix] / relative
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

    def projection_fixture(self, fail_export=False, fail_ready=False):
        self.write(self.root / 'active.json', json.dumps([
            'buttonsbebe-webhook','helpdesk-inbox2','buttonsbebe-inbox-projection.timer']))
        self.write(self.bin / 'curl', """#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['HARNESS_ROOT'])
url=next(arg for arg in sys.argv if arg.startswith('http://'))
with (root/'calls').open('a') as out:out.write('probe '+url+'\\n')
if url.endswith('/inbox/api/helpdesk'):
 print(json.dumps({'ok':True,'readOnly':True,'capabilities':{'sendReply':False}}))
elif url.endswith(':8767/health'):
 if FAIL_READY and (root/'live/webhook/app.py').read_text()=='new code':sys.exit(22)
 print(json.dumps({'ok':True,'readOnly':True}))
""".replace('FAIL_READY',repr(fail_ready)))
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
        self.assertLess(calls.index('start buttonsbebe-inbox-projection.service'),calls.index('probe http://127.0.0.1:8767/health'))
        self.assertLess(calls.index('probe http://127.0.0.1:8767/health'),calls.index('start buttonsbebe-inbox-projection.timer'))

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
