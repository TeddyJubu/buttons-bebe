"""Read-only structural, relational and attachment-byte validation for recovery."""
import json
from pathlib import Path
import sqlite3

from .policy import Invalid
from .private_files import checksum, file_digest
from .store import APPLICATION_ID
from .records import digest

MAX_DATABASE = 512 * 1024 * 1024


def readonly(path):
    connection = sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA trusted_schema=OFF')
    return connection


def check_database(path):
    # Also checks file type, size, links and every ancestor before SQLite opens it.
    file_digest(path, MAX_DATABASE)
    db = readonly(path)
    expected = sqlite3.connect(':memory:')
    try:
        db.execute('BEGIN')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID or version not in (4, 5, 6, 7):
            raise Invalid('Recovery requires a recognized schema-version-4-to-7 offline database.')
        if [r[0] for r in db.execute('PRAGMA integrity_check')] != ['ok'] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise Invalid('Database integrity or relationship checks failed.')
        expected.executescript(Path(__file__).with_name('schema.sql' if version == 7 else f'schema-v{version}.sql').read_text())
        objects = {(r[0], r[1]) for r in db.execute('SELECT type,name FROM sqlite_master')}
        allowed = {(r[0], r[1]) for r in expected.execute('SELECT type,name FROM sqlite_master')}
        if objects != allowed:
            raise Invalid('Unexpected database schema objects. Recovery refused.')
        indexes = {r[0]: ' '.join(r[1].split()) for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        wanted = {r[0]: ' '.join(r[1].split()) for r in expected.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        if indexes != wanted:
            raise Invalid('Database protection indexes differ from the supported schema.')
        tables = sorted(name for kind, name in allowed if kind == 'table')
        for table in tables:
            if [tuple(r) for r in db.execute('PRAGMA table_info(' + table + ')')] != list(expected.execute('PRAGMA table_info(' + table + ')')):
                raise Invalid('Unexpected database columns. Recovery refused.')
            if [tuple(r) for r in db.execute('PRAGMA foreign_key_list(' + table + ')')] != list(expected.execute('PRAGMA foreign_key_list(' + table + ')')):
                raise Invalid('Unexpected database relationships. Recovery refused.')
        if db.execute("SELECT value FROM sandbox_meta WHERE key='mode'").fetchone()[0] != 'offline_sandbox':
            raise Invalid('Only offline sandbox databases may be recovered.')
        blobs, byte_count = 0, 0
        for row in db.execute('SELECT * FROM attachment_blobs'):
            if not isinstance(row['data'], bytes) or checksum(row['data']) != row['sha256'] or len(row['data']) != row['byte_size']:
                raise Invalid('Stored attachment bytes failed checksum validation.')
            blobs += 1
            byte_count += row['byte_size']
        mismatch = db.execute('''SELECT 1 FROM attachments a LEFT JOIN attachment_files f ON f.attachment_id=a.id
            WHERE a.availability != CASE WHEN f.attachment_id IS NULL THEN 'metadata_only' ELSE 'private_copy' END''').fetchone()
        if mismatch:
            raise Invalid('Attachment availability does not match stored file evidence.')
        broken = db.execute('''SELECT 1 FROM delivery_attempts a JOIN delivery_reviews r ON r.id=a.review_id
            LEFT JOIN fake_dispatches d ON d.attempt_id=a.id
            LEFT JOIN messages m ON m.id=a.message_id
            LEFT JOIN simulated_outgoing s ON s.attempt_id=a.id
            WHERE a.ticket_id!=r.ticket_id OR (d.attempt_id IS NOT NULL AND d.digest!=r.digest)
            OR (a.state='failed' AND (d.outcome IS NULL OR d.outcome!='rejected'))
            OR (a.state='simulated_delivered' AND (d.outcome IS NULL OR d.outcome!='accepted'
                OR m.id IS NULL OR m.ticket_id!=a.ticket_id OR m.origin!='offline_simulation' OR m.kind!='outgoing'
                OR s.message_id IS NULL OR s.message_id!=a.message_id))
            OR (a.state!='simulated_delivered' AND (a.message_id IS NOT NULL OR s.message_id IS NOT NULL))''').fetchone()
        if broken:
            raise Invalid('Delivery ledger and fake receipt evidence are inconsistent.')
        if version >= 6:
            broken = db.execute('''SELECT 1 FROM assistance_runs r JOIN assistance_fixtures f ON f.id=r.fixture_id
                WHERE r.ticket_id!=f.ticket_id OR r.input_digest!=f.input_digest''').fetchone()
            linked = db.execute('''SELECT 1 FROM assistance_reviews l JOIN delivery_reviews d ON d.id=l.review_id
                JOIN assistance_runs r ON r.id=l.run_id WHERE d.ticket_id!=r.ticket_id''').fetchone()
            if broken or linked:
                raise Invalid('Assistance records do not belong to the same independent ticket.')
            for row in db.execute('SELECT * FROM assistance_fixtures'):
                value = json.loads(row['payload_json'])
                if (digest(value)!=row['digest'] or value['ticket_id']!=row['ticket_id']
                    or value['input_digest']!=row['input_digest'] or value['fixture_id']!=row['id']
                    or value['mode']!='offline_fixture' or value['format']!='intake-assistance-fixture-v1'):
                    raise Invalid('Saved assistance fixture evidence is inconsistent.')
            for row in db.execute('SELECT r.*,f.digest AS fixture_digest,f.payload_json FROM assistance_runs r JOIN assistance_fixtures f ON f.id=r.fixture_id'):
                request = json.loads(row['request_json'])
                result = json.loads(row['result_json'])
                fixture = json.loads(row['payload_json'])
                if (request['mode']!='offline_fixture' or request['run_id']!=row['id']
                    or request['input']['ticket']['id']!=row['ticket_id']
                    or request['input_digest']!=row['input_digest'] or digest(request['input'])!=row['input_digest']
                    or request['fixture_digest']!=row['fixture_digest'] or request['context']!=fixture['context']
                    or (row['state']=='running' and (result!={} or row['completed_at'] is not None))
                    or (row['state']!='running' and (result.get('state')!=row['state'] or not row['completed_at']))
                    or (row['state']!='ready' and result.get('body',''))):
                    raise Invalid('Saved assistance request/result bindings are inconsistent.')
        if version >= 7:
            from .intake_jobs import check_integrity
            check_integrity(db)
        return {'tables': {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in tables},
                'attachment_blobs': blobs, 'attachment_bytes': byte_count,
                'unresolved_attempts': db.execute("SELECT count(*) FROM delivery_attempts WHERE state IN ('attempting','uncertain')").fetchone()[0]}
    except (sqlite3.Error, TypeError, IndexError, KeyError, ValueError) as exc:
        raise Invalid('Offline database validation failed.') from exc
    finally:
        expected.close()
        db.close()
