"""Recovery tests use temporary synthetic data only; no VPS or service calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('source_release', Path(__file__).parents[1] / 'cd/source_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

class SourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.staged = self.root / 'staged'
        self.live = self.root / 'live'
        self.web = self.root / 'web'
        self.journal = self.root / 'journal'
        self.state = self.root / 'state.json'
        for component in release.COMPONENTS:
            (self.staged / component).mkdir(parents=True)
            self.write(self.staged / component / 'app.py', 'new code')
        self.write(self.staged / '.buttonsbebe-release.json', json.dumps({'generation': 10, 'commit': 'a' * 40}))
        for component, required in release.REQUIRED_FILES.items():
            for filename in required:
                self.write(self.staged / component / filename, 'required source')
                if Path(filename).name in release.DEPENDENCIES:
                    target = release.COMPONENTS[component][0]
                    self.write(self.live / target / filename, 'required source')
        for name in ('index.html', 'login.html'):
            self.write(self.staged / 'console-src' / name, 'new html')
        self.write(self.live / 'webhook/app.py', 'old code')

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def prepare(self):
        return release.prepare(self.staged, self.live, self.web, self.journal, self.state)

    def test_failed_release_preserves_new_data_credentials_venv_and_unknown_files(self):
        paths = ['.env', 'webhook/data/webhook.db', 'webhook/.venv/bin/python',
                 'KB/lancedb/index', 'KB/policies/policy.md', 'KB/tickets/learned.md',
                 'whatsapp-connect/auth/session.json', 'console-src/inbox/data/tickets.json',
                 'tools/unknown-runtime-file']
        for path in paths:
            self.write(self.live / path, 'before')
        self.prepare()
        release.apply(self.journal)
        for path in paths:
            self.write(self.live / path, 'after accepted customer work')
        release.apply(self.journal, rollback=True)
        for path in paths:
            self.assertEqual((self.live / path).read_text(), 'after accepted customer work', path)
            self.assertFalse((self.journal / 'files/app' / path).exists())
        self.assertEqual((self.live / 'webhook/app.py').read_text(), 'old code')
        self.assertFalse((self.live / 'console-src/inbox/app.py').exists())

    def test_inbox2_web_assets_and_backend_have_separate_roots(self):
        manifest = release.inventory(self.staged)
        for name in release.INBOX2_ASSETS:
            key = 'inbox2web/' + name
            self.assertIn(key, manifest)
            self.assertEqual(release.target_path(key, self.live, self.web, self.root/'runtime'), self.web.parent/'inbox2'/name)
        for name in ('live_api.py', 'customer_details.py', 'shop_worker.py'):
            self.assertIn('inbox2/' + name, manifest)
            self.assertNotIn('inbox2web/' + name, manifest)
        self.assertFalse(any('helpdesk-inbox' in item['services'] for item in manifest.values()))
        self.assertEqual(manifest['inbox/console-src/inbox/projection.py']['services'], ['helpdesk-inbox2', 'buttonsbebe-inbox2-shop'])

    def test_missing_runtime_hashlock_fails_before_source_mutation(self):
        for component in ('tools', 'kb'):
            path = self.staged / component / 'requirements.lock'
            original = path.read_text()
            path.unlink()
            with self.subTest(component=component), self.assertRaises(Exception):
                self.prepare()
            self.assertEqual((self.live / 'webhook/app.py').read_text(), 'old code')
            path.write_text(original)

    def test_projection_process_code_ships_but_qa_and_runtime_data_do_not(self):
        for path in ('testing/requirements-qa.lock', 'testing/qa_harness.py',
                     'console-src/inbox/data/projection.sqlite3',
                     'console-src/inbox/data/inbox.sqlite3', 'kb/lancedb/index'):
            self.write(self.staged / path, 'never replace runtime')
        journal = self.prepare()
        expected = {
            'inbox/console-src/inbox/projection.py',
            'inbox/console-src/inbox/export_projection.py',
            'inbox/console-src/inbox/shop_rail.py',
            'inbox/console-src/inbox/export_shop_rail.py',
            'app/processor/hermes_runner/process.py',
        }
        self.assertTrue(expected.issubset(journal['files']))
        self.assertFalse(any('testing/' in key or '/data/' in key or '/lancedb/' in key
                             for key in journal['files']))
        self.assertEqual(journal['files']['app/processor/hermes_runner/process.py']['services'],
                         ['buttonsbebe-webhook', 'buttonsbebe-processor'])

    def test_inventory_accepts_actual_tracked_archive_layout(self):
        import io
        import subprocess
        import tarfile
        repo = Path(__file__).resolve().parents[2]
        payload = subprocess.check_output([
            'git', '-C', str(repo), 'archive', '--format=tar', 'HEAD',
            *release.COMPONENTS, 'console-src/index.html', 'console-src/login.html'])
        destination = self.root / 'actual-git-archive'
        destination.mkdir()
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            archive.extractall(destination, filter='data')
        manifest = release.inventory(destination)
        self.assertIn('app/kb-admin/server.js', manifest)
        self.assertNotIn('app/kb-admin/package.json', manifest)
        self.assertIn('app/processor/hermes_runner/process.py', manifest)
        self.assertIn('inbox/console-src/inbox/export_projection.py', manifest)

    def test_partial_apply_recovers_and_repeated_rollback_is_safe(self):
        journal = self.prepare()
        first = journal['changes'][0]
        target = release.target_path(first['key'], self.live, self.web)
        release.atomic_copy(self.staged / first['entry']['source'], target)
        release.apply(self.journal, rollback=True)
        release.apply(self.journal, rollback=True)
        self.assertEqual((self.live / 'webhook/app.py').read_text(), 'old code')

    def test_concurrent_code_edit_prevents_partial_rollback_overwrite(self):
        self.prepare()
        release.apply(self.journal)
        self.write(self.live / 'webhook/app.py', 'independent fix')
        with self.assertRaisesRegex(ValueError, 'concurrent code edit'):
            release.apply(self.journal, rollback=True)
        self.assertEqual((self.live / 'webhook/app.py').read_text(), 'independent fix')
        self.assertEqual((self.web / 'index.html').read_text(), 'new html')

    def test_tampered_staged_artifact_is_rejected(self):
        self.prepare()
        self.write(self.staged / 'console-src/index.html', 'tampered')
        with self.assertRaisesRegex(ValueError, 'checksum changed'):
            release.apply(self.journal)
        release.apply(self.journal, rollback=True)

    def test_symlink_target_is_rejected(self):
        outside = self.root / 'customer-data'
        outside.mkdir()
        self.live.mkdir(exist_ok=True)
        (self.live / 'feedback').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.prepare()

    def test_dependency_changes_fail_before_backup_or_service_stop(self):
        self.write(self.staged / 'webhook/uv.lock', 'changed dependency')
        with self.assertRaisesRegex(ValueError, 'dependency preparation required'):
            self.prepare()
        self.assertFalse(self.journal.exists())
        self.assertEqual((self.live / 'webhook/app.py').read_text(), 'old code')

    def test_success_manifest_owns_only_source_and_detects_later_drift(self):
        for source in ('kb/policies/policy.md', 'console-src/inbox/data/live.json', 'webhook/.env'):
            self.write(self.staged / source, 'never ship this')
        journal = self.prepare()
        self.assertNotIn('app/KB/policies/policy.md', journal['files'])
        self.assertNotIn('app/webhook/.env', journal['files'])
        release.apply(self.journal)
        release.atomic_json({'files': journal['files']}, self.state)
        self.write(self.live / 'webhook/app.py', 'unreviewed live drift')
        with self.assertRaisesRegex(ValueError, 'live code drift'):
            release.prepare(self.staged, self.live, self.web, self.root / 'next-journal', self.state)

    def test_incomplete_artifact_fails_before_a_journal_exists(self):
        (self.staged / 'webhook/src/bb_webhook/app.py').unlink()
        with self.assertRaisesRegex(ValueError, 'missing required release file'):
            self.prepare()
        self.assertFalse(self.journal.exists())

    def test_restrictive_umask_does_not_hide_new_code_from_runtime_user(self):
        import os
        self.prepare()
        previous = os.umask(0o077)
        try:
            release.apply(self.journal)
        finally:
            os.umask(previous)
        self.assertEqual((self.live / 'console-src/inbox').stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.journal.stat().st_mode & 0o777, 0o700)

    def test_shell_entrypoint_execute_permission_is_restored(self):
        self.write(self.staged / 'kb/sync-products.sh', '#!/bin/bash\nexit 0\n')
        (self.staged / 'kb/sync-products.sh').chmod(0o644)
        self.prepare()
        release.apply(self.journal)
        self.assertEqual((self.live / 'KB/sync-products.sh').stat().st_mode & 0o777, 0o755)

    def test_stale_workflow_generation_is_rejected(self):
        self.write(self.state, json.dumps({'files': {}, 'generation': 11, 'commit': 'b' * 40}))
        with self.assertRaisesRegex(ValueError, 'stale release'):
            self.prepare()
        self.assertFalse(self.journal.exists())

    def test_only_previously_managed_file_is_removed_and_can_be_restored(self):
        journal = self.prepare()
        release.apply(self.journal)
        release.atomic_json({'files': journal['files']}, self.state)
        (self.staged / 'webhook/app.py').unlink()
        self.write(self.live / 'webhook/unknown.py', 'runtime custom')
        next_journal = self.root / 'second'
        release.prepare(self.staged, self.live, self.web, next_journal, self.state)
        release.apply(next_journal)
        self.assertFalse((self.live / 'webhook/app.py').exists())
        self.assertEqual((self.live / 'webhook/unknown.py').read_text(), 'runtime custom')
        release.apply(next_journal, rollback=True)
        self.assertEqual((self.live / 'webhook/app.py').read_text(), 'new code')

if __name__ == '__main__':
    unittest.main()
