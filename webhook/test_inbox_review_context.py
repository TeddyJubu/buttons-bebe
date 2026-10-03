"""Dormant inbox review context never reserves or posts an action."""
import hashlib
import unittest
import uuid
from unittest.mock import patch
from bb_webhook.routers import console as console_router
from bb_webhook.db import Database
from bb_webhook.send_intents import IntentStore
from webhook.action_test_support import setup_action_case

class ReviewContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await setup_action_case(self)
        await Database(self.path).execute("UPDATE parsed_messages SET channel='email'")
        self.url='/dashboard/api/inbox/review-context/gorgias:1'
    async def get(self,**params):
        return await self.client.get(self.url,params={'source_message_id':'source-1',**params})
    async def test_context_is_readonly_exact_and_send_locked(self):
        before=self.path.read_bytes()
        with patch.object(console_router,'_GClient') as transport:
            response=await self.get()
        transport.assert_not_called()
        self.assertEqual(response.status_code,200)
        context=response.json()['context']
        self.assertEqual(context['inboxTicketId'],'gorgias:1')
        self.assertEqual(context['ticketId'],'1')
        self.assertEqual(context['draftText'],'I can help check that.')
        self.assertEqual(context['draftRevision'],hashlib.sha256(context['draftText'].encode()).hexdigest())
        self.assertEqual(context['recipient'],'customer@example.com')
        self.assertEqual(context['channel'],'email')
        self.assertEqual(context['message'],'Activate the send access.')
        self.assertFalse(context['sendEnabled']);self.assertFalse(context['sendAndCloseEnabled'])
        self.assertFalse(context['providerIdentityVerified'])
        self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(await Database(self.path).fetch("SELECT name FROM sqlite_master WHERE name='console_action_intents'"),[])
    async def test_wrong_ids_stale_draft_and_recipient_fail_closed(self):
        for value in ('1','gorgias:01','gorgias:-1','gorgias:1:2','other:1'):
            response=await self.client.get('/dashboard/api/inbox/review-context/'+value,params={'source_message_id':'source-1'})
            self.assertEqual(response.status_code,400,value)
        self.assertEqual((await self.get(source_message_id='other')).status_code,404)
        self.assertEqual((await self.get(draft_revision='0'*64)).json()['error'],'draft_changed_refresh_ticket')
        self.assertEqual((await self.get(expected_recipient='changed@example.com')).json()['error'],'recipient_changed_refresh_ticket')
    async def test_new_customer_message_invalidates_previous_context(self):
        old=(await self.get()).json()['context']
        await Database(self.path).execute("INSERT INTO parsed_messages(message_id,ticket_id,event_type,author_type,is_customer_message,received_at) VALUES('newer',1,'created','customer',1,'2026-09-26T01:00:02+00:00')")
        response=await self.get()
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['error'],'new_customer_message_refresh_ticket')
        self.assertFalse(old['sendEnabled'])
    async def test_existing_console_operation_can_be_resumed_without_new_intent(self):
        store=IntentStore(self.path);operation=str(uuid.uuid4())
        await store.reserve(operation_id=operation,actor_id='owner:owner',kind='send',ticket_id=1,
                            source_message_id='source-1',text='Reviewed text',draft_revision=hashlib.sha256(b'I can help check that.').hexdigest())
        first=(await self.get()).json()['context'];second=(await self.get()).json()['context']
        self.assertEqual(first,second)
        self.assertEqual(first['unresolvedActions'][0]['operationId'],operation)
        self.assertFalse(first['reviewable'])
        self.assertEqual((await Database(self.path).fetch('SELECT COUNT(*) count FROM console_action_intents'))[0]['count'],1)
        status=await self.client.get(f'/dashboard/api/ticket/1/actions/{operation}')
        self.assertEqual(status.json()['operation_id'],operation)
        self.assertEqual(status.json()['delivery_status'],'unknown')
    async def test_changed_recipient_and_source_text_change_context_identity(self):
        old=(await self.get()).json()['context']
        await Database(self.path).execute("UPDATE parsed_messages SET customer_email='changed@example.com',message_text='Changed source text' WHERE message_id='source-1'")
        response=await self.get(expected_recipient=old['recipient'])
        self.assertEqual(response.status_code,409)
        current=(await self.get()).json()['context']
        self.assertNotEqual(current['contextId'],old['contextId'])
        self.assertNotEqual(current['sourceRevision'],old['sourceRevision'])
        self.assertFalse(current['sendEnabled'])
    async def test_other_actor_operation_blocks_without_leaking_identifier(self):
        await IntentStore(self.path).reserve(operation_id=str(uuid.uuid4()),actor_id='owner:other',kind='send',ticket_id=1,
          source_message_id='source-1',text='Reviewed',draft_revision=hashlib.sha256(b'I can help check that.').hexdigest())
        context=(await self.get()).json()['context']
        self.assertFalse(context['reviewable'])
        self.assertFalse(context['unresolvedActions'][0]['ownedByCurrentActor'])
        self.assertNotIn('operationId',context['unresolvedActions'][0])

    async def test_session_required(self):
        self.client.cookies.clear()
        self.assertEqual((await self.get()).status_code,401)
