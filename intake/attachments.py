"""Atomic local attachment-file imports. Bytes remain opaque inside SQLite."""
import json
import os
from pathlib import Path
import re

from .policy import Conflict, Invalid
from .private_files import checksum, filename, json_bytes, private_write, read_bytes, regular_directory
from .records import canonical, digest, now, uid

MAX_FILE = 25 * 1024 * 1024
MAX_BATCH = 100 * 1024 * 1024


def prepare(bundle):
    bundle = regular_directory(bundle)
    manifest = json_bytes(read_bytes(bundle / 'manifest.json', 1024 * 1024))
    if not isinstance(manifest, dict) or set(manifest) != {'mode', 'files'} or manifest.get('mode') != 'offline_attachment_import':
        raise Invalid('Attachment imports require the offline_attachment_import manifest.')
    entries = manifest['files']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 25:
        raise Invalid('Import 1–25 local attachment files per bundle.')
    files, seen, total = [], set(), 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'attachment_id', 'file', 'sha256'}:
            raise Invalid('Each file needs an attachment_id, plain filename and SHA-256 checksum.')
        aid = entry['attachment_id']
        if not isinstance(aid, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,128}', aid) or aid in seen:
            raise Invalid('Attachment IDs must be valid and unique within the bundle.')
        seen.add(aid)
        name = filename(entry['file'])
        if name == 'manifest.json':
            raise Invalid('The manifest cannot also be an attachment payload.')
        if not isinstance(entry['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
            raise Invalid('A lowercase SHA-256 checksum is required for each file.')
        data = read_bytes(bundle / name, MAX_FILE)
        total += len(data)
        if total > MAX_BATCH:
            raise Invalid('Attachment bundle exceeds 100 MiB.')
        if checksum(data) != entry['sha256']:
            raise Invalid('Attachment checksum mismatch. Nothing was imported.')
        files.append({'id': aid, 'sha256': entry['sha256'], 'bytes': len(data), 'data': data})
    fingerprint = digest({'manifest': manifest, 'files': [{k: v for k, v in f.items() if k != 'data'} for f in files]})
    return files, fingerprint


def plan(db, files, fingerprint):
    report = {'mode': 'offline_attachment_import', 'digest': fingerprint, 'files': len(files),
              'bytes': sum(f['bytes'] for f in files), 'new_links': 0, 'duplicates': 0,
              'source_size_verified': 0, 'source_size_unavailable': 0, 'outboundActions': 0}
    for item in files:
        row = db.execute('''SELECT a.*,f.sha256 FROM attachments a
                            LEFT JOIN attachment_files f ON f.attachment_id=a.id WHERE a.id=?''', (item['id'],)).fetchone()
        if not row:
            raise Invalid('An attachment ID does not exist in this workspace.')
        metadata = json.loads(row['metadata_json'])
        size = metadata.get('size')
        if type(size) is int and size >= 0:
            if size != item['bytes']:
                raise Invalid('Local bytes differ from the recorded attachment size. Nothing was imported.')
            report['source_size_verified'] += 1
        else:
            report['source_size_unavailable'] += 1
        if row['sha256'] and row['sha256'] != item['sha256']:
            raise Conflict('This attachment already has a different private copy. Overwrites are refused.')
        blob = db.execute('SELECT byte_size,data FROM attachment_blobs WHERE sha256=?', (item['sha256'],)).fetchone()
        if blob and (blob['byte_size'] != item['bytes'] or checksum(blob['data']) != item['sha256']):
            raise Invalid('A stored attachment copy is damaged. Restore a verified backup.')
        report['duplicates' if row['sha256'] else 'new_links'] += 1
    return report


def preview(store, bundle):
    files, fingerprint = prepare(bundle)
    with store.connection() as db:
        return plan(db, files, fingerprint)


def apply_import(store, bundle, expected_digest):
    files, fingerprint = prepare(bundle)
    if fingerprint != expected_digest:
        raise Conflict('Attachment bundle changed or preview digest is missing. Preview again.')
    with store.connection(write=True) as db:
        report = plan(db, files, fingerprint)
        if db.execute('SELECT 1 FROM attachment_imports WHERE digest=?', (fingerprint,)).fetchone():
            return {**report, 'alreadyImported': True}
        tickets = set()
        at = now()
        for item in files:
            if db.execute('SELECT 1 FROM attachment_files WHERE attachment_id=?', (item['id'],)).fetchone():
                continue
            db.execute('INSERT OR IGNORE INTO attachment_blobs VALUES (?,?,?)', (item['sha256'], item['bytes'], item['data']))
            db.execute('INSERT INTO attachment_files VALUES (?,?,?)', (item['id'], item['sha256'], at))
            db.execute("UPDATE attachments SET availability='private_copy' WHERE id=?", (item['id'],))
            tid = db.execute('SELECT m.ticket_id FROM attachments a JOIN messages m ON m.id=a.message_id WHERE a.id=?', (item['id'],)).fetchone()[0]
            tickets.add(tid)
        for tid in tickets:
            db.execute('UPDATE tickets SET revision=revision+1 WHERE id=?', (tid,))
            store.event(db, tid, 'attachment_files_imported', {'digest': fingerprint}, 'Offline attachment import')
        db.execute('INSERT INTO attachment_imports VALUES (?,?,?,?)', (uid(), fingerprint, at, canonical(report)))
        return {**report, 'alreadyImported': False}


def inventory(store):
    """Save identifiers/filenames privately instead of printing customer metadata."""
    with store.connection() as db:
        entries = [dict(r) for r in db.execute('''SELECT a.id AS attachment_id,a.message_id,m.ticket_id,
            a.metadata_json,a.availability,f.sha256 FROM attachments a JOIN messages m ON m.id=a.message_id
            LEFT JOIN attachment_files f ON f.attachment_id=a.id ORDER BY m.sequence,a.rowid''')]
    path = store.path.parent / ('attachment-index-' + uid() + '.json')
    private_write(path, canonical({'format': 'private-attachment-index-v1', 'attachments': entries}).encode())
    return {'attachments': len(entries), 'private_index': str(path), 'outboundActions': 0}


def copy_file(store, attachment_id, name):
    """Explicit local extraction; no browser rendering, MIME handling or execution."""
    name = filename(name)
    with store.connection() as db:
        blob = db.execute('''SELECT b.* FROM attachment_files f JOIN attachment_blobs b ON b.sha256=f.sha256
                             WHERE f.attachment_id=?''', (attachment_id,)).fetchone()
        if not blob:
            raise Invalid('No private copy exists for this attachment.')
        if checksum(blob['data']) != blob['sha256'] or len(blob['data']) != blob['byte_size']:
            raise Invalid('Private attachment checksum mismatch. Restore a verified backup.')
    directory = store.path.parent / 'copies'
    if directory.is_symlink():
        raise Invalid('Private copy directory cannot be a link.')
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    private_write(directory / name, blob['data'])
    return {'bytes': blob['byte_size'], 'sha256': blob['sha256'], 'outboundActions': 0}
