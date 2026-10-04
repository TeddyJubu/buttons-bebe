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
        top = parse_event(json.dumps({'event':'ticket-message-created','status':'closed',
            'ticket':{'id':123}, 'message':{'id':456, 'from_agent':False,
            'created_datetime':'2026-09-07T00:00:00Z'}}).encode())
        self.assertEqual(top['ticket_status'], 'closed')
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

    def test_normalize_splits_display_history_from_current_ai_text(self):
        import json
        from bb_webhook.message_content import CLEANUP_VERSION, normalize_message
        html = ('<div style="display:none">Preview</div>'
                '<p>Where is order #10322954?</p><blockquote>Earlier note</blockquote>')
        message = {
            'id': 456,
            'stripped_text': 'Where is order #10322954?\nSent from my iPhone',
            'body_html': html,
            'body_url': 'http://127.0.0.1/private',
        }
        result = normalize_message(message)
        self.assertEqual(result, {
            'display_text': 'Where is order #10322954?\n\n> Earlier note',
            'current_text': 'Where is order #10322954?',
            'display_source': 'body_html',
            'current_source': 'stripped_text',
            'original_content': html,
            'original_field': 'body_html',
            'history_available': True,
            'source_truncated': False,
            'cleanup_version': 'intake-1',
        })
        self.assertEqual(message['id'], 456)
        self.assertNotIn('127.0.0.1', result['display_text'])
        self.assertNotIn('127.0.0.1', result['original_content'])
        self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertEqual(CLEANUP_VERSION, 'intake-1')
        self.assertEqual(message_text(message), 'Where is order #10322954?')

    def test_hidden_html_font_urls_and_preheader_padding_leave_visible_text(self):
        from bb_webhook.message_content import normalize_message
        html = (
            '<html><head><title>Secret title</title>'
            '<link href="https://fonts.googleapis.com/css?family=Roboto" rel="stylesheet">'
            '</head><body>'
            '<div class="preheader" style="display:none">Preview padding'
            + ('\u200c\u00a0' * 12) +
            '</div>'
            '<p>Hello \U0001F476</p>'
            '<p>https://fonts.gstatic.com/s/roboto/v30/abc.woff2</p>'
            '<p>অর্ডার #10322954</p>'
            '<p>Family \U0001F469\u200d\U0001F467</p>'
            '<p>ক\u200cখ</p>'
            '</body></html>'
        )
        result = normalize_message({'id': 77, 'body_html': html})
        self.assertEqual(
            result['display_text'],
            'Hello \U0001F476\n\nঅর্ডার #10322954\n\nFamily \U0001F469\u200d\U0001F467\n\nক\u200cখ',
        )
        self.assertEqual(result['current_text'], result['display_text'])
        self.assertNotIn('Secret title', result['display_text'])
        self.assertNotIn('Preview', result['display_text'])
        self.assertNotIn('fonts.', result['display_text'])
        self.assertNotIn('woff', result['display_text'])
        self.assertEqual(result['original_content'], html)
        self.assertEqual(result['original_field'], 'body_html')
        self.assertTrue(result['history_available'])

    def test_meaningful_link_destination_survives_and_archive_url_is_not_used(self):
        from bb_webhook.message_content import normalize_message
        html = ('<p>Track <a href="https://buttonsbebe.com/orders/10322954">your order</a>.</p>'
                '<p><a href="javascript:alert(1)">ignore this script</a></p>')
        result = normalize_message({'body_html': html, 'body_url': 'http://127.0.0.1/archive-body'})
        self.assertEqual(
            result['display_text'],
            'Track your order (https://buttonsbebe.com/orders/10322954).\n\nignore this script',
        )
        self.assertNotIn('javascript:', result['display_text'])
        self.assertNotIn('127.0.0.1', result['display_text'])
        self.assertFalse(result['source_truncated'])

    def test_excerpt_and_stripped_only_history_are_explicit(self):
        from bb_webhook.message_content import normalize_message
        excerpt = normalize_message({
            'excerpt': '<div style="display:none">hidden</div><p>Order #10322954</p>',
            'body_url': 'http://127.0.0.1/secret',
        })
        self.assertEqual(excerpt['display_text'], 'Order #10322954')
        self.assertEqual(excerpt['current_text'], 'Order #10322954')
        self.assertEqual(excerpt['display_source'], 'excerpt')
        self.assertEqual(excerpt['original_field'], 'excerpt')
        self.assertFalse(excerpt['history_available'])
        self.assertTrue(excerpt['source_truncated'])
        self.assertNotIn('hidden', excerpt['display_text'])
        self.assertNotIn('127.0.0.1', excerpt['original_content'])
        stripped = normalize_message({
            'stripped_text': 'New question',
            'body_text': None,
            'body_url': 'http://127.0.0.1/private',
        })
        self.assertEqual(stripped['current_text'], 'New question')
        self.assertEqual(stripped['display_text'], 'New question')
        self.assertFalse(stripped['history_available'])
        self.assertTrue(stripped['source_truncated'])
        self.assertEqual(stripped['original_field'], 'stripped_text')
        self.assertIsNone(normalize_message('nope')['display_source'])

    def test_bottom_post_and_quote_only_rules_stay_on_current_text(self):
        from bb_webhook.message_content import normalize_message
        bottom = normalize_message({'body_text': '> we are looking into it\n\nWHERE IS IT I HAVE HAD ENOUGH'})
        self.assertEqual(bottom['display_text'], '> we are looking into it\n\nWHERE IS IT I HAVE HAD ENOUGH')
        self.assertEqual(bottom['current_text'], 'WHERE IS IT I HAVE HAD ENOUGH')
        quoted = normalize_message({'body_text': '> I want a refund, my order arrived damaged'})
        self.assertEqual(quoted['current_text'], '> I want a refund, my order arrived damaged')
        self.assertEqual(quoted['display_text'], quoted['current_text'])
        signature = normalize_message({'body_text': 'Sent from my Galaxy'})
        self.assertEqual(signature['display_text'], 'Sent from my Galaxy')
        self.assertEqual(signature['current_text'], '')

    def test_hundred_kilobyte_message_keeps_both_ends(self):
        import time
        from bb_webhook.message_content import normalize_message
        filler = 'a' * 100_000
        html = (
            '<div style="display:none">Preview padding' + ('\u200c\u00a0' * 20) + '</div>'
            '<p>Start অর্ডার #10322954 \U0001F476</p>'
            '<p>https://fonts.gstatic.com/s/roboto/v30/abc.woff2</p>'
            '<p>' + filler + '</p>'
            '<p>End অর্ডার #10322954</p>'
        )
        started = time.perf_counter()
        result = normalize_message({'id': 100, 'body_html': html})
        for _ in range(19):
            normalize_message({'id': 100, 'body_html': html})
        elapsed = time.perf_counter() - started
        self.assertEqual(result['display_text'].split('\n\n')[0], 'Start অর্ডার #10322954 \U0001F476')
        self.assertTrue(result['display_text'].endswith('End অর্ডার #10322954'))
        self.assertNotIn('fonts.gstatic.com', result['display_text'])
        self.assertNotIn('Preview', result['display_text'])
        self.assertIn(filler, result['display_text'])
        self.assertEqual(result['original_content'], html)
        self.assertLess(elapsed, 2.0, f'20 normalizations of a 100KB message took {elapsed:.3f}s')

    @patch("bb_webhook.webhook_handler.get_settings", return_value=SimpleNamespace(gorgias_subdomain="synthetic"))
    def test_webhook_keeps_current_text_and_intake_provenance(self, settings):
        import json
        from bb_webhook.webhook_handler import parse_event
        html = ('<div style="display:none">Preview</div>'
                '<p>Where is order #10322954?</p><blockquote>Earlier note</blockquote>')
        payload = {'event': 'ticket-message-created', 'ticket': {'id': 123}, 'message': {
            'id': 456, 'from_agent': False, 'created_datetime': '2026-09-07T00:00:00Z',
            'channel': 'email', 'stripped_text': 'Where is order #10322954?\nSent from my iPhone',
            'body_html': html, 'body_url': 'http://127.0.0.1/private',
        }}
        parsed = parse_event(json.dumps(payload).encode())
        self.assertEqual(parsed['message_id'], 456)
        self.assertEqual(parsed['message_text'], 'Where is order #10322954?')
        self.assertEqual(parsed['intake']['display_text'], 'Where is order #10322954?\n\n> Earlier note')
        self.assertEqual(parsed['intake']['current_source'], 'stripped_text')
        self.assertEqual(parsed['intake']['cleanup_version'], 'intake-1')
        self.assertTrue(parsed['intake']['history_available'])
        self.assertEqual(parsed['raw']['message']['body_html'], html)
        self.assertEqual(parsed['raw']['message']['body_url'], 'http://127.0.0.1/private')
