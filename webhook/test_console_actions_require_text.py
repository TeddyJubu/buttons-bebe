"""Authenticated console actions reject empty text before any external write."""
import unittest
from unittest.mock import patch
from bb_webhook.routers import console as console_router
from webhook.action_test_support import setup_action_case

class ConsoleActionsRequireTextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await setup_action_case(self)

    async def test_send_and_note_reject_empty_text_with_400(self):
        with patch.object(console_router, '_GClient') as transport:
            send = await self.client.post('/dashboard/api/ticket/1/send', json={'text':'   ', 'confirmed': True})
            note = await self.client.post('/dashboard/api/ticket/1/note', json={'text':'', 'confirmed': True})
        self.assertEqual(send.status_code, 400)
        self.assertEqual(send.json(), {'ok':False,'error':'empty reply','delivery_status':'not_attempted'})
        self.assertEqual(note.status_code, 400)
        self.assertEqual(note.json(), {'ok':False,'error':'empty note','delivery_status':'not_attempted'})
        transport.assert_not_called()
