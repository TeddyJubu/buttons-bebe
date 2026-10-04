import unittest
from tools.gorgias_content import curate_message,curate_messages,curate_ticket

class GorgiasContentTests(unittest.TestCase):
    def test_retained_content_selected_and_original_metadata_preserved(self):
        message={'id':123,'body_text':None,'body_html':None,'stripped_text':'Retained reply','headers':None,'body_url':'https://archive.example/private'}
        result=curate_messages({'data':[message]})['data'][0]
        self.assertEqual(result['preferred_content'],'Retained reply')
        self.assertEqual(result['preferred_content_field'],'stripped_text')
        self.assertFalse(result['content_unavailable'])
        self.assertEqual(result['body_url'],message['body_url'])
        self.assertNotIn('preferred_content',message)
    def test_html_only_and_missing_content_are_explicit(self):
        result=curate_ticket({'messages':[{'stripped_html':'<p>Retained</p>','body_text':'Old thread'}]})
        self.assertEqual(result['messages'][0]['preferred_content_field'],'stripped_html')
        self.assertTrue(curate_message({'body_text':None,'headers':None,'body_url':'http://127.0.0.1/private'})['content_unavailable'])

    def test_preferred_hint_stays_raw_while_derived_fields_are_clean(self):
        from tools.gorgias_content import curate_summaries
        raw_html = '<p>Retained</p><div style="display:none">secret</div>'
        message = {'id': 99, 'stripped_html': raw_html, 'body_text': 'Old thread', 'body_url': 'http://127.0.0.1/private'}
        result = curate_message(message)
        self.assertEqual(result['id'], 99)
        self.assertEqual(result['preferred_content'], raw_html)
        self.assertEqual(result['preferred_content_field'], 'stripped_html')
        self.assertEqual(result['body_url'], 'http://127.0.0.1/private')
        self.assertEqual(result['current_text'], 'Retained')
        self.assertEqual(result['current_source'], 'stripped_html')
        self.assertEqual(result['display_text'], 'Old thread')
        self.assertEqual(result['display_source'], 'body_text')
        self.assertEqual(result['original_content'], 'Old thread')
        self.assertEqual(result['original_field'], 'body_text')
        self.assertTrue(result['history_available'])
        self.assertFalse(result['source_truncated'])
        self.assertEqual(result['cleanup_version'], 'intake-1')
        self.assertNotIn('secret', result['current_text'])
        self.assertNotIn('preferred_content', message)
        page = curate_summaries({'data': [{
            'id': 7,
            'excerpt': '<p>Hello</p> https://fonts.gstatic.com/s/a.woff2',
        }]})
        row = page['data'][0]
        self.assertEqual(row['id'], 7)
        self.assertEqual(row['excerpt'], 'Hello')
        self.assertNotIn('woff', row['excerpt'])
        self.assertEqual(row['original_content'], '<p>Hello</p> https://fonts.gstatic.com/s/a.woff2')
        self.assertEqual(row['original_field'], 'excerpt')
        self.assertTrue(row['source_truncated'])
        self.assertFalse(row['history_available'])
        self.assertEqual(row['cleanup_version'], 'intake-1')
