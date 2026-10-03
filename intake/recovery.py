"""Self-contained private snapshots, validated before publication or restoration."""
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from .integrity import MAX_DATABASE, check_database, readonly
from .policy import Conflict, Invalid, ROOT
from .private_files import copy_regular, file_digest, json_bytes, private_write, read_bytes, regular_directory, sync_directory
from .records import canonical, digest, now, uid


def label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', value):
        raise Invalid('Use a 1–48 character lowercase backup/workspace name.')
    return value


def workspace_path(name):
    base = ROOT / '.local'
    base.mkdir(mode=0o700, exist_ok=True)
    regular_directory(base)
    os.chmod(base, 0o700)
    return base / label(name)


def backup_path(workspace, name):
    return workspace_path(workspace) / 'backups' / label(name)


def publish(stage, destination):
    if destination.exists() or destination.is_symlink():
        raise Conflict('Destination already exists. Choose a new name; nothing is overwritten.')
    # A competing completed snapshot/workspace is nonempty, so POSIX rename also
    # refuses to replace it even if it appeared after the existence check.
    os.rename(stage, destination)
    sync_directory(destination.parent)


def backup(store, name):
    destination = store.path.parent / 'backups' / label(name)
    directory = destination.parent
    if directory.is_symlink():
        raise Invalid('Backup directory cannot be a symbolic link.')
    directory.mkdir(mode=0o700, exist_ok=True)
    regular_directory(directory)
    os.chmod(directory, 0o700)
    if destination.exists() or destination.is_symlink():
        raise Conflict('Backup already exists. Choose a new name.')
    stage = Path(tempfile.mkdtemp(prefix='.backup-', dir=directory))
    try:
        path = stage / 'intake.sqlite3'
        # Hold the writer reservation while a separate read connection uses the
        # SQLite online backup API. Copying the raw live DB file is not safe.
        with store.connection(write=True):
            source = readonly(store.path)
            target = sqlite3.connect(path)
            os.chmod(path, 0o600)
            try:
                source.backup(target)
                # Normalize the copy so it never depends on WAL/SHM sidecars or
                # creates them during later read-only verification.
                target.execute('PRAGMA journal_mode=DELETE')
            finally:
                target.close()
                source.close()
        checks = check_database(path)
        with path.open('rb') as data:
            os.fsync(data.fileno())
        manifest = {'format': 'offline-intake-backup-v1', 'schema_version': 7, 'mode': 'offline_sandbox',
                    'created_at': now(), 'database': file_digest(path, MAX_DATABASE), 'checks': checks}
        private_write(stage / 'manifest.json', canonical(manifest).encode())
        sync_directory(stage)
        publish(stage, destination)
        return {'backup': name, 'digest': digest(manifest), **checks, 'outboundActions': 0}
    finally:
        if stage.exists():
            shutil.rmtree(stage)  # Only this invocation's private staging directory.


def manifest_at(directory):
    directory = regular_directory(directory)
    manifest = json_bytes(read_bytes(directory / 'manifest.json', 1024 * 1024))
    if (not isinstance(manifest, dict) or manifest.get('format') != 'offline-intake-backup-v1'
        or manifest.get('schema_version') not in (4, 5, 6, 7) or manifest.get('mode') != 'offline_sandbox'):
        raise Invalid('Not a completed offline intake backup.')
    database = manifest.get('database')
    if (not isinstance(database, dict) or set(database) != {'bytes', 'sha256'}
        or type(database['bytes']) is not int or not 0 < database['bytes'] <= MAX_DATABASE
        or not isinstance(database['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', database['sha256'])):
        raise Invalid('Backup database manifest is invalid.')
    if set(p.name for p in directory.iterdir()) != {'intake.sqlite3', 'manifest.json'}:
        raise Invalid('Backup contains unexpected or incomplete files.')
    return manifest


def verify(directory):
    manifest = manifest_at(directory)
    path = Path(directory) / 'intake.sqlite3'
    if file_digest(path, MAX_DATABASE) != manifest['database']:
        raise Invalid('Backup checksum mismatch. Restore refused.')
    checks = check_database(path)
    if checks != manifest.get('checks'):
        raise Invalid('Backup counts or recovery evidence do not match the manifest.')
    return {'digest': digest(manifest), **checks, 'outboundActions': 0}


def restore(directory, workspace, expected_digest):
    manifest = manifest_at(directory)
    if digest(manifest) != expected_digest:
        raise Conflict('Backup changed or verification digest missing. Verify it first.')
    destination = workspace_path(workspace)
    if destination.exists() or destination.is_symlink():
        raise Conflict('Restore requires a new workspace; existing workspaces are never overwritten.')
    stage = Path(tempfile.mkdtemp(prefix='.restore-', dir=destination.parent))
    try:
        copied = copy_regular(Path(directory) / 'intake.sqlite3', stage / 'intake.sqlite3', MAX_DATABASE)
        if copied != manifest['database']:
            raise Invalid('Backup changed or is corrupt. Restore refused.')
        checks = check_database(stage / 'intake.sqlite3')
        if checks != manifest.get('checks'):
            raise Invalid('Restored database does not match verified recovery evidence.')
        # Preserve every record. A fresh generation invalidates unconfirmed old
        # reviews; attempts and receipts still reconcile by their original IDs.
        db = sqlite3.connect(stage / 'intake.sqlite3')
        try:
            db.execute("INSERT OR REPLACE INTO sandbox_meta VALUES ('review_generation',?)", (uid(),))
            db.execute("INSERT OR REPLACE INTO sandbox_meta VALUES ('restored_from',?)",
                       (canonical({'digest': expected_digest, 'at': now()}),))
            # Also applies when an old snapshot is later migrated to E4. Claims
            # and admissions stay held until an explicit local resume.
            db.execute("INSERT OR REPLACE INTO sandbox_meta VALUES ('intake_hold',?)", (uid(),))
            if db.execute('PRAGMA user_version').fetchone()[0] >= 7:
                db.execute("UPDATE intake_jobs SET state='retry',lease_token=NULL,lease_until=NULL,error_code='restored_lease' WHERE state='leased'")
                db.execute('UPDATE adapter_keys SET active=0')
            if db.execute('PRAGMA user_version').fetchone()[0] >= 5:
                db.execute('DELETE FROM sessions')
                db.execute('DELETE FROM login_limits')
            db.commit()
        finally:
            db.close()
        check_database(stage / 'intake.sqlite3')
        with (stage / 'intake.sqlite3').open('rb') as data:
            os.fsync(data.fileno())
        sync_directory(stage)
        publish(stage, destination)
        return {'workspace': workspace, 'backup_digest': expected_digest, **checks,
                'old_unconfirmed_reviews_invalidated': True, 'sessions_revoked': True,
                'intake_held': True, 'outboundActions': 0}
    finally:
        if stage.exists():
            shutil.rmtree(stage)
