"""Workspace-local identities. No production credentials, invites or provider I/O."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import hmac
import re
import secrets
import time

from .policy import Invalid, Conflict
from .records import now, text, uid

current = ContextVar('intake_identity', default=None)
SESSION_SECONDS = 8 * 60 * 60
ROLES = {'viewer': {'read'}, 'agent': {'read', 'work'}, 'admin': {'read', 'work', 'import'}}


class Denied(Invalid):
    status = 403


class Unauthorized(Denied):
    status = 401


class Throttled(Denied):
    status = 429


def key(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise Invalid('Use a sandbox-only password of 12–1024 characters.')
    salt = salt or secrets.token_hex(16)
    derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64*1024*1024)
    return salt + ':' + derived.hex()


def matches(password, saved):
    valid_input = isinstance(password, str) and 12 <= len(password) <= 1024
    if not valid_input:
        password = 'invalid-password-input'
    correct = hmac.compare_digest(password_hash(password, saved.split(':')[0]), saved)
    # Keep the derivation work for invalid inputs, but never authenticate the
    # replacement text even if somebody chose that literal as their password.
    return valid_input and correct


# Equal work for unknown usernames; not a usable user credential.
DUMMY_HASH = password_hash(secrets.token_urlsafe(32))


def public(row):
    return {field: row[field] for field in ('id', 'username', 'name', 'role', 'active')}


def actor_name():
    return current.get()['name'] if current.get() else 'Sandbox operator'


def provision(store, username, name, role, password=None, active=True):
    """OS-owner CLI administration; never exposed over HTTP."""
    if not isinstance(username, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', username):
        raise Invalid('Username must be 1–64 lowercase letters, digits, dots, underscores or dashes.')
    name = text(name, 'name', 100, True)
    if role not in ROLES or type(active) is not bool:
        raise Invalid('Choose admin, agent or viewer and an active state.')
    hashed = password_hash(password) if password is not None else None
    with store.connection(write=True) as db:
        old = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if not old and not hashed:
            raise Invalid('New users require a password.')
        if old and old['active'] and old['role'] == 'admin' and (not active or role != 'admin'):
            if db.execute("SELECT count(*) FROM users WHERE active=1 AND role='admin'").fetchone()[0] <= 1:
                raise Conflict('Keep at least one active administrator.')
        ident = old['id'] if old else uid()
        db.execute('''INSERT INTO users VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,role=excluded.role,password_hash=excluded.password_hash,active=excluded.active''',
            (ident, username, name, role, hashed or old['password_hash'], int(active), old['created_at'] if old else now()))
        # All edits revoke sessions, including role, name and password changes.
        db.execute('DELETE FROM sessions WHERE user_id=?', (ident,))
        store.event(db, None, 'local_user_updated' if old else 'local_user_created',
                    {'user_id': ident, 'role': role, 'active': active}, 'Local account administrator')
        return {'id': ident, 'username': username, 'name': name, 'role': role, 'active': int(active)}


def login(store, username, password, audience):
    if not isinstance(username, str) or len(username) > 64 or not isinstance(password, str) or len(password) > 1024:
        raise Unauthorized('Invalid sandbox username or password.')
    at = int(time.time())
    denied = False
    with store.connection(write=True) as db:
        db.execute('DELETE FROM login_limits WHERE window_start<=?', (at - 60,))
        db.execute('DELETE FROM sessions WHERE expires_at<=? OR audience!=?', (at, audience))
        for bucket, maximum in (('global', 20), ('user:' + key(username), 5)):
            row = db.execute('SELECT failures FROM login_limits WHERE key=?', (bucket,)).fetchone()
            if row and row[0] >= maximum:
                raise Throttled('Too many sign-in attempts. Wait one minute and try again.')
        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        correct = matches(password, user['password_hash'] if user else DUMMY_HASH)
        if not user or not user['active'] or not correct:
            for bucket in ('global', 'user:' + key(username)):
                db.execute('''INSERT INTO login_limits VALUES (?,?,1) ON CONFLICT(key)
                    DO UPDATE SET failures=failures+1''', (bucket, at))
            denied = True
        else:
            raw = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (key(raw), user['id'], audience, at + SESSION_SECONDS))
            store.event(db, None, 'local_sign_in', {'user_id': user['id']}, user['name'])
    if denied:
        raise Unauthorized('Invalid sandbox username or password.')
    return raw


def identity(store, raw, audience):
    with store.connection() as db:
        row = db.execute('''SELECT u.*,s.id AS session_id,s.expires_at,s.audience FROM sessions s
            JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.audience=? AND s.expires_at>? AND u.active=1''',
            (key(raw), audience, int(time.time()))).fetchone()
        if not row:
            raise Unauthorized('Sign in to this offline workspace.')
        return {**public(row), 'session_id': row['session_id'], 'expires_at': row['expires_at'], 'audience': audience, 'permission': 'read'}


def require(db):
    """Recheck inside the same read/write transaction; revocation cannot race a write."""
    actor = current.get()
    row = db.execute('''SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
        WHERE s.id=? AND s.audience=? AND s.expires_at>? AND u.active=1''',
        (actor['session_id'], actor['audience'], int(time.time()))).fetchone()
    if not row or row['id'] != actor['id']:
        raise Unauthorized('Your sandbox session ended. Sign in again.')
    if actor['permission'] not in ROLES[row['role']]:
        raise Denied('Your sandbox role does not allow this action.')


@contextmanager
def acting(identity, permission='read'):
    token = current.set({**identity, 'permission': permission})
    try:
        yield
    finally:
        current.reset(token)


def logout(store):
    with store.connection(write=True) as db:
        actor = current.get()
        db.execute('DELETE FROM sessions WHERE id=?', (actor['session_id'],))
        store.event(db, None, 'local_sign_out', {})


def change_password(store, data):
    new_hash = password_hash(data.get('new_password'))
    with store.connection(write=True) as db:
        actor = current.get()
        row = db.execute('SELECT password_hash FROM users WHERE id=?', (actor['id'],)).fetchone()
        if not matches(data.get('password'), row[0]):
            raise Denied('Current password is incorrect.')
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (new_hash, actor['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?', (actor['id'],))
        store.event(db, None, 'local_password_changed', {})
    return {'signed_out': True}
