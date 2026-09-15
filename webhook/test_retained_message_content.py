import unittest
from unittest.mock import patch
from types import SimpleNamespace
from bb_webhook.message_content import message_text

class RetainedContentTests(unittest.TestCase):
    def test_stripped_content_wins_over_quoted_full_bodies(self):
        self.assertEqual(message_text({'stripped_text':'New question','body_text':'New question plus old thread'}),'New question')
        self.assertEqual(message_text({'stripped_html':'<p>New &amp; current</p>','body_text':'Old quoted thread'}),'New & current')
    def test_nullable_full_body_html_conversion_and_no_archive_fetch(self):
        self.assertEqual(message_text({'body_text':None,'body_html':None,'stripped_html':'<p>Hello</p><script>bad()</script><p>Question</p>','body_url':'http://127.0.0.1/private'}),'Hello\n\nQuestion')
        self.assertEqual(message_text({'body_text':None,'body_html':None,'body_url':'http://127.0.0.1/private'}),'')
        self.assertEqual(message_text({'stripped_text':{},'body_html':'<p>Fallback</p>'}),'Fallback')

    @patch("bb_webhook.webhook_handler.get_settings", return_value=SimpleNamespace(gorgias_subdomain="synthetic"))
    def test_webhook_parser_keeps_retained_html_customer_message(self, settings):
        import json
        from bb_webhook.webhook_handler import parse_event
        parsed = parse_event(json.dumps({'event':'ticket-message-created',
            'ticket':{'id':123}, 'message':{'id':456, 'from_agent':False,
            'created_datetime':'2026-09-07T00:00:00Z', 'channel':'email',
            'stripped_html':'<p>Current &amp; retained</p>', 'body_text':None,
            'body_html':None, 'headers':None}}).encode())
        self.assertEqual(parsed['message_text'], 'Current & retained')
        self.assertTrue(parsed['is_customer_message'])

    @patch("bb_webhook.webhook_handler.get_settings", return_value=SimpleNamespace(gorgias_subdomain="synthetic"))
    def test_webhook_parser_keeps_bounded_ticket_status(self, settings):
        import json
        from bb_webhook.webhook_handler import parse_event
        def parse(ticket):
            return parse_event(json.dumps({'event':'ticket-message-created',
                'ticket':ticket, 'message':{'id':456, 'from_agent':False,
                'created_datetime':'2026-09-07T00:00:00Z'}}).encode())
        self.assertEqual(parse({'id':123, 'status':'closed'})['ticket_status'], 'closed')
        self.assertEqual(parse({'id':123, 'status':' Open '})['ticket_status'], 'open')
        for bad in (None, 123, '', 'x'*31, '<script>', 'open; DROP TABLE x'):
            payload = {'id':123} if bad is None else {'id':123, 'status':bad}
            self.assertIsNone(parse(payload)['ticket_status'])
