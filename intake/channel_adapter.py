"""Provider-neutral boundary exercised only by an in-process, locally signed fake.

No SDK, URL, receiver, polling, environment configuration or transport selection.
The signature scheme is a fixture protocol, not a claim about any email vendor.
"""
import hashlib
import hmac
import re
import secrets
import time

from . import auth, replay
from .imports import MAX_BYTES, account_name
from .policy import Conflict, Invalid
from .private_files import checksum, json_bytes
from .records import canonical, email, now, text, uid

FORMAT = 'offline-channel-envelope-v1'
MAX_FRAME = 2 * MAX_BYTES
MAX_PENDING = 10000
STATUSES = ('accepted', 'deferred', 'delivered', 'bounced', 'complained')


def local_only():
    if auth.current.get():
        raise auth.Denied('Channel fixtures and workers are local operator commands only.')


def create_channel(store, account, mailbox, provider):
    local_only()
    account, provider = account_name(account), account_name(provider)
    mailbox = email(mailbox).casefold()
    if not mailbox:
        raise Invalid('A fake channel requires a mailbox.')
    with store.connection(write=True) as db:
        old = db.execute('SELECT id FROM adapter_channels WHERE account=? AND mailbox=? AND provider=?',
                         (account, mailbox, provider)).fetchone()
        if old:
            return old[0]
        cid = uid()
        db.execute('INSERT INTO adapter_channels VALUES (?,?,?,?,?)', (cid, account, mailbox, provider, now()))
        store.event(db, None, 'fake_channel_created', {'channel_id': cid})
        return cid


def rotate_key(store, channel_id, *, revoke_previous=False):
    local_only()
    with store.connection(write=True) as db:
        if not db.execute('SELECT 1 FROM adapter_channels WHERE id=?', (channel_id,)).fetchone():
            raise Invalid('Fake channel not found.')
        if revoke_previous:
            db.execute('UPDATE adapter_keys SET active=0 WHERE channel_id=?', (channel_id,))
        kid = uid()
        # Generated here, never supplied from production or printed. Backup is private.
        db.execute('INSERT INTO adapter_keys VALUES (?,?,?,1,?)', (kid, channel_id, secrets.token_hex(32), now()))
        store.event(db, None, 'fake_channel_key_rotated', {'channel_id': channel_id, 'previous_revoked': revoke_previous})
        return kid


def signature(secret, frame):
    metadata = [FORMAT, frame['channel_id'], frame['key_id'], frame['delivery_id'], frame['purpose'], frame['signed_at']]
    try:
        data = canonical(metadata).encode() + b'\n' + frame['body'].encode()
    except UnicodeError as exc:
        raise Invalid('Fake envelope contains invalid Unicode.') from exc
    return hmac.new(bytes.fromhex(secret), data, hashlib.sha256).hexdigest()


def make_fixture(store, key_id, body, purpose='inbound', *, delivery_id=None):
    """Explicit local test producer. It cannot contact or impersonate a live provider."""
    local_only()
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise Invalid('Fake body exceeds its size limit.')
    try:
        raw = body.decode('utf8')
    except UnicodeError as exc:
        raise Invalid('Fake body must be UTF-8.') from exc
    with store.connection() as db:
        key = db.execute('SELECT * FROM adapter_keys WHERE id=? AND active=1', (key_id,)).fetchone()
        if not key:
            raise Invalid('Active fake signing key not found.')
        frame = {'format': FORMAT, 'channel_id': key['channel_id'], 'key_id': key_id,
                 'delivery_id': delivery_id or uid(), 'purpose': purpose, 'signed_at': int(time.time()), 'body': raw}
        frame['signature'] = signature(key['secret'], frame)
        return canonical(frame).encode()


def verify_frame(db, data, *, freshness=True):
    if not isinstance(data, bytes) or len(data) > MAX_FRAME:
        raise Invalid('Fake envelope exceeds its size limit.')
    frame = json_bytes(data)
    fields = {'format', 'channel_id', 'key_id', 'delivery_id', 'purpose', 'signed_at', 'body', 'signature'}
    if not isinstance(frame, dict) or set(frame) != fields or frame['format'] != FORMAT:
        raise Invalid('Use an explicit offline channel envelope.')
    for field in ('channel_id', 'key_id', 'delivery_id', 'signature'):
        if text(frame[field], field, 128, True) != frame[field]:
            raise Invalid('Envelope identifiers must be exact.')
    if not re.fullmatch(r'[a-f0-9]{64}', frame['signature']):
        raise Invalid('Invalid fake envelope signature.')
    if (frame['purpose'] not in ('inbound', 'receipt') or type(frame['signed_at']) is not int
        or not isinstance(frame['body'], str)):
        raise Invalid('Invalid fake envelope metadata.')
    try:
        body_size = len(frame['body'].encode())
    except UnicodeError as exc:
        raise Invalid('Fake envelope contains invalid Unicode.') from exc
    if body_size > MAX_BYTES:
        raise Invalid('Fake body exceeds its size limit.')
    key = db.execute('SELECT * FROM adapter_keys WHERE id=? AND channel_id=?',
                     (frame['key_id'], frame['channel_id'])).fetchone()
    if not key or (freshness and not key['active']) or not hmac.compare_digest(signature(key['secret'], frame), frame['signature']):
        raise Invalid('Fake envelope authentication failed.')
    if freshness and abs(int(time.time()) - frame['signed_at']) > 300:
        raise Invalid('Fake envelope is outside its five-minute signing window.')
    channel = db.execute('SELECT * FROM adapter_channels WHERE id=?', (frame['channel_id'],)).fetchone()
    return frame, channel


def normalize(frame, channel):
    try:
        return _normalize(frame, channel)
    except UnicodeError as exc:
        raise Invalid('Fake event contains invalid Unicode.') from exc


def _normalize(frame, channel):
    if frame['purpose'] == 'inbound':
        account, mailbox, events, _ = replay.prepare(frame['body'].encode())
        if (account != channel['account'] or mailbox != channel['mailbox']
            or any(e['provider'] != channel['provider'] for e in events)):
            raise Invalid('Authenticated channel does not match the message namespace.')
        return [(e['id'], e['raw']) for e in events]
    value = json_bytes(frame['body'].encode())
    fields = {'mode', 'attempt_id', 'review_digest', 'receipt_id', 'status'}
    if not isinstance(value, dict) or set(value) != fields or value['mode'] != 'offline_receipt':
        raise Invalid('Use an explicit offline receipt observation.')
    for field in ('attempt_id', 'review_digest', 'receipt_id'):
        if text(value[field], field, 128, True) != value[field]:
            raise Invalid('Receipt identifiers must be exact.')
    if value['status'] not in STATUSES:
        raise Invalid('Unsupported fake receipt status.')
    return [(frame['delivery_id'], value)]


def enqueue(store, data, expected_digest=None, *, preview=False):
    local_only()
    if not isinstance(data, bytes) or len(data) > MAX_FRAME:
        raise Invalid('Fake envelope exceeds its size limit.')
    fingerprint = checksum(data)
    if not preview and expected_digest != fingerprint:
        raise Conflict('Fake envelope changed or preview digest missing.')
    with store.connection(write=True) as db:
        if db.execute("SELECT 1 FROM sandbox_meta WHERE key='intake_hold'").fetchone():
            raise Conflict('Recovered intake is held. Inspect the recovery plan before resuming locally.')
        frame, channel = verify_frame(db, data)
        events = normalize(frame, channel)
        previous = db.execute('SELECT * FROM adapter_envelopes WHERE channel_id=? AND delivery_id=?',
                              (channel['id'], frame['delivery_id'])).fetchone()
        body_digest = checksum(frame['body'].encode())
        if previous and (previous['digest'] != body_digest or previous['purpose'] != frame['purpose']):
            raise Conflict('A delivery ID was reused with different contents.')
        envelope_id = previous['id'] if previous else uid()
        at = int(time.time())
        if not previous:
            db.execute('INSERT INTO adapter_envelopes VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (envelope_id, channel['id'], frame['delivery_id'], frame['key_id'], frame['purpose'],
                        frame['signed_at'], frame['body'], body_digest, frame['signature'], at))
        queued = duplicates = 0
        from .records import digest
        for event_id, event in events:
            payload_digest = digest(event)
            old = db.execute('SELECT digest FROM intake_jobs WHERE channel_id=? AND kind=? AND event_id=?',
                             (channel['id'], frame['purpose'], event_id)).fetchone()
            if old:
                if old[0] != payload_digest:
                    raise Conflict('An intake event ID was reused with different contents.')
                duplicates += 1
                continue
            db.execute('''INSERT INTO intake_jobs(id,channel_id,envelope_id,event_id,kind,payload_json,digest,state,
                available_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'queued',?,?,?)''',
                (uid(), channel['id'], envelope_id, event_id, frame['purpose'], canonical(event), payload_digest, at, at, at))
            queued += 1
        if queued and db.execute("SELECT count(*) FROM intake_jobs WHERE state!='done'").fetchone()[0] > MAX_PENDING:
            raise Conflict('Local intake capacity reached. Recover pending or dead jobs before adding more.')
        if queued:
            store.event(db, None, 'fake_envelope_queued', {'envelope_id': envelope_id, 'jobs': queued})
        if preview:
            db.rollback()
        # A future gateway may acknowledge only after this transaction commits.
    return {'mode': 'offline_sandbox', 'digest': fingerprint, 'phase': 'preview' if preview else 'queued',
            'queued': queued, 'duplicates': duplicates, 'outboundActions': 0}
