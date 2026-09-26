"""The alert health probe authenticates without sending a message."""
import io
import json
import os
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import urllib.error
from unittest.mock import MagicMock, patch
from whatsapp_notifier import check_alert_route


class AlertRouteTests(unittest.TestCase):
    def probe(self, status=200, body=None):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps(body or {
            'ok':True,'route':'owner_alert','connected':True,'destinationConfigured':True}).encode()
        opener=MagicMock()
        opener.open.return_value=response
        if status!=200:
            opener.open.side_effect=urllib.error.HTTPError('secret-route',status,'synthetic',{},io.BytesIO())
        with patch.dict(os.environ,{'WHATSAPP_SEND_URL':'http://127.0.0.1:8085/connect-whatsapp/test/send',
                                    'WA_SEND_SECRET':'test-secret'},clear=True), \
             patch('whatsapp_notifier.urllib.request.build_opener',return_value=opener):
            result=check_alert_route()
        request=opener.open.call_args.args[0]
        self.assertEqual(request.method,'GET')
        self.assertIsNone(request.data)
        self.assertTrue(request.full_url.endswith('/send/check'))
        self.assertEqual(request.get_header('Authorization'),'Bearer test-secret')
        self.assertNotIn('test-secret',json.dumps(result))
        self.assertNotIn('secret-route',json.dumps(result))
        return result

    def test_route_ok_does_not_claim_device_delivery(self):
        result=self.probe()
        self.assertEqual(result['status'],'ok')
        self.assertNotIn('delivered',result)

    def test_route_mismatch_and_authentication_failure(self):
        self.assertEqual(self.probe(404)['status'],'route_mismatch')
        self.assertEqual(self.probe(401)['status'],'authentication_failed')

    def test_unexpected_response_is_not_accepted(self):
        self.assertEqual(self.probe(body={'ok':True})['status'],'invalid_response')
