"""Durable human action intents. SQLite records authority before any remote POST.

An ambiguous transport result is never retried by this module. A matching retry
returns the existing intent; only a stored remote message ID can be reconciled
with a read. An operator must inspect unidentifiable outcomes in Gorgias.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from datetime import datetime, timezone
from pathlib import Path

from .db import Database

_SCHEMA = '''CREATE TABLE IF NOT EXISTS console_action_intents (
 operation_id TEXT PRIMARY KEY, semantic_hash TEXT NOT NULL UNIQUE,
 actor_id TEXT NOT NULL, kind TEXT NOT NULL, ticket_id INTEGER NOT NULL,
 source_message_id TEXT NOT NULL, recipient TEXT NOT NULL, text_hash TEXT NOT NULL,
 approved_text TEXT NOT NULL, customer_message TEXT NOT NULL, ai_draft TEXT NOT NULL,
 draft_hash TEXT NOT NULL, approve_learning INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL, remote_message_id INTEGER, response_json TEXT NOT NULL,
 response_status INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 learning_recorded INTEGER NOT NULL DEFAULT 0
)'''


_SOURCE_CONTEXT_SQL = """SELECT pm.customer_email, pm.message_text, tr.draft_text,
    pm.channel, pm.created_at, pm.received_at
    FROM parsed_messages pm LEFT JOIN ticket_results tr
    ON tr.ticket_id=pm.ticket_id AND tr.message_id=pm.message_id
    WHERE pm.ticket_id=? AND pm.message_id=? AND pm.is_customer_message=1"""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_operation(value) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value.lower() and len(value) == 36
    except (ValueError, AttributeError):
        return False


class ActionConflict(Exception):
    def __init__(self, error: str, status=409, operation_id=None):
        self.error, self.status, self.operation_id = error, status, operation_id
        super().__init__(error)


def _checked_context(source, *, draft_revision: str | None = None,
                     expected_recipient: str | None = None) -> tuple[str, str, str]:
    """One definition of stale-draft / changed-recipient (3.5).

    Shared by review_context and reserve: both fetch the same source row and
    must reject a changed draft or recipient identically.
    """
    draft = source["draft_text"] or ""
    revision = _hash(draft)
    recipient = (source["customer_email"] or "").strip().lower()
    if draft_revision is not None and draft_revision != revision:
        raise ActionConflict("draft_changed_refresh_ticket")
    if expected_recipient is not None and expected_recipient.strip().lower() != recipient:
        raise ActionConflict("recipient_changed_refresh_ticket")
    return draft, revision, recipient


def _context_id(ticket_id, source_message_id, source, revision, recipient):
    identity = [ticket_id, source_message_id, _hash(source['message_text'] or ''),
                revision, recipient, source['channel'] or '']
    return _hash(json.dumps(identity, separators=(',', ':')))


class IntentStore:
    def __init__(self, path: Path | str):
        self.db = Database(path)

    async def review_context(self, *, ticket_id: int, source_message_id: str, actor_id: str,
                             expected_revision: str | None = None, expected_recipient: str | None = None) -> dict:
        """Read-only preparation; never creates or reserves an action.

        Reads go through Database.fetch for busy_timeout+retry parity with
        every other DB access. Cross-read snapshot consistency is advisory
        here — reserve() re-validates in one transaction before any intent
        exists, and every interleaving fails closed (a message arriving
        mid-read makes latest != source -> conflict)."""
        source_rows = await self.db.fetch(_SOURCE_CONTEXT_SQL, (ticket_id, source_message_id),
                                           operation="review_context")
        source = source_rows[0] if source_rows else None
        if not source: raise ActionConflict('source_message_not_in_console', 404)
        latest_rows = await self.db.fetch(
            "SELECT message_id FROM parsed_messages WHERE ticket_id=? AND is_customer_message=1 "
            "ORDER BY COALESCE(NULLIF(created_at,''),received_at) DESC,received_at DESC,message_id DESC LIMIT 1",
            (ticket_id,), operation="review_context")
        if not latest_rows or latest_rows[0]['message_id'] != source_message_id:
            raise ActionConflict('new_customer_message_refresh_ticket')
        draft, revision, recipient = _checked_context(
            source, draft_revision=expected_revision, expected_recipient=expected_recipient)
        channel = source['channel'] or ''
        pending: list[dict] = []
        has_intents = await self.db.fetch(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='console_action_intents'",
            operation="review_context")
        if has_intents:
            pending = [dict(row) for row in await self.db.fetch(
                "SELECT operation_id,actor_id,kind,state,source_message_id,draft_hash "
                "FROM console_action_intents WHERE ticket_id=? AND state IN ('pending','uncertain') "
                "ORDER BY created_at,operation_id LIMIT 100", (ticket_id,), operation="review_context")]
        unresolved = [{'kind': row['kind'], 'state': row['state'], 'ownedByCurrentActor': row['actor_id'] == actor_id,
                       **({'operationId': row['operation_id'], 'sourceMessageId': row['source_message_id'],
                           'draftRevision': row['draft_hash']} if row['actor_id'] == actor_id else {})} for row in pending]
        source_text = source["message_text"] or ""
        source_revision = _hash(source_text)
        return {'inboxTicketId': f'gorgias:{ticket_id}', 'ticketId': str(ticket_id), 'sourceMessageId': source_message_id,
                'sourceMessageAt': source['created_at'] or source['received_at'], 'sourceRevision': source_revision,
                'sourceMessageText': source_text[:20000], 'sourceMessageTruncated': len(source_text) > 20000, 'draftRevision': revision,
                'recipient': recipient, 'channel': channel, 'draftText': draft,
                'contextId': _context_id(ticket_id, source_message_id, source, revision, recipient), 'unresolvedActions': unresolved,
                'reviewable': bool(draft and recipient and channel and not unresolved and len(source_text) <= 20000),
                'providerIdentityVerified': False, 'sendEnabled': False, 'sendAndCloseEnabled': False,
                'message': 'Activate the send access.'}

    async def reserve(self, *, operation_id: str, actor_id: str, kind: str,
                      ticket_id: int, source_message_id: str, text: str, draft_revision: str,
                      approve_learning: bool = False, expected_recipient: str | None = None,
                      expected_context_id: str | None = None) -> tuple[dict, bool]:
        if not valid_operation(operation_id):
            raise ActionConflict('valid_operation_id_required', 400)
        if not source_message_id or len(source_message_id) > 200:
            raise ActionConflict('source_message_id_required', 400)
        if not actor_id or kind not in {'send', 'note'}:
            raise ActionConflict('not_authenticated', 401)
        if not isinstance(draft_revision, str) or not re.fullmatch('[0-9a-f]{64}', draft_revision):
            raise ActionConflict('draft_revision_required', 400)
        text_hash = _hash(text)

        async def transaction(conn):
            await conn.execute(_SCHEMA)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_console_action_pending ON console_action_intents(ticket_id,kind,state)")
            cursor = await conn.execute('SELECT * FROM console_action_intents WHERE operation_id=?', (operation_id,))
            existing = await cursor.fetchone()
            await cursor.close()
            if existing:
                row = dict(existing)
                for name, value in {'actor_id': actor_id, 'kind': kind, 'ticket_id': ticket_id,
                                    'source_message_id': source_message_id, 'text_hash': text_hash, 'draft_hash': draft_revision,
                                    'approve_learning': int(approve_learning)}.items():
                    if row[name] != value:
                        raise ActionConflict('operation_id_conflict')
                return row, False
            cursor = await conn.execute(_SOURCE_CONTEXT_SQL,(ticket_id, source_message_id))
            context = await cursor.fetchone()
            await cursor.close()
            if not context:
                raise ActionConflict('source_message_not_in_console', 404)
            ai_draft, _revision, recipient = _checked_context(context, draft_revision=draft_revision, expected_recipient=expected_recipient)
            if expected_context_id is not None and expected_context_id != _context_id(ticket_id, source_message_id, context, _revision, recipient):
                raise ActionConflict('review_changed_refresh_ticket')
            if kind == 'send' and not recipient:
                raise ActionConflict('recipient_unavailable', 409)
            semantic = _hash(json.dumps([kind, ticket_id, source_message_id, recipient,
                                        _hash(ai_draft), text_hash], separators=(',', ':')))
            cursor = await conn.execute('SELECT * FROM console_action_intents WHERE semantic_hash=?', (semantic,))
            previous = await cursor.fetchone()
            await cursor.close()
            if previous:
                if previous['actor_id'] != actor_id:
                    raise ActionConflict('action_owned_by_another_actor', 403)
                if previous['approve_learning'] != int(approve_learning):
                    raise ActionConflict('learning_approval_is_fixed_for_existing_action', operation_id=previous['operation_id'])
                return dict(previous), False  # New browser tab/key, same reviewed message.
            cursor = await conn.execute('''SELECT operation_id FROM console_action_intents
                WHERE ticket_id=? AND kind=? AND state IN ('uncertain','pending') LIMIT 1''', (ticket_id, kind))
            unresolved = await cursor.fetchone()
            await cursor.close()
            if unresolved:
                raise ActionConflict('previous_delivery_unresolved', operation_id=unresolved['operation_id'])
            now = _now()
            response = {'ok': False, 'error': 'delivery_unconfirmed', 'delivery_status': 'unknown',
                        'operation_id': operation_id,
                        'message': 'Check this action status before sending again. No automatic resend.'}
            await conn.execute('''INSERT INTO console_action_intents
                (operation_id, semantic_hash, actor_id, kind, ticket_id, source_message_id,
                 recipient, text_hash, approved_text, customer_message, ai_draft, draft_hash,
                 approve_learning, state, response_json, response_status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'uncertain',?,202,?,?)''',
                (operation_id, semantic, actor_id, kind, ticket_id, source_message_id, recipient,
                 text_hash, text, context['message_text'] or '', ai_draft, _hash(ai_draft),
                 int(approve_learning), json.dumps(response), now, now))
            cursor = await conn.execute('SELECT * FROM console_action_intents WHERE operation_id=?', (operation_id,))
            row = dict(await cursor.fetchone())
            await cursor.close()
            return row, True
        return await self.db.transaction(transaction, operation='reserve_console_action')

    async def get(self, operation_id: str) -> dict | None:
        await self.db.execute(_SCHEMA, operation='init_console_actions')
        rows = await self.db.fetch('SELECT * FROM console_action_intents WHERE operation_id=?', (operation_id,))
        return dict(rows[0]) if rows else None

    async def attach_message(self, operation_id: str, message_id: int):
        if type(message_id) is not int or message_id <= 0:
            raise ValueError('invalid remote message id')
        response = {'ok': True, 'delivery_status': 'pending', 'operation_id': operation_id,
                    'message_id': message_id}
        await self.db.execute('''UPDATE console_action_intents SET remote_message_id=?, state='pending',
            response_json=?, response_status=202, updated_at=? WHERE operation_id=? AND state='uncertain' ''',
            (message_id, json.dumps(response), _now(), operation_id), operation='record_remote_message')

    async def finish(self, operation_id: str, state: str, response: dict, status: int):
        if state not in {'sent', 'recorded', 'pending', 'uncertain', 'failed'}:
            raise ValueError('invalid action state')
        await self.db.execute('''UPDATE console_action_intents SET state=?, response_json=?,
            response_status=?, updated_at=? WHERE operation_id=? AND state NOT IN ('sent','recorded')''',
            (state, json.dumps(response), status, _now(), operation_id), operation='finish_console_action')
        return await self.get(operation_id)

    async def mark_learning_recorded(self, operation_id: str):
        await self.db.execute('UPDATE console_action_intents SET learning_recorded=1 WHERE operation_id=?',
                              (operation_id,), operation='learning_recorded')
