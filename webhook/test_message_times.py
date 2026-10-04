import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bb_webhook.webhook_handler import parse_event

from bb_webhook import database, draft_generation
from bb_webhook.db import Database
from bb_webhook.message_times import LATEST_CUSTOMER_SQL, normalize_timestamp, utc_microseconds
from bb_webhook.send_intents import ActionConflict, IntentStore


class TimestampTests(unittest.TestCase):
    def test_aware_utc_normalization_and_integer_precision(self):
        for value, normalized, micros in (
            ('1970-01-01T02:00:00.000100+02:00', '1970-01-01T00:00:00.000100+00:00', 100),
            ('1969-12-31T19:00:00.000200-05:00', '1970-01-01T00:00:00.000200+00:00', 200),
            ('1970-01-01T00:00:00Z', '1970-01-01T00:00:00+00:00', 0),
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_timestamp(value), normalized)
                self.assertEqual(utc_microseconds(value), micros)
                self.assertIs(type(utc_microseconds(value)), int)
        for invalid in (None, '', 'now', '1970-01-01T00:00:00', 'invalid', 100):
            self.assertIsNone(normalize_timestamp(invalid))
            self.assertIsNone(utc_microseconds(invalid))

    def test_webhook_intake_uses_shared_utc_normalizer_and_rejects_bad_time(self):
        for value, expected in (('2026-09-26T12:00:00.000200+02:00', '2026-09-26T10:00:00.000200+00:00'),
                                (None, None), ('invalid', None), ('2026-09-26T10:00:00', None)):
            body=json.dumps({'trigger':'ticket-message-created', 'ticket':{'id':1},
                'message':{'id':2, 'from_agent':False, 'created_datetime':value, 'body_text':'A question'}}).encode()
            with patch('bb_webhook.webhook_handler.get_settings', return_value=SimpleNamespace(gorgias_subdomain='test')):
                parsed=parse_event(body)
            self.assertEqual(parsed['created_at'] if parsed else None, expected)


class MessageChronologyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'chronology.db'
        await database.init_db(self.path)
        self.db=Database(self.path)
        self.store=IntentStore(self.path)

    async def ingest(self, mid, at, received='2026-09-26T12:00:00+00:00'):
        job=await database.ingest_event(dict(tenant_id='test', ticket_id=1, message_id=mid,
            event_type='message', author_type='customer', is_customer_message=True,
            message_text='Synthetic question', customer_email='customer@example.invalid',
            channel='email', created_at=at), '{}', self.path)
        await self.db.execute('UPDATE parsed_messages SET received_at=? WHERE message_id=?', (received, mid))
        return job

    async def latest(self):
        return dict((await self.db.fetch(LATEST_CUSTOMER_SQL, (1,)))[0])

    async def publish(self, job, mid, attempt=None, state='ready'):
        if attempt is None:
            self.assertTrue(await database.claim_job(job, self.path))
            attempt=await draft_generation.begin_attempt(job, self.path)
        return await draft_generation.finish_attempt(dict(job_id=job, ticket_id=1,
            message_id=mid, generation_attempt_id=attempt, generation_state=state,
            priority='normal', action='drafted', draft_text='Grounded reply' if state=='ready' else '',
            generation_error='authentication' if state=='failed' else None), self.path)

    async def test_ordering_uses_instants_then_precise_receipts_and_message_ids(self):
        cases=(
            ('2026-09-26T10:00:00+02:00', '2026-09-26T09:30:00Z',
             '2026-09-26T12:00:00Z', '2026-09-26T11:00:00Z'),
            ('2026-09-26T10:00:00.000100Z', '2026-09-26T10:00:00.000200Z',
             '2026-09-26T12:00:00Z', '2026-09-26T11:00:00Z'),
            ('2026-09-26T12:00:00+02:00', '2026-09-26T05:00:00-05:00',
             '2026-09-26T12:00:00.000100Z', '2026-09-26T12:00:00.000200Z'),
            (None, '', '2026-09-26T12:00:00.000100Z', '2026-09-26T12:00:00.000200Z'),
            ('2026-09-26T12:00:00+02:00', '2026-09-26T10:00:00Z',
             '2026-09-26T13:00:00+02:00', '2026-09-26T11:00:00Z'),
        )
        for first, second, r1, r2 in cases:
            with self.subTest(first=first, second=second):
                await self.db.execute('DELETE FROM parsed_messages')
                await self.db.execute('DELETE FROM webhook_events')
                await self.ingest('a', first, r1)
                await self.ingest('b', second, r2)
                self.assertEqual(await self.latest(), {'message_id':'b', 'chronology_invalid':0})
                context=await self.store.review_context(ticket_id=1, source_message_id='b', actor_id='operator')
                self.assertEqual(context['sourceMessageId'], 'b')
                with self.assertRaisesRegex(ActionConflict, 'new_customer_message'):
                    await self.store.review_context(ticket_id=1, source_message_id='a', actor_id='operator')
                async def transaction(conn):
                    cursor=await conn.execute(LATEST_CUSTOMER_SQL, (1,))
                    return dict(await cursor.fetchone())
                self.assertEqual(await self.db.transaction(transaction), await self.latest())

    async def test_microsecond_new_message_blocks_begin_and_publication(self):
        job=await self.ingest('a', '2026-09-26T10:00:00.000100Z')
        self.assertTrue(await database.claim_job(job, self.path))
        attempt=await draft_generation.begin_attempt(job, self.path)
        await self.ingest('b', '2026-09-26T10:00:00.000200Z', '2026-09-26T11:00:00Z')
        self.assertEqual(await self.publish(job, 'a', attempt), 'superseded')
        self.assertIsNone(await database.get_job_result(job, self.path))
        await self.db.execute("UPDATE job_queue SET status='processing' WHERE id=?", (job,))
        self.assertIsNone(await draft_generation.begin_attempt(job, self.path))

    async def test_earlier_offset_message_does_not_supersede_latest_draft(self):
        job=await self.ingest('a', '2026-09-26T09:30:00Z')
        self.assertTrue(await database.claim_job(job, self.path))
        attempt=await draft_generation.begin_attempt(job, self.path)
        await self.ingest('b', '2026-09-26T10:00:00+02:00')
        self.assertEqual(await self.publish(job, 'a', attempt), 'ready')
        self.assertEqual((await database.get_job_result(job, self.path))['draft_text'], 'Grounded reply')

    async def test_manual_retry_rejects_precisely_newer_source(self):
        job=await self.ingest('a', '2026-09-26T10:00:00.000100Z')
        await self.publish(job, 'a', state='failed')
        await database.complete_job(job, db_path=self.path, require_result=True)
        await self.ingest('b', '2026-09-26T10:00:00.000200Z', '2026-09-26T11:00:00Z')
        with self.assertRaisesRegex(ActionConflict, 'new_customer_message'):
            await draft_generation.retry_draft(1, dict(operation_id=str(uuid.uuid4()),
                source_message_id='a', draft_revision=draft_generation.revision('')), 'operator', self.path)

    async def test_invalid_legacy_source_or_receipt_refuses_selection(self):
        await self.ingest('a', '2026-09-26T10:00:00Z')
        for source, receipt in (('invalid', '2026-09-26T12:00:00Z'),
                                ('2026-09-26T10:00:00', '2026-09-26T12:00:00Z'),
                                ('2026-09-26T09:00:00Z', 'now')):
            with self.subTest(source=source, receipt=receipt):
                await self.db.execute("DELETE FROM parsed_messages WHERE message_id='b'")
                await self.db.execute("DELETE FROM webhook_events WHERE message_id='b'")
                await self.ingest('b', source, receipt)
                self.assertEqual((await self.latest())['chronology_invalid'], 1)
                with self.assertRaisesRegex(ActionConflict, 'message_chronology_unavailable'):
                    await self.store.review_context(ticket_id=1, source_message_id='a', actor_id='operator')
                async def transaction(conn):
                    return await draft_generation.conflict(conn, 1, 'a')
                self.assertEqual(await self.db.transaction(transaction), 'message_chronology_unavailable')
