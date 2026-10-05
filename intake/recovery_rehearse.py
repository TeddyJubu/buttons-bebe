"""Private saved-data recovery proof with separate synthetic attachment bytes."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import sqlite3

from . import attachments, delivery, recovery, replay
from .integrity import MAX_DATABASE, check_database, readonly
from .policy import Conflict, Invalid, install_offline_guard
from .private_files import checksum, file_digest, private_write
from .records import canonical
from .replay_rehearse import snapshot
from .store import APPLICATION_ID, Store


def seed(store, name, files=None):
    raw = {'mode': 'offline_replay', 'account': 'recovery-synthetic', 'mailbox': 'support@example.test',
        'events': [{'event_id': name, 'provider': 'simulation', 'subject': 'Synthetic recovery fixture',
            'message': {'id': name, 'public': True, 'from_agent': False, 'channel': 'email',
                'created_datetime': '2026-09-01T12:00:00Z', 'body_text': 'Fabricated recovery test input.',
                'sender': {'name': 'Recovery Test', 'email': 'customer@example.test'},
                'source': {'from': {'address': 'customer@example.test'}, 'to': [{'address': 'support@example.test'}]},
                'headers': {'Message-ID': '<' + name + '@example.test>'}, 'attachments': files or []}}]}
    data = canonical(raw).encode()
    return replay.run(store, data, replay.run(store, data, preview=True)['digest'])['results'][0]['ticket_id']


def reviewed(store, tid, name, scenario):
    review = delivery.review(store, tid, {'mode': delivery.MODE, 'operation_id': name,
        'revision': store.get_ticket(tid)['revision'], 'body': 'Synthetic recovery reply.', 'scenario': scenario})
    return {'mode': delivery.MODE, 'review_id': review['review_id'], 'digest': review['digest'], 'confirmed': True}


def rehearse(source_workspace, workspace):
    source = recovery.workspace_path(source_workspace) / 'intake.sqlite3'
    source_hash = file_digest(source, MAX_DATABASE)
    destination = recovery.workspace_path(workspace)
    restored_name = recovery.label(workspace + '-restored')
    rejected_name = recovery.label(workspace + '-rejected')
    if destination.exists() or recovery.workspace_path(restored_name).exists():
        raise Invalid('Recovery proof requires new source-copy and restored workspaces.')
    destination.mkdir(mode=0o700)
    incoming = readonly(source)
    outgoing = sqlite3.connect(destination / 'intake.sqlite3')
    os.chmod(destination / 'intake.sqlite3', 0o600)
    try:
        if incoming.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
            raise Invalid('Proof source is not an intake workspace.')
        incoming.backup(outgoing)
        outgoing.execute('PRAGMA journal_mode=DELETE')
    finally:
        outgoing.close()
        incoming.close()
    # Only this fresh clone is upgraded. Original D2-D5 proof files are untouched.
    store = Store(destination)
    checks, failures = Counter(), Counter()
    def check(name, value):
        checks[name] += 1
        if not value:
            failures[name] += 1
    initial = check_database(store.path)
    with store.connection() as db:
        original_sources = [tuple(r) for r in db.execute('SELECT * FROM source_records ORDER BY kind,account,external_id')]
    data = b'Private synthetic recovery attachment\x00\xff\n'
    tid = seed(store, 'recovery-files', [{'name': 'recovery-a.bin', 'size': len(data)}, {'name': 'recovery-b.bin', 'size': len(data)}])
    with store.connection() as db:
        ids = [r[0] for r in db.execute('SELECT a.id FROM attachments a JOIN messages m ON m.id=a.message_id WHERE m.ticket_id=?', (tid,))]
    bundle = destination / 'synthetic-bundle'
    bundle.mkdir(mode=0o700)
    private_write(bundle / 'payload.bin', data)
    manifest = {'mode': 'offline_attachment_import', 'files': [
        {'attachment_id': aid, 'file': 'payload.bin', 'sha256': checksum(data)} for aid in ids]}
    private_write(bundle / 'manifest.json', canonical(manifest).encode())
    before = snapshot(store)
    preview = attachments.preview(store, bundle)
    check('attachment_preview_no_writes', before == snapshot(store))
    attachments.apply_import(store, bundle, preview['digest'])
    before = snapshot(store)
    attachments.apply_import(store, bundle, preview['digest'])
    check('attachment_repeat_rows_identical', before == snapshot(store))
    check('identical_bytes_stored_once', check_database(store.path)['attachment_blobs'] == 1)
    pending = reviewed(store, tid, 'pending-before-backup', 'accepted')
    uncertain_tid = seed(store, 'recovery-accepted-receipt')
    uncertain_request = reviewed(store, uncertain_tid, 'receipt-before-backup', 'accepted_timeout')
    uncertain = delivery.confirm(store, uncertain_tid, uncertain_request)
    check('accepted_receipt_still_uncertain_before_backup', uncertain['state'] == 'uncertain')
    before = snapshot(store)
    backup = recovery.backup(store, 'checkpoint')
    backup_path = destination / 'backups' / 'checkpoint'
    check('backup_verify_matches', recovery.verify(backup_path)['digest'] == backup['digest'])
    check('backup_has_no_source_side_effects', before == snapshot(store))
    # A later confirmation illustrates the checkpoint boundary. Its old review
    # must not be usable in the restored snapshot where the attempt is absent.
    delivery.confirm(store, tid, pending)
    bad = destination / 'backups' / 'corrupted-proof'
    shutil.copytree(backup_path, bad)
    with (bad / 'intake.sqlite3').open('r+b') as target:
        target.truncate(100)
    try:
        recovery.restore(bad, rejected_name, backup['digest'])
    except Invalid:
        check('corrupt_backup_rejected', True)
    else:
        check('corrupt_backup_rejected', False)
    check('corrupt_restore_published_nothing', not recovery.workspace_path(rejected_name).exists())
    recovery.restore(backup_path, restored_name, backup['digest'])
    restored = Store(recovery.workspace_path(restored_name))
    after = snapshot(restored)
    for table, rows in before.items():
        if table != 'sandbox_meta':
            check('original_table_rows_preserved', rows == after[table])
    with restored.connection() as db:
        check('source_records_preserved', original_sources == [tuple(r) for r in db.execute('SELECT * FROM source_records ORDER BY kind,account,external_id')])
    for index, aid in enumerate(ids):
        attachments.copy_file(restored, aid, f'recovered-{index}.bin')
        check('restored_file_bytes_match', (restored.path.parent / 'copies' / f'recovered-{index}.bin').read_bytes() == data)
    try:
        delivery.confirm(restored, tid, pending)
    except Conflict:
        check('old_pending_review_invalidated', True)
    else:
        check('old_pending_review_invalidated', False)
    repeated = delivery.confirm(restored, uncertain_tid, uncertain_request)
    check('repeat_confirm_does_not_dispatch', repeated['state'] == 'uncertain')
    result = delivery.reconcile(restored, uncertain_tid, {'mode': delivery.MODE, 'attempt_id': uncertain['attempt_id'], 'confirmed': True})
    check('accepted_receipt_recovers_one_message', result['state'] == 'simulated_delivered')
    before = snapshot(restored)
    restored = Store(recovery.workspace_path(restored_name))
    with restored.connection() as db:
        attempts = [dict(r) for r in db.execute('SELECT a.id,a.ticket_id,r.id AS review_id,r.digest,a.state FROM delivery_attempts a JOIN delivery_reviews r ON r.id=a.review_id')]
    for attempt in attempts:
        delivery.confirm(restored, attempt['ticket_id'], {'mode': delivery.MODE, 'review_id': attempt['review_id'], 'digest': attempt['digest'], 'confirmed': True})
        reconciled = delivery.reconcile(restored, attempt['ticket_id'], {'mode': delivery.MODE, 'attempt_id': attempt['id'], 'confirmed': True})
        check('attempt_state_stable_after_restart', reconciled['state'] == attempt['state'])
    check('restart_repeated_confirm_reconcile_rows_identical', before == snapshot(restored))
    final = check_database(restored.path)
    check('original_unknown_outcomes_still_blocked', final['unresolved_attempts'] == initial['unresolved_attempts'])
    check('no_extra_fake_dispatch', final['tables']['fake_dispatches'] == backup['tables']['fake_dispatches'])
    check('original_source_database_unchanged', file_digest(source, MAX_DATABASE) == source_hash)
    report = {'passed': not failures, 'checks': dict(checks), 'failures': dict(failures),
        'source_workspace': source_workspace, 'workspace': workspace, 'restored_workspace': restored_name,
        'initial': initial, 'backup': backup, 'restored': final,
        'source_attachment_bytes_available': 0, 'synthetic_attachment_links': len(ids), 'outboundActions': 0,
        'limitations': ['Original export contains attachment metadata only; customer attachment bytes remain unverified.',
                        'Only the self-contained database is backed up; reports, extracted copies and source export files are separate.',
                        'Post-checkpoint activity is absent from the restored copy; old unconfirmed reviews are invalidated.',
                        'Local private backup is not encrypted or offsite disaster recovery.']}
    path = destination / 'recovery-reconciliation.json'
    private_write(path, canonical(report).encode())
    return report


def main():
    parser = argparse.ArgumentParser(description='Offline attachment and recovery proof against an existing saved-data workspace.')
    parser.add_argument('--source-workspace', required=True)
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    install_offline_guard()
    os.umask(0o077)
    try:
        report = rehearse(args.source_workspace, args.workspace)
    except (Invalid, OSError, ValueError, KeyError, sqlite3.Error):
        print('Recovery rehearsal failed. Private evidence is retained; no external operation was attempted.')
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
