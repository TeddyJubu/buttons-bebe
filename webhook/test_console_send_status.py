"""Authenticated dashboard replies expose durable Gorgias delivery state."""
import unittest
import uuid
import hashlib
from unittest.mock import Mock, patch
from bb_webhook.routers import console as console_router
from webhook.action_test_support import enable_action_send_access, setup_action_case

class ConsoleSendStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await setup_action_case(self)
        await enable_action_send_access(self)

    async def _post(self, result):
        fake=Mock()
        async def send(ticket_id,text,**kwargs):
            await kwargs['on_created'](result['message_id'])
            return result
        fake.send_public_reply=send
        with patch.object(console_router,'_GClient',return_value=fake):
            return await self.client.post('/dashboard/api/ticket/1/send',json={
                'text':'Thanks!', 'confirmed':True,'operation_id':str(uuid.uuid4()),'source_message_id':'source-1',
                'draft_revision':hashlib.sha256(b'I can help check that.').hexdigest()})

    async def test_confirmed_sent_reply_returns_delivery_confirmation(self):
        response=await self._post({'ok':True,'delivery_status':'sent','message_id':9001})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['delivery_status'],'sent')
        self.assertEqual(response.json()['message_id'],9001)
        self.assertTrue(response.json()['operation_id'])

    async def test_pending_reply_is_not_labelled_sent(self):
        response=await self._post({'ok':True,'delivery_status':'pending','message_id':9002})
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()['delivery_status'],'pending')
        self.assertFalse(response.json()['ok'])
