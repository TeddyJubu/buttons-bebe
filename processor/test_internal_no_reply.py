"""Internal no-action instructions must never become sendable customer drafts."""
import unittest
from draft_cleaner import clean_draft
from hermes_runner.runner import draft_for_console
from test_draft_cleaner_wiring import _compliant, _raw
from types import SimpleNamespace
from unittest.mock import patch
from hermes_runner import process_ticket_with_hermes


class InternalNoReplyTests(unittest.TestCase):
    def test_authenticated_no_reply_verdict_is_suppressed_without_safety_failure(self):
        output='<DRAFT:@@T@@>No reply needed — empty message.</DRAFT:@@T@@>\nJSON_RESULT[@@T@@]: {"priority":"low","action":"no_draft_needed","reason":"No request","notify_owner":false}'
        with patch('hermes_runner.runner.get_settings',return_value=SimpleNamespace(job_timeout=30,hermes_toolsets='buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias')), \
             patch('hermes_runner.runner.run_bounded',side_effect=_raw(output)):
            result=process_ticket_with_hermes(123,'','Empty / survey','',[])
        self.assertEqual(result['generation_state'],'no_reply')
        self.assertFalse(result['notify_owner'])
        self.assertEqual(result['draft_text'],'')

    def test_bounded_internal_markers_are_empty_with_diagnostic(self):
        for text in ('No reply needed.', 'No response required', 'No draft is necessary.',
                     'No reply needed — this message contains no customer question or request.'):
            with self.subTest(text=text):
                cleaned = clean_draft(text)
                self.assertEqual(cleaned.text, '')
                self.assertTrue(cleaned.no_draft)
                self.assertIn('internal no-reply', cleaned.reasons[0])

    def test_customer_facing_instructions_and_questions_remain(self):
        for text in ('You do not need to reply to this message.',
                     'Do you mean no reply is needed?',
                     'No reply needed? Please clarify your question.',
                     'The label says no response required.'):
            self.assertEqual(clean_draft(text).text, text)

    def test_authenticated_runner_does_not_promote_internal_marker_to_fallback(self):
        with patch('hermes_runner.runner.get_settings', return_value=SimpleNamespace(job_timeout=30, hermes_toolsets='buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias')), \
             patch('hermes_runner.runner.run_bounded', side_effect=_compliant(
                draft='No reply needed — this message contains no customer question or request.')):
            result = process_ticket_with_hermes(ticket_id=123, message_text='Synthetic question', ticket_subject='', customer_email='', intents=[])
        self.assertTrue(result['no_draft'])
        self.assertEqual(draft_for_console(result), '')
        # A model saying no action for a real question still requires review.
        self.assertTrue(result['notify_owner'])
