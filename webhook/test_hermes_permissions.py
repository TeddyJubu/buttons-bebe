"""Synthetic permission and authenticated rewrite launch checks."""
import itertools
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from bb_webhook import app as app_module
from bb_webhook.hermes_permissions import canonical_toolsets, child_environment
from webhook.action_test_support import setup_action_case

CANONICAL = 'buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias'


class PermissionTests(unittest.TestCase):
    def test_helper_import_is_pure_and_release_contains_both_python_services(self):
        src = Path(__file__).resolve().parent / 'src'
        probe = subprocess.run([sys.executable, '-c',
            "import sys; import bb_webhook.hermes_permissions; assert 'bb_webhook.config' not in sys.modules; print('pure')"],
            env={'PYTHONPATH': str(src), 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True, check=True)
        self.assertEqual(probe.stdout, 'pure\n')
        from deploy.cd.source_release import COMPONENTS, REQUIRED_FILES, inventory
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for component in COMPONENTS:
                (root / component).mkdir(parents=True, exist_ok=True)
                for name in REQUIRED_FILES[component]:
                    file = root / component / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_text('synthetic source')
            for name in ('index.html', 'login.html'):
                (root / 'console-src' / name).write_text('synthetic console')
            helper = root / 'webhook/src/bb_webhook/hermes_permissions.py'
            helper.write_text('synthetic permission helper')
            entries = inventory(root)
            self.assertEqual(entries['app/webhook/src/bb_webhook/hermes_permissions.py']['services'],
                             ['buttonsbebe-webhook', 'buttonsbebe-processor'])

    def test_only_the_complete_readonly_set_is_canonicalized(self):
        for names in itertools.permutations(CANONICAL.split(',')):
            self.assertEqual(canonical_toolsets(' , '.join(names)), CANONICAL)
        for invalid in ('', None, 'buttonsbebe_kb', CANONICAL + ',', CANONICAL + ',file',
                        'buttonsbebe_kb,buttonsbebe_kb,buttonsbebe_redo'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                canonical_toolsets(invalid)

    def test_child_environment_preserves_only_provider_and_runtime_settings(self):
        self.assertEqual(child_environment({'OPENAI_API_KEY': 'synthetic-model', 'LANG': 'C',
            'HOME': '/wrong', 'PATH': '/wrong', 'WA_TOKEN': 'private', 'SHOPIFY_CLIENT_SECRET': 'private',
            'PYTHONPATH': '/injected'}, home='/controlled', path='/controlled/bin'),
            {'OPENAI_API_KEY': 'synthetic-model', 'LANG': 'C', 'HOME': '/controlled', 'PATH': '/controlled/bin'})


class RewritePermissionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await setup_action_case(self)
        self.capture = Path(self.tmp.name) / 'launch.json'
        self.child = Path(self.tmp.name) / 'fake-hermes'
        self.child.write_text(f'''#!{Path(sys.executable).resolve()}
import json,os,pathlib,re,sys
prompt=sys.argv[-1]
pathlib.Path({str(self.capture)!r}).write_text(json.dumps({{"arguments":sys.argv[1:-1],"environment":dict(os.environ),"prompt":prompt}}))
token=re.search(r'<DRAFT:([a-f0-9]+)>',prompt).group(1)
print(f'<DRAFT:{{token}}>Thank you for checking in.</DRAFT:{{token}}>')
''')
        self.child.chmod(0o755)
        for name, value in {'_HERMES_BIN': str(self.child), '_HERMES_HOME': '/controlled',
                            '_HERMES_PROFILE': 'synthetic', '_HERMES_IGNORE_RULES': True}.items():
            p = patch.object(app_module, name, value)
            p.start()
            self.addCleanup(p.stop)

    async def rewrite(self, policy):
        with patch.object(app_module, '_HERMES_REWRITE_TOOLSETS', policy), \
             patch.dict(os.environ, {'PATH': '/usr/bin:/bin', 'OPENAI_API_KEY': 'synthetic-model',
                 'LANG': 'C', 'WA_TOKEN': 'synthetic-private', 'CONSOLE_SESSION_SECRET': 'synthetic-private',
                 'GORGIAS_API_KEY': 'synthetic-private', 'PYTHONPATH': '/injected'}, clear=True):
            return await self.client.post('/dashboard/api/ticket/1/rewrite', json={
                'source_message_id': 'source-1', 'draft': 'Prior draft', 'instruction': 'Make it polite',
                'message_text': 'Untrusted browser context'})

    async def test_authenticated_route_launches_exact_tools_and_filtered_environment(self):
        response = await self.rewrite('buttonsbebe_gorgias, buttonsbebe_kb, buttonsbebe_redo')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'ok': True, 'draft': 'Thank you for checking in.'})
        launch = json.loads(self.capture.read_text())
        self.assertEqual(launch['arguments'], ['-p', 'synthetic', '--ignore-rules', '-t', CANONICAL, '-z'])
        self.assertEqual({k: v for k, v in launch['environment'].items()
                          if k not in {'LC_CTYPE', '__CF_USER_TEXT_ENCODING'}},
                         {'PATH': '/usr/bin:/bin', 'HOME': '/controlled', 'OPENAI_API_KEY': 'synthetic-model', 'LANG': 'C'})
        self.assertIn('Where is my parcel?', launch['prompt'])
        self.assertNotIn('Untrusted browser context', launch['prompt'])

    async def test_invalid_policy_returns_unavailable_without_launching(self):
        for policy in ('', 'buttonsbebe_kb', CANONICAL + ',terminal'):
            response = await self.rewrite(policy)
            self.assertEqual((response.status_code, response.json()), (502, {'error': 'rewrite_unavailable'}))
            self.assertFalse(self.capture.exists())

    async def test_unauthenticated_route_never_launches(self):
        self.client.cookies.clear()
        response = await self.rewrite(CANONICAL)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.capture.exists())
