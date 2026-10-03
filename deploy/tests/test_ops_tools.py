"""Synthetic safety tests for reviewed operations scripts; never touch live paths."""
from __future__ import annotations
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/ops'))
import sqlite_backup
import scheduled_backup
import inbox_runtime


class BackupTests(unittest.TestCase):
    def test_wal_snapshot_is_consistent_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'live.db'
            reader = sqlite3.connect(source)
            self.addCleanup(reader.close)
            reader.execute('PRAGMA journal_mode=WAL')
            reader.execute('CREATE TABLE synthetic(value TEXT)')
            reader.execute("INSERT INTO synthetic VALUES ('committed')"); reader.commit()
            reader.execute("INSERT INTO synthetic VALUES ('uncommitted')")
            target = root / 'snapshot.sqlite3'
            receipt = sqlite_backup.backup(source, target)
            with closing(sqlite3.connect(target)) as snapshot:
                self.assertEqual(snapshot.execute('SELECT * FROM synthetic').fetchall(), [('committed',)])
            self.assertEqual(receipt['integrity'], 'ok')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError): sqlite_backup.backup(source, target)
            reader.rollback()

    def test_invalid_database_never_produces_a_completed_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'bad.db'; source.write_text('synthetic corruption')
            target = Path(temp) / 'snapshot.sqlite3'
            with self.assertRaises(sqlite3.DatabaseError): sqlite_backup.backup(source, target)
            self.assertFalse(target.exists())
            self.assertTrue(target.with_name(target.name + '.incomplete').exists())

    def test_retention_only_removes_old_verified_owned_snapshots_and_keeps_three(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = datetime.now(timezone.utc)
            manual = root / 'operator-backup.cms'; manual.write_bytes(b'keep')
            for n in range(1, 6):
                path = root / f'scheduled-2020010{n}T000000Z.cms'; path.write_bytes(b'synthetic encrypted bytes')
                path.with_suffix('.json').write_text(json.dumps({'ciphertext_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
                os.utime(path, (1, 1))
            scheduled_backup.prune(root, now)
            self.assertTrue(manual.exists())
            self.assertEqual(len(list(root.glob('scheduled-*.cms'))), 3)

    def test_backup_failure_is_recorded_without_sensitive_exception_text(self):
        with tempfile.TemporaryDirectory() as temp:
            status = Path(temp) / 'backup-status.json'
            with patch.object(scheduled_backup, 'STATUS', status), patch.object(scheduled_backup, 'CERT', Path(temp) / 'missing.pem'):
                with self.assertRaises(RuntimeError): scheduled_backup.snapshot()
            record = json.loads(status.read_text())
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['error_type'], 'FileNotFoundError')
            self.assertNotIn('missing.pem', status.read_text())

    def test_corrupt_prior_status_is_replaced_with_observable_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            status = Path(temp) / 'backup-status.json'; status.write_text('not json')
            with patch.object(scheduled_backup, 'STATUS', status):
                with self.assertRaises(RuntimeError): scheduled_backup.snapshot()
            record = json.loads(status.read_text())
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['error_type'], 'JSONDecodeError')


class InboxRuntimeRetirementTests(unittest.TestCase):
    def test_all_legacy_actions_refuse_before_privilege_or_mutation(self):
        commands = {
            'prepare': ['--source', '/synthetic/source', '--stage', '/synthetic/stage'],
            'apply': ['--stage', '/synthetic/stage', '--unit', '/synthetic/unit',
                      '--expected-unit-sha256', '0' * 64, '--state-verified'],
            'rollback': ['--backup', '/synthetic/backup'],
        }
        for command, arguments in commands.items():
            for active in (True, False):
                with self.subTest(command=command, previous_active=active), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    files = {'unit': 'active' if active else 'masked', 'runtime': 'old source',
                             'receipt.json': json.dumps({'was_active': active}),
                             'customer.sqlite3': 'synthetic protected data'}
                    for name, body in files.items():
                        (root / name).write_text(body)
                    with patch.object(sys, 'argv', ['inbox_runtime.py', command, *arguments]), \
                            patch('os.geteuid') as privilege, patch('builtins.open') as opening, \
                            patch('subprocess.run') as child, patch.object(Path, 'rename') as rename, \
                            patch.object(Path, 'write_text') as writing:
                        with self.assertRaisesRegex(SystemExit, 'Inbox 1 is retired'):
                            inbox_runtime.main()
                        for mutation in (privilege, opening, child, rename, writing):
                            mutation.assert_not_called()
                    self.assertEqual({path.name: path.read_text() for path in root.iterdir()}, files)

    def test_help_points_to_supported_recovery_without_install_actions(self):
        import contextlib
        import io
        output = io.StringIO()
        with patch.object(sys, 'argv', ['inbox_runtime.py', '--help']), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:
                inbox_runtime.main()
        self.assertEqual(result.exception.code, 0)
        text = output.getvalue()
        self.assertIn('deploy/cd/README.md', text)
        for command in ('prepare', 'apply', 'rollback'):
            self.assertNotIn(command, text)

    def test_unqualified_call_refuses(self):
        with patch.object(sys, 'argv', ['inbox_runtime.py']):
            with self.assertRaisesRegex(SystemExit, 'Inbox 1 is retired'):
                inbox_runtime.main()


if __name__ == '__main__': unittest.main()
