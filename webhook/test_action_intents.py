"""Interruption, concurrency and authenticated retry safety for human actions."""
import asyncio
import json
import hashlib
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

from bb_webhook import database
from bb_webhook.routers import console as console_router
from bb_webhook.db import Database
from bb_webhook.send_intents import IntentStore, ActionConflict
from webhook.action_test_support import setup_action_case


class ActionIntentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await setup_action_case(self)
        self.store = IntentStore(self.path)
        self.operation = str(uuid.uuid4())
        self.payload = {'operation_id': self.operation, 'source_message_id': 'source-1',
                        'text': 'I can help check that.', 'confirmed': True,
                        'draft_revision': hashlib.sha256(b'I can help check that.').hexdigest()}

    async def reserve(self, **changes):
        args = dict(operation_id=self.operation, actor_id='owner:owner', kind='send',
                    ticket_id=1, source_message_id='source-1', text='I can help check that.',
                    draft_revision=self.payload['draft_revision'])
        return await self.store.reserve(**(args | changes))

    async def newer_customer(self, at='2026-09-26T01:00:00.000200Z'):
        await database.ingest_event(dict(tenant_id='test', ticket_id=1, message_id='new-source',
            event_type='message', author_type='customer', is_customer_message=True,
            message_text='A new customer question', created_at=at), '{}', self.path)

    async def test_stale_fresh_reply_is_not_attempted_but_note_policy_is_preserved(self):
        await self.newer_customer()
        with patch.object(console_router, '_GClient') as transport:
            response=await self.client.post('/dashboard/api/ticket/1/send', json=self.payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'new_customer_message_refresh_ticket')
        self.assertEqual(response.json()['delivery_status'], 'not_attempted')
        self.assertEqual(response.json()['operation_id'], self.operation)
        self.assertIsNone(await self.store.get(self.operation))
        transport.assert_not_called()
        _, fresh=await self.reserve(kind='note')
        self.assertTrue(fresh)

    async def test_malformed_legacy_chronology_blocks_fresh_reply_without_transport(self):
        await self.newer_customer(at='invalid')
        with patch.object(console_router, '_GClient') as transport:
            response=await self.client.post('/dashboard/api/ticket/1/send', json=self.payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'message_chronology_unavailable')
        self.assertEqual(response.json()['delivery_status'], 'not_attempted')
        self.assertEqual(response.json()['operation_id'], self.operation)
        self.assertIsNone(await self.store.get(self.operation))
        transport.assert_not_called()
        self.assertTrue((await self.reserve(kind='note'))[1])

    async def test_both_replay_paths_survive_newer_source_and_restart(self):
        await self.reserve()
        await self.newer_customer()
        self.store=IntentStore(self.path)
        for state in ('uncertain', 'pending', 'sent'):
            await self.store.finish(self.operation, state, {'ok':state=='sent', 'delivery_status':state}, 200 if state=='sent' else 202)
            for operation in (self.operation, str(uuid.uuid4())):
                row, fresh=await self.reserve(operation_id=operation)
                self.assertFalse(fresh)
                self.assertEqual((row['operation_id'], row['state']), (self.operation, state))
        with self.assertRaisesRegex(ActionConflict, 'new_customer_message_refresh_ticket'):
            await self.reserve(operation_id=str(uuid.uuid4()), text='A distinct reply')
        await Database(self.path).execute("UPDATE parsed_messages SET created_at='invalid' WHERE message_id='new-source'")
        self.assertFalse((await self.reserve(operation_id=str(uuid.uuid4())))[1])
        rows=await Database(self.path).fetch('SELECT * FROM console_action_intents')
        self.assertEqual(len(rows), 1)

    async def test_two_concurrent_keys_reserve_only_one_semantic_action(self):
        results = await asyncio.gather(self.reserve(), self.reserve(operation_id=str(uuid.uuid4())))
        self.assertEqual(sum(fresh for _, fresh in results), 1)
        self.assertEqual(results[0][0]['operation_id'], results[1][0]['operation_id'])
        self.assertEqual(results[0][0]['actor_id'], 'owner:owner')

    async def test_same_key_cannot_change_actor_text_source_or_approval(self):
        await self.reserve()
        for changes in ({'actor_id': 'owner:other'}, {'text': 'Different'}, {'source_message_id': 'other'},
                        {'approve_learning': True}):
            with self.assertRaisesRegex(ActionConflict, 'operation_id_conflict'):
                await self.reserve(**changes)

    async def test_another_actor_cannot_adopt_semantic_action_or_read_status(self):
        await self.reserve(actor_id='owner:other')
        with self.assertRaisesRegex(ActionConflict, 'action_owned_by_another_actor'):
            await self.reserve(operation_id=str(uuid.uuid4()))
        response=await self.client.get(f'/dashboard/api/ticket/1/actions/{self.operation}')
        self.assertEqual(response.status_code,404)

    async def test_interrupted_intent_survives_new_store_without_resend_permission(self):
        await self.reserve()
        fresh_store = IntentStore(self.path)
        row, fresh = await fresh_store.reserve(operation_id=self.operation, actor_id='owner:owner',
            kind='send', ticket_id=1, source_message_id='source-1', text=self.payload['text'], draft_revision=self.payload['draft_revision'])
        self.assertFalse(fresh)
        self.assertEqual(row['state'], 'uncertain')
        with self.assertRaisesRegex(ActionConflict, 'previous_delivery_unresolved'):
            await self.reserve(operation_id=str(uuid.uuid4()), text='A different followup')

    async def test_completed_action_allows_distinct_human_followup(self):
        await self.reserve()
        await self.store.finish(self.operation, 'sent', {'ok': True}, 200)
        _, fresh = await self.reserve(operation_id=str(uuid.uuid4()), text='Here is the next update.')
        self.assertTrue(fresh)

    async def test_remote_timeout_retry_never_posts_again_and_cannot_promote(self):
        fake = Mock()
        calls = []
        async def send(*args, **kwargs):
            calls.append(args)
            raise TimeoutError('ambiguous transport failure')
        fake.send_public_reply = send
        recorder = Mock(return_value=True)
        with patch.object(console_router, '_GClient', return_value=fake), patch.object(console_router, '_record_lesson', recorder):
            first = await self.client.post('/dashboard/api/ticket/1/send', json=self.payload)
            retry = await self.client.post('/dashboard/api/ticket/1/send', json=self.payload)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(retry.json(), first.json())
        self.assertEqual(len(calls), 1)
        recorder.assert_not_called()

    async def test_pending_id_is_durable_and_status_uses_only_get_reconciliation(self):
        fake = Mock()
        posts, gets = [], []
        async def send(*args, **kwargs):
            posts.append(args)
            await kwargs['on_created'](99)
            return {'ok': True, 'message_id': 99, 'delivery_status': 'pending'}
        async def read(*args):
            gets.append(args)
            return {'status': 'sent'}
        fake.send_public_reply, fake._wait_for_delivery = send, read
        recorder = Mock(return_value=True)
        with patch.object(console_router, '_GClient', return_value=fake), patch.object(console_router, '_record_lesson', recorder):
            response = await self.client.post('/dashboard/api/ticket/1/send', json=self.payload | {'approve_learning': True,
                'message_text': 'forged browser facts', 'ai_draft': 'forged browser draft'})
            self.assertEqual(response.status_code, 202)
            recorder.assert_not_called()
            status = await self.client.get(f'/dashboard/api/ticket/1/actions/{self.operation}')
            retry = await self.client.post('/dashboard/api/ticket/1/send', json=self.payload | {'approve_learning': True})
        self.assertEqual(status.status_code, 200)
        self.assertEqual(retry.json(), status.json())
        self.assertEqual(len(posts), 1)
        self.assertEqual(gets, [(1, 99)])
        self.assertEqual(recorder.call_count, 1)
        self.assertEqual(recorder.call_args.args[2], 'Where is my parcel?')
        self.assertEqual(recorder.call_args.args[3], 'I can help check that.')
        self.assertEqual(recorder.call_args.kwargs['review_actor'], 'owner:owner')
        self.assertEqual((await self.store.get(self.operation))['learning_recorded'], 1)

    async def test_internal_note_requires_confirmation_and_never_promotes(self):
        fake = Mock()
        async def note(*args, **kwargs):
            await kwargs['on_created'](101)
            return {'ok': True, 'message': {'id': 101}}
        fake.post_internal_note = note
        recorder = Mock()
        with patch.object(console_router, '_GClient', return_value=fake), patch.object(console_router, '_record_lesson', recorder):
            refused = await self.client.post('/dashboard/api/ticket/1/note', json=self.payload | {'confirmed': False})
            accepted = await self.client.post('/dashboard/api/ticket/1/note', json=self.payload | {'approve_learning': True})
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(accepted.json()['delivery_status'], 'recorded')
        recorder.assert_not_called()

    async def test_same_text_can_be_used_again_only_for_new_source_or_revision(self):
        await self.reserve()
        await self.store.finish(self.operation, 'sent', {'ok': True}, 200)
        await Database(self.path).execute("UPDATE ticket_results SET draft_text='new reviewed revision' WHERE message_id='source-1'")
        _, fresh = await self.reserve(operation_id=str(uuid.uuid4()), draft_revision=hashlib.sha256(b'new reviewed revision').hexdigest())
        self.assertTrue(fresh)

    async def test_changed_server_draft_requires_fresh_owner_review(self):
        await Database(self.path).execute("UPDATE ticket_results SET draft_text='changed after review' WHERE message_id='source-1'")
        with patch.object(console_router, '_GClient') as transport:
            response = await self.client.post('/dashboard/api/ticket/1/send', json=self.payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], 'draft_changed_refresh_ticket')
        self.assertEqual(response.json()['operation_id'], self.operation)
        self.assertEqual(response.json()['delivery_status'], 'not_attempted')
        transport.assert_not_called()

    async def test_learning_failure_does_not_hide_send_and_is_recoverable_without_resend(self):
        fake = Mock()
        posts = []
        async def send(*args, **kwargs):
            posts.append(args)
            await kwargs['on_created'](99)
            return {'ok': True, 'message_id': 99, 'delivery_status': 'sent'}
        fake.send_public_reply = send
        recorder = Mock(side_effect=[False, True])
        with patch.object(console_router, '_GClient', return_value=fake), patch.object(console_router, '_record_lesson', recorder):
            result = await self.client.post('/dashboard/api/ticket/1/send', json=self.payload | {'approve_learning': True})
            retry = await self.client.post('/dashboard/api/ticket/1/send', json=self.payload | {'approve_learning': True})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(retry.json(), result.json())
        self.assertEqual(len(posts), 1)
        self.assertEqual(recorder.call_count, 2)

    async def test_no_operation_or_confirmation_cannot_construct_transport(self):
        for body in (self.payload | {'operation_id': ''}, self.payload | {'confirmed': False}):
            with patch.object(console_router, '_GClient') as client:
                response = await self.client.post('/dashboard/api/ticket/1/send', json=body)
            self.assertIn(response.status_code, (400, 409))
            client.assert_not_called()

    async def test_preflight_refusals_allow_recovery_only_without_prior_intent(self):
        with patch.object(console_router, '_GClient') as transport:
            response=await self.client.post('/dashboard/api/ticket/1/send',json=self.payload|{'draft_revision':'0'*64})
            self.assertEqual(response.json()['delivery_status'],'not_attempted')
            self.assertEqual(response.json()['operation_id'],self.operation)
            self.assertIsNone(await self.store.get(self.operation))
            await self.reserve()
            for change in ({'confirmed':False},{'text':''},{'draft_revision':'0'*64}):
                response=await self.client.post('/dashboard/api/ticket/1/send',json=self.payload|change)
                self.assertNotEqual(response.json().get('delivery_status'),'not_attempted')
                self.assertNotIn('operation_id',response.json())
            transport.assert_not_called()

    async def test_preflight_lookup_failure_cannot_identify_a_definite_refusal(self):
        with patch.object(IntentStore,'get',AsyncMock(side_effect=RuntimeError('synthetic lookup failure'))), \
             patch.object(console_router,'_GClient') as transport:
            response=await self.client.post('/dashboard/api/ticket/1/send',json=self.payload|{'confirmed':False})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json(),{'ok':False,'error':'confirmation_required'})
        transport.assert_not_called()

    async def test_invalid_operation_is_never_echoed_as_a_known_refusal(self):
        with patch.object(console_router,'_GClient') as transport:
            response=await self.client.post('/dashboard/api/ticket/1/send',json=self.payload|{'operation_id':'invalid'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.json()['error'],'valid_operation_id_required')
        self.assertNotIn('operation_id',response.json())
        transport.assert_not_called()

    async def test_unauthenticated_or_wrong_origin_cannot_send(self):
        self.client.cookies.clear()
        with patch.object(console_router, '_GClient') as client:
            response = await self.client.post('/dashboard/api/ticket/1/send', json=self.payload)
        self.assertEqual(response.status_code, 401)
        client.assert_not_called()

if __name__ == '__main__':
    unittest.main()
