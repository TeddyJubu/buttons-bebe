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

    @patch("bb_webhook.webhook_handler.get_settings", return_value=SimpleNamespace(gorgias_subdomain="synthetic"))
    def test_webhook_parser_keeps_bounded_ticket_assignee(self, settings):
        import json
        from bb_webhook.webhook_handler import parse_event
        def parse(ticket):
            return parse_event(json.dumps({'event':'ticket-message-created',
                'ticket':ticket, 'message':{'id':456, 'from_agent':False,
                'created_datetime':'2026-09-07T00:00:00Z'}}).encode())
        self.assertEqual(parse({'id':123, 'assignee':{'email':'agent@example.com', 'name':'Agent'}})['ticket_assignee'], 'agent@example.com')
        self.assertEqual(parse({'id':123, 'assignee':{'name':'Agent Only'}})['ticket_assignee'], 'Agent Only')
        self.assertEqual(parse({'id':123, 'assignee':'{"email":"json@example.com"}'})['ticket_assignee'], 'json@example.com')
        self.assertEqual(parse({'id':123, 'assignee':'agent@example.com'})['ticket_assignee'], 'agent@example.com')
        self.assertEqual(parse({'id':123, 'assignee_user':{'email':'alias@example.com'}})['ticket_assignee'], 'alias@example.com')
        for bad in (None, 123, '', {}, {'id':7}, 'x'*121, '<b>Agent</b>', 'a;b'):
            payload = {'id':123} if bad is None else {'id':123, 'assignee':bad}
            self.assertIsNone(parse(payload)['ticket_assignee'])

    @patch("bb_webhook.webhook_handler.get_settings", return_value=SimpleNamespace(gorgias_subdomain="synthetic"))
    def test_webhook_parser_keeps_bounded_ticket_tags(self, settings):
        import json
        from bb_webhook.webhook_handler import parse_event
        def parse(ticket):
            return parse_event(json.dumps({'event':'ticket-message-created',
                'ticket':ticket, 'message':{'id':456, 'from_agent':False,
                'created_datetime':'2026-09-07T00:00:00Z'}}).encode())
        self.assertEqual(parse({'id':123, 'tags':['vip','Refund Requested']})['ticket_tags'], ['vip','Refund Requested'])
        self.assertEqual(parse({'id':123, 'tags':'["vip"]'})['ticket_tags'], ['vip'])
        self.assertEqual(parse({'id':123, 'tags':[{'name':'vip'}, {'name':'vip'}, 'vip']})['ticket_tags'], ['vip'])
        self.assertEqual(parse({'id':123, 'tags':[f'tag{i}' for i in range(20)]})['ticket_tags'], [f'tag{i}' for i in range(12)])
        for bad in (None, 123, '', 'not json', {'id':7}, ['x'*41, 'ok'], ['<b>vip</b>', 'ok'], [None, 123, {'id':7}], '[]extra'):
            payload = {'id':123} if bad is None else {'id':123, 'tags':bad}
            tags = parse(payload)['ticket_tags']
            self.assertTrue(isinstance(tags, list))
            self.assertNotIn('<b>vip</b>', tags)
            self.assertNotIn('x'*41, tags)

    @patch("bb_webhook.webhook_handler.get_settings", return_value=SimpleNamespace(gorgias_subdomain="synthetic"))
    def test_webhook_parser_keeps_bounded_ticket_priority(self, settings):
        import json
        from bb_webhook.webhook_handler import parse_event
        def parse(ticket):
            return parse_event(json.dumps({'event':'ticket-message-created',
                'ticket':ticket, 'message':{'id':456, 'from_agent':False,
                'created_datetime':'2026-09-07T00:00:00Z'}}).encode())
        self.assertEqual(parse({'id':123, 'priority':'urgent'})['ticket_priority'], 'urgent')
        self.assertEqual(parse({'id':123, 'priority':' High '})['ticket_priority'], 'high')
        self.assertEqual(parse({'id':123, 'priority':'level-2'})['ticket_priority'], 'level-2')
        for bad in (None, 123, '', 'x'*21, '<b>high</b>', 'high; DROP', 'high priority'):
            payload = {'id':123} if bad is None else {'id':123, 'priority':bad}
            self.assertIsNone(parse(payload)['ticket_priority'])

    @patch("bb_webhook.webhook_handler.get_settings", return_value=SimpleNamespace(gorgias_subdomain="synthetic"))
    def test_webhook_parser_keeps_observed_state_flags_without_dropping(self, settings):
        import json
        from bb_webhook.webhook_handler import parse_event
        def parse(ticket):
            return parse_event(json.dumps({'event':'ticket-message-created',
                'ticket':ticket, 'message':{'id':456, 'from_agent':False,
                'created_datetime':'2026-09-07T00:00:00Z'}}).encode())
        self.assertEqual(parse({'id':123, 'spam':True})['ticket_spam'], 1)
        self.assertEqual(parse({'id':123, 'spam':'True'})['ticket_spam'], 1)
        self.assertEqual(parse({'id':123, 'trashed_datetime':'2026-09-07T01:00:00Z'})['ticket_trashed'], 1)
        self.assertEqual(parse({'id':123, 'trashed':True})['ticket_trashed'], 1)
        self.assertEqual(parse({'id':123, 'snooze_datetime':'2026-09-08T01:00:00Z'})['ticket_snoozed'], 1)
        self.assertEqual(parse({'id':123, 'snoozed':True})['ticket_snoozed'], 1)
        flagged = parse({'id':123, 'spam':True, 'trashed_datetime':'2026-09-07T01:00:00Z'})
        self.assertEqual((flagged['ticket_spam'], flagged['ticket_trashed'], flagged['ticket_snoozed']), (1, 1, 0))
        for ticket in ({'id':123}, {'id':123, 'spam':'maybe'}, {'id':123, 'trashed_datetime':'not-a-time'},
                {'id':123, 'snooze_datetime':'not-a-time'}, {'id':123, 'spam':'<b>yes</b>'}):
            parsed = parse(ticket)
            self.assertIsNotNone(parsed)
            self.assertEqual((parsed['ticket_spam'], parsed['ticket_trashed'], parsed['ticket_snoozed']), (0, 0, 0))
