"""Short-lived, revocable grants for explicit human sends from one Inbox page.

Tokens are kept only in the page's memory; SQLite stores their hashes. Reloading
starts read-only. Grants do not authorize the read-only Inbox service or Hermes.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time

from .db import Database

TTL_SECONDS = 30 * 60
_SCHEMA = '''CREATE TABLE IF NOT EXISTS inbox_send_grants (
 token_hash TEXT PRIMARY KEY, actor_id TEXT NOT NULL, session_id TEXT NOT NULL,
 expires_at INTEGER NOT NULL
)'''


def _digest(token):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        return None
    return hashlib.sha256(token.encode()).hexdigest()


class InboxSendAccess:
    def __init__(self, path):
        self.db = Database(path)

    async def enable(self, actor_id, session_id):
        if not actor_id or not session_id:
            raise ValueError('authenticated_session_required')
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + TTL_SECONDS
        async def transaction(conn):
            await conn.execute(_SCHEMA)
            await conn.execute('DELETE FROM inbox_send_grants WHERE expires_at<=?', (int(time.time()),))
            await conn.execute('INSERT INTO inbox_send_grants VALUES (?,?,?,?)',
                               (_digest(token), actor_id, session_id, expires_at))
        await self.db.transaction(transaction, operation='enable_inbox_send')
        return {'token': token, 'expiresAt': expires_at}

    async def disable(self, token, actor_id, session_id):
        digest = _digest(token)
        if not digest:
            return
        await self.db.execute('DELETE FROM inbox_send_grants WHERE token_hash=? AND actor_id=? AND session_id=?',
                              (digest, actor_id, session_id), operation='disable_inbox_send')

    async def allowed(self, token, actor_id, session_id):
        digest = _digest(token)
        if not digest or not actor_id or not session_id:
            return False
        # Missing table is the normal default before the first explicit enable.
        tables = await self.db.fetch("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbox_send_grants'")
        if not tables:
            return False
        rows = await self.db.fetch('SELECT 1 FROM inbox_send_grants WHERE token_hash=? AND actor_id=? AND session_id=? AND expires_at>?',
                                   (digest, actor_id, session_id, int(time.time())))
        return bool(rows)
