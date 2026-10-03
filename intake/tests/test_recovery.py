from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from intake import attachments, delivery, recovery, replay
from intake.integrity import check_database
from intake.policy import Conflict, Invalid
from intake.private_files import checksum
from intake.records import canonical
from intake.replay_rehearse import snapshot
from intake.store import Store
from intake.tests.test_replay import counts, event, payload


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'work').mkdir(mode=0o700)
        self.store = Store(self.root / 'work')
        self.data = b'Private synthetic attachment\x00\xff\n'
        candidate = event('files', attachments=[{'id': 'a', 'name': 'example.html', 'size': len(self.data)},
                                               {'id': 'b', 'name': 'other.bin', 'size': len(self.data)}])
        raw = payload([candidate])
        self.tid = replay.run(self.store, raw, replay.run(self.store, raw, preview=True)['digest'])['results'][0]['ticket_id']
        with self.store.connection() as db:
            self.ids = [r[0] for r in db.execute('SELECT id FROM attachments ORDER BY rowid')]
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir(mode=0o700)
        (self.bundle / 'payload.bin').write_bytes(self.data)
        self.manifest = {'mode': 'offline_attachment_import', 'files': [
            {'attachment_id': aid, 'file': 'payload.bin', 'sha256': checksum(self.data)} for aid in self.ids]}
        self.write_manifest()
        self.patcher = patch.object(recovery, 'ROOT', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write_manifest(self):
        (self.bundle / 'manifest.json').write_text(canonical(self.manifest))

    def import_files(self):
        return attachments.apply_import(self.store, self.bundle, attachments.preview(self.store, self.bundle)['digest'])

    def backup(self):
        report = recovery.backup(self.store, 'checkpoint')
        return self.store.path.parent / 'backups' / 'checkpoint', report

    def review(self, scenario='accepted', operation='review'):
        return delivery.review(self.store, self.tid, {'mode': delivery.MODE, 'operation_id': operation,
            'revision': self.store.get_ticket(self.tid)['revision'], 'body': 'Synthetic reply', 'scenario': scenario})

    @staticmethod
    def confirmation(review):
        return {'mode': delivery.MODE, 'review_id': review['review_id'], 'digest': review['digest'], 'confirmed': True}

    def test_attachment_preview_atomic_import_repeat_and_dedup_bytes(self):
        before = snapshot(self.store)
        report = attachments.preview(self.store, self.bundle)
        self.assertEqual(report['new_links'], 2)
        self.assertEqual(snapshot(self.store), before)
        self.import_files()
        self.assertEqual(counts(self.store)['attachment_blobs'], 1)
        self.assertEqual(counts(self.store)['attachment_files'], 2)
        saved = snapshot(self.store)
        self.store = Store(self.root / 'work')
        self.assertTrue(self.import_files()['alreadyImported'])
        self.assertEqual(snapshot(self.store), saved)
        message = self.store.get_ticket(self.tid)['messages'][0]
        self.assertTrue(all(a['availability'] == 'private_copy' for a in message['attachment_records']))
        self.assertTrue(all('data' not in a for a in message['attachment_records']))

    def test_wrong_hash_size_identity_and_changed_preview_have_no_writes(self):
        before = snapshot(self.store)
        original = copy.deepcopy(self.manifest)
        for mutate in (lambda m: m['files'][1].update(sha256='0'*64),
                       lambda m: m['files'][1].update(attachment_id='unknown'),
                       lambda m: m['files'].append(copy.deepcopy(m['files'][0]))):
            self.manifest = copy.deepcopy(original)
            mutate(self.manifest)
            self.write_manifest()
            with self.assertRaises(Invalid):
                self.import_files()
            self.assertEqual(snapshot(self.store), before)
        self.manifest = original
        self.write_manifest()
        with self.assertRaises(Conflict):
            attachments.apply_import(self.store, self.bundle, 'wrong')
        (self.bundle / 'payload.bin').write_bytes(b'wrong size')
        for entry in self.manifest['files']:
            entry['sha256'] = checksum(b'wrong size')
        self.write_manifest()
        with self.assertRaises(Invalid):
            self.import_files()
        self.assertEqual(snapshot(self.store), before)

    def test_changed_existing_copy_is_refused(self):
        self.import_files()
        changed = b'X' * len(self.data)
        (self.bundle / 'payload.bin').write_bytes(changed)
        for entry in self.manifest['files']:
            entry['sha256'] = checksum(changed)
        self.write_manifest()
        with self.assertRaises(Conflict):
            self.import_files()
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT data FROM attachment_blobs').fetchone()[0], self.data)

    def test_links_traversal_urls_devices_and_oversized_payloads_refused(self):
        original = copy.deepcopy(self.manifest)
        for name in ('../payload.bin', '/etc/passwd', 'https://example.test/file', '.env'):
            self.manifest['files'][0]['file'] = name
            self.write_manifest()
            with self.assertRaises(Invalid):
                self.import_files()
        self.manifest = original
        self.write_manifest()
        path = self.bundle / 'payload.bin'
        path.unlink()
        path.symlink_to('/dev/null')
        with self.assertRaises((Invalid, OSError)):
            self.import_files()
        path.unlink()
        source = self.root / 'original.bin'
        source.write_bytes(self.data)
        os.link(source, path)
        with self.assertRaises(Invalid):
            self.import_files()
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(Invalid):
            self.import_files()
        path.unlink()
        with path.open('wb') as target:
            target.truncate(attachments.MAX_FILE + 1)
        with self.assertRaises(Invalid):
            self.import_files()
        self.assertEqual(counts(self.store)['attachment_files'], 0)

    def test_injected_attachment_storage_failure_rolls_back_all_bytes(self):
        before = snapshot(self.store)
        with patch.object(self.store, 'event', side_effect=sqlite3.OperationalError('disk failure')), self.assertRaises(sqlite3.Error):
            self.import_files()
        self.assertEqual(snapshot(self.store), before)
        self.assertEqual(self.import_files()['new_links'], 2)

    def test_concurrent_imports_and_explicit_local_copy(self):
        fingerprint = attachments.preview(self.store, self.bundle)['digest']
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(lambda _: attachments.apply_import(self.store, self.bundle, fingerprint), range(4)))
        self.assertEqual(sum(not r['alreadyImported'] for r in reports), 1)
        result = attachments.copy_file(self.store, self.ids[0], 'saved.bin')
        path = self.store.path.parent / 'copies' / 'saved.bin'
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(result['sha256'], checksum(self.data))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            attachments.copy_file(self.store, self.ids[0], 'saved.bin')

    def test_backup_restore_preserves_every_record_and_blob(self):
        self.import_files()
        before = snapshot(self.store)
        path, report = self.backup()
        self.assertEqual(recovery.verify(path)['digest'], report['digest'])
        recovery.restore(path, 'restored', report['digest'])
        restored = Store(recovery.workspace_path('restored'))
        after = snapshot(restored)
        self.assertEqual({k:v for k,v in before.items() if k!='sandbox_meta'}, {k:v for k,v in after.items() if k!='sandbox_meta'})
        self.assertEqual(check_database(restored.path)['attachment_bytes'], len(self.data))
        self.assertEqual(restored.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(restored.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(set(p.name for p in restored.path.parent.iterdir()), {'intake.sqlite3'})

    def test_restore_refuses_overwrite_and_needs_verified_digest(self):
        path, report = self.backup()
        with self.assertRaises(Conflict):
            recovery.restore(path, 'restored', 'wrong')
        self.assertFalse(recovery.workspace_path('restored').exists())
        recovery.restore(path, 'restored', report['digest'])
        with self.assertRaises(Conflict):
            recovery.restore(path, 'restored', report['digest'])
        with self.assertRaises(Conflict):
            self.backup()

    def test_corrupt_missing_or_linked_backup_never_publishes_restore(self):
        path, report = self.backup()
        dbfile = path / 'intake.sqlite3'
        original = dbfile.read_bytes()
        dbfile.write_bytes(original[:-100])
        with self.assertRaises(Invalid):
            recovery.restore(path, 'bad', report['digest'])
        self.assertFalse(recovery.workspace_path('bad').exists())
        dbfile.unlink()
        dbfile.symlink_to(self.store.path)
        with self.assertRaises((Invalid, OSError)):
            recovery.restore(path, 'bad', report['digest'])
        (path / 'manifest.json').unlink()
        with self.assertRaises((Invalid, OSError)):
            recovery.verify(path)

    def test_structural_checks_reject_damaged_blobs_or_weakened_indexes(self):
        self.import_files()
        with self.store.connection(write=True) as db:
            db.execute('UPDATE attachment_blobs SET data=?', (b'X'*len(self.data),))
        with self.assertRaises(Invalid):
            check_database(self.store.path)
        with self.assertRaises(Invalid):
            self.backup()
        self.assertFalse((self.store.path.parent/'backups'/'checkpoint').exists())
        with self.store.connection(write=True) as db:
            db.execute('UPDATE attachment_blobs SET data=?', (self.data,))
            db.execute('DROP INDEX one_unresolved_delivery')
            db.execute('CREATE INDEX one_unresolved_delivery ON delivery_attempts(ticket_id)')
        with self.assertRaises(Invalid):
            check_database(self.store.path)

    def test_interrupted_backup_or_restore_leaves_no_published_partial(self):
        with patch.object(recovery, 'publish', side_effect=OSError('interruption')), self.assertRaises(OSError):
            self.backup()
        self.assertFalse((self.store.path.parent/'backups'/'checkpoint').exists())
        path, report = self.backup()
        with patch.object(recovery, 'publish', side_effect=OSError('interruption')), self.assertRaises(OSError):
            recovery.restore(path, 'restored', report['digest'])
        self.assertFalse(recovery.workspace_path('restored').exists())
        recovery.restore(path, 'restored', report['digest'])
        self.assertEqual(check_database(recovery.workspace_path('restored')/'intake.sqlite3')['tables']['tickets'], 1)

    def test_wal_backup_includes_committed_uncheckpointed_state(self):
        # Keep a WAL connection open so the snapshot must include its WAL pages.
        db = sqlite3.connect(self.store.path)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute("UPDATE tickets SET assignee='WAL state' WHERE id=?", (self.tid,))
            db.commit()
            path, report = self.backup()
            recovery.restore(path, 'restored', report['digest'])
            self.assertEqual(Store(recovery.workspace_path('restored')).get_ticket(self.tid)['assignee'], 'WAL state')
        finally:
            db.close()

    def test_unknown_and_accepted_timeouts_recover_without_retry(self):
        for scenario in ('unknown', 'accepted_timeout'):
            reviewed = self.review(scenario, operation=scenario)
            result = delivery.confirm(self.store, self.tid, self.confirmation(reviewed))
            report = recovery.backup(self.store, scenario.replace('_','-'))
            path = self.store.path.parent/'backups'/scenario.replace('_','-')
            recovery.restore(path, scenario.replace('_','-'), report['digest'])
            restored = Store(recovery.workspace_path(scenario.replace('_','-')))
            before = counts(restored)
            with patch('intake.fake_delivery.dispatch', side_effect=AssertionError('No dispatch during recovery')):
                self.assertEqual(delivery.confirm(restored,self.tid,self.confirmation(reviewed))['state'],'uncertain')
                recovered = delivery.reconcile(restored,self.tid,{'mode':delivery.MODE,'attempt_id':result['attempt_id'],'confirmed':True})
                self.assertEqual(recovered['state'],'uncertain' if scenario=='unknown' else 'simulated_delivered')
                stable = snapshot(restored)
                delivery.reconcile(restored,self.tid,{'mode':delivery.MODE,'attempt_id':result['attempt_id'],'confirmed':True})
                self.assertEqual(snapshot(restored),stable)
            self.assertEqual(counts(restored)['fake_dispatches'],before['fake_dispatches'])
            # Fresh isolated source for the second scenario, preserving the first.
            if scenario == 'unknown':
                directory=self.root/'second'
                directory.mkdir()
                self.store=Store(directory)
                raw=payload([event('second')])
                self.tid=replay.run(self.store,raw,replay.run(self.store,raw,preview=True)['digest'])['results'][0]['ticket_id']

    def test_restored_pending_review_is_invalidated_and_new_review_works(self):
        reviewed = self.review()
        path, report = self.backup()
        # Simulate activity after the checkpoint. Recovery cannot know it happened.
        delivery.confirm(self.store,self.tid,self.confirmation(reviewed))
        recovery.restore(path,'restored',report['digest'])
        self.store=Store(recovery.workspace_path('restored'))
        with self.assertRaisesRegex(Conflict,'predates workspace recovery'):
            delivery.confirm(self.store,self.tid,self.confirmation(reviewed))
        newer=self.review(operation='fresh-review')
        self.assertEqual(delivery.confirm(self.store,self.tid,self.confirmation(newer))['state'],'simulated_delivered')

    def test_recovery_does_not_need_original_working_database(self):
        path, report = self.backup()
        self.store.path.rename(self.store.path.with_name('held-original.sqlite3'))
        recovery.restore(path,'restored',report['digest'])
        self.assertEqual(Store(recovery.workspace_path('restored')).get_ticket(self.tid)['subject'],'Same subject')


if __name__ == '__main__':
    unittest.main()
