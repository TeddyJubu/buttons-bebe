from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.gorgias_content import curate_message
import live_api

class MessageDisplayTests(unittest.TestCase):
    def test_intake_owns_cleaning_and_history(self):
        source={'id':1,'stripped_text':'Where is order #12345?',
                'body_html':'<head><title>Hidden title</title></head><p style="display:none">Preheader</p><p>Where is order #12345?</p><blockquote>Earlier email</blockquote>'}
        curated=curate_message(source)
        result=live_api.message(curated)
        self.assertEqual(result['body'],'Where is order #12345?\n\n> Earlier email')
        self.assertEqual(result['current_text'],'Where is order #12345?')
        self.assertEqual(result['original_content'],source['body_html'])
        self.assertEqual(result['id'],'1')
        self.assertTrue(result['history_available'])
    def test_greeting_paragraphs_and_lists_keep_breaks(self):
        raw='Hi team,\nPlease review this request.\n\n- Keep this item\n- Return that one'
        self.assertEqual(live_api.message(curate_message({'stripped_text':raw}))['body'],raw)
    def test_missing_history_is_explicit_not_reconstructed(self):
        result=live_api.message(curate_message({'stripped_html':'<div>Hello<br>World</div>','body_url':'https://archive.invalid/body'}))
        self.assertEqual(result['body'],'Hello\nWorld')
        self.assertFalse(result['history_available']);self.assertTrue(result['source_truncated'])
    def test_attachment_metadata_and_original_fields_survive(self):
        source={'body_text':'See the image.','attachments':[{'url':'https://example.test/image.png','name':'image.png','content_type':'image/png','secret':'never export'}]}
        result=live_api.message(curate_message(source))
        self.assertEqual(result['attachments'],[{'url':'https://example.test/image.png','name':'image.png','content_type':'image/png'}])
