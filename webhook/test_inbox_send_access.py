"""Inbox opt-in send authorization, using synthetic sessions and a fake provider."""
import hashlib
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

from bb_webhook import app as app_module, session_store
from bb_webhook.console_auth import build_session_token, session_claims
from bb_webhook.db import Database
from bb_webhook.inbox_send_access import InboxSendAccess
from bb_webhook.send_intents import IntentStore, ActionConflict
from webhook.action_test_support import setup_action_case


class InboxSendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await setup_action_case(self)
        await Database(self.path).execute("UPDATE parsed_messages SET channel='email'")
        self.access_url='/dashboard/api/inbox/send-access'
        self.send_url='/dashboard/api/inbox/ticket/1/send'
        self.context=(await self.client.get('/dashboard/api/inbox/review-context/gorgias:1',params={'source_message_id':'source-1'})).json()['context']
        self.payload={'text':'Thanks for your message.', 'confirmed':True, 'operation_id':str(uuid.uuid4()),
                      'source_message_id':'source-1','draft_revision':self.context['draftRevision'],
                      'expected_recipient':self.context['recipient'],'context_id':self.context['contextId'],
                      'approve_learning':False}
        self.provider=Mock()
        async def send(ticket_id,text,**kwargs):
            await kwargs['on_created'](9001)
            return {'ok':True,'delivery_status':'sent','message_id':9001}
        self.provider.send_public_reply=AsyncMock(side_effect=send)
        factory=patch.object(app_module,'_GClient',return_value=self.provider)
        self.factory=factory.start();self.addCleanup(factory.stop)

    async def enable(self):
        response=await self.client.post(self.access_url,json={'enabled':True})
        self.assertEqual(response.status_code,200,response.text)
        self.token=response.json()['token']
        return {'X-Inbox-Send-Access':self.token}

    async def test_default_and_invalid_grants_cannot_send_even_with_confirmation(self):
        for token in ('','forged','x'*43):
            response=await self.client.post(self.send_url,json=self.payload,headers={'X-Inbox-Send-Access':token})
            self.assertEqual(response.status_code,403)
            self.assertEqual(response.json()['delivery_status'],'not_attempted')
        self.factory.assert_not_called()

    async def test_toggle_never_calls_provider_and_off_revokes(self):
        headers=await self.enable()
        self.assertEqual((await self.client.post(self.access_url,json={'enabled':False},headers=headers)).status_code,200)
        response=await self.client.post(self.send_url,json=self.payload,headers=headers)
        self.assertEqual(response.status_code,403)
        self.factory.assert_not_called()
        self.assertEqual(await Database(self.path).fetch('SELECT * FROM inbox_send_grants'),[])

    async def test_grant_requires_same_session_and_expiry(self):
        headers=await self.enable()
        original=dict(self.client.cookies)
        token=build_session_token('owner','test-action-secret')
        await session_store.register(session_claims(token,'test-action-secret'),self.path)
        self.client.cookies.clear();self.client.cookies.set('bb_console_session',token)
        self.assertEqual((await self.client.post(self.send_url,json=self.payload,headers=headers)).status_code,403)
        self.client.cookies.clear();self.client.cookies.update(original)
        await Database(self.path).execute('UPDATE inbox_send_grants SET expires_at=0')
        self.assertEqual((await self.client.post(self.send_url,json=self.payload,headers=headers)).status_code,403)
        self.factory.assert_not_called()

    async def test_auth_origin_and_explicit_boolean_required(self):
        self.assertEqual((await self.client.post(self.access_url,json={'enabled':'true'})).status_code,400)
        for origin in ('https://evil.example','null',''):
            self.assertEqual((await self.client.post(self.access_url,json={'enabled':True},headers={'Origin':origin})).status_code,403)
        self.client.cookies.clear()
        self.assertEqual((await self.client.post(self.access_url,json={'enabled':True})).status_code,401)
        self.factory.assert_not_called()

    async def test_on_still_requires_confirmation_and_unchanged_review(self):
        headers=await self.enable()
        for changes in ({'confirmed':False},{'context_id':'forged'},{'expected_recipient':'other@example.com'},
                        {'source_message_id':'other'},{'draft_revision':'0'*64}):
            response=await self.client.post(self.send_url,json={**self.payload,**changes},headers=headers)
            self.assertIn(response.status_code,(400,404,409),response.text)
            self.assertEqual(response.json()['delivery_status'],'not_attempted')
        self.factory.assert_not_called()

    async def test_confirmed_reply_uses_durable_sender_and_never_duplicates(self):
        headers=await self.enable()
        first=await self.client.post(self.send_url,json=self.payload,headers=headers)
        second=await self.client.post(self.send_url,json=self.payload,headers=headers)
        third=await self.client.post(self.send_url,json={**self.payload,'operation_id':str(uuid.uuid4())},headers=headers)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['delivery_status'],'sent')
        self.assertEqual(first.json(),second.json());self.assertEqual(first.json(),third.json())
        self.provider.send_public_reply.assert_awaited_once()
        kwargs=self.provider.send_public_reply.call_args.kwargs
        self.assertEqual(kwargs['expected_recipient'],'customer@example.com')
        self.assertEqual(kwargs['expected_source_message_id'],'source-1')
        rows=await Database(self.path).fetch('SELECT * FROM console_action_intents')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['actor_id'],'owner:owner')
        self.assertEqual(rows[0]['approve_learning'],0)
        grants=await Database(self.path).fetch('SELECT * FROM inbox_send_grants')
        self.assertEqual(grants[0]['token_hash'],hashlib.sha256(self.token.encode()).hexdigest())

    async def test_manual_reply_does_not_require_an_ai_draft(self):
        await Database(self.path).execute('DELETE FROM ticket_results')
        context=(await self.client.get('/dashboard/api/inbox/review-context/gorgias:1',params={'source_message_id':'source-1'})).json()['context']
        response=await self.client.post(self.send_url,json={**self.payload,'draft_revision':context['draftRevision'],'context_id':context['contextId']},headers=await self.enable())
        self.assertEqual(response.status_code,200,response.text)

    async def test_uncertain_delivery_blocks_another_reply_and_status_does_not_resend(self):
        self.provider.send_public_reply.side_effect=TimeoutError('synthetic timeout')
        headers=await self.enable()
        response=await self.client.post(self.send_url,json=self.payload,headers=headers)
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()['delivery_status'],'unknown')
        again=await self.client.post(self.send_url,json={**self.payload,'text':'Another reply','operation_id':str(uuid.uuid4())},headers=headers)
        self.assertEqual(again.status_code,409)
        await self.client.post(self.access_url,json={'enabled':False},headers=headers)
        status=await self.client.get('/dashboard/api/ticket/1/actions/'+self.payload['operation_id'])
        self.assertEqual(status.json()['delivery_status'],'unknown')
        self.provider.send_public_reply.assert_awaited_once()

    async def test_access_storage_failure_denies_delivery(self):
        headers=await self.enable()
        with patch.object(InboxSendAccess, 'allowed', AsyncMock(side_effect=RuntimeError('synthetic DB failure'))):
            response=await self.client.post(self.send_url,json=self.payload,headers=headers)
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['delivery_status'],'not_attempted')
        self.factory.assert_not_called()

    async def test_recipient_or_source_change_during_reservation_is_rejected(self):
        for sql in ("UPDATE parsed_messages SET customer_email='new@example.com'", "UPDATE parsed_messages SET message_text='Changed question'"):
            await Database(self.path).execute(sql)
            with self.assertRaises(ActionConflict):
                await IntentStore(self.path).reserve(operation_id=str(uuid.uuid4()),actor_id='owner:owner',kind='send',ticket_id=1,
                  source_message_id='source-1',text='Reviewed reply',draft_revision=self.context['draftRevision'],
                  expected_recipient=self.context['recipient'],expected_context_id=self.context['contextId'])
            await Database(self.path).execute("UPDATE parsed_messages SET customer_email='customer@example.com',message_text='Where is my parcel?'")
        self.factory.assert_not_called()
