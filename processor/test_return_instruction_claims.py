"""Narrow confirmed packing guidance is not a promise to perform a return."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_draft_cleaner_wiring import _compliant, _call
from hermes_runner import draft_for_console
from draft_cleaner import clean_draft

INSTRUCTION = ('Please include a note inside the package identifying each item and its '
               'order number so the warehouse can process each return correctly.')


class ReturnInstructionTests(unittest.TestCase):
    def test_confirmed_customer_packing_instruction_is_preserved(self):
        text = ('Yes, you can place returns from different orders in the same package. '
                + INSTRUCTION + ' Both drop-off and mail options work.')
        self.assertEqual(clean_draft(text).text, text)

    def test_observed_combined_return_guidance_is_preserved(self):
        text = ('Please include a note inside listing each item and its order '
                'number so the warehouse can process them correctly.')
        self.assertEqual(clean_draft(text).text, text)
        for unsafe in (text + ' We will issue your refund.',
                       text.replace('correctly.', 'correctly and refund you.'),
                       text.replace('them correctly', 'your refund today')):
            self.assertTrue(clean_draft(unsafe).no_draft)

    def test_exception_does_not_hide_other_claims_or_financial_changes(self):
        for text in (INSTRUCTION + ' We will issue your refund.',
                     'We will credit your account. ' + INSTRUCTION,
                     INSTRUCTION.replace('each return correctly', 'each refund correctly'),
                     INSTRUCTION.replace('each return correctly', 'each credit correctly'),
                     INSTRUCTION.replace('correctly.', 'correctly and we will refund you.'),
                     'The warehouse can process your return today.',
                     INSTRUCTION + " We'll check and get back shortly."):
            with self.subTest(text=text):
                result = clean_draft(text)
                self.assertNotEqual(result.text, text)
                self.assertTrue(result.reasons)

    def test_observed_spanish_review_commitment_is_non_sendable(self):
        for text in ('Estamos revisando el estado de tu pedido.',
                     'Estoy comprobando tu pedido.', 'Revisaremos tu solicitud.'):
            with self.subTest(text=text):
                result = clean_draft(text)
                self.assertTrue(result.no_draft)
                self.assertEqual(result.text, '')
                self.assertIn('Spanish', result.reasons[-1])

    def test_spanish_facts_and_customer_questions_are_not_work_claims(self):
        for text in ('El pedido aparece como no enviado.',
                     '¿Puedes compartir el número de pedido?',
                     'La política requiere una revisión antes de confirmar una decisión.'):
            self.assertEqual(clean_draft(text).text, text)

    def test_spanish_claim_stays_empty_through_authenticated_runner(self):
        settings = SimpleNamespace(job_timeout=30, hermes_toolsets='buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias')
        with patch('hermes_runner.runner.get_settings', return_value=settings), \
             patch('hermes_runner.runner.run_bounded', side_effect=_compliant(
                 draft='Estamos revisando el estado de tu pedido.')):
            result = _call()
        self.assertTrue(result['no_draft'])
        self.assertTrue(result['notify_owner'])
        self.assertEqual(draft_for_console(result), '')


class ReturnInstructionPatternBoundaryTests(unittest.TestCase):
    def test_standalone_search_is_anchored_without_changing_sentence_matching(self):
        from draft_cleaner import _RETURN_IDENTIFICATION_INSTRUCTION_RE as pattern
        self.assertIsNotNone(pattern.search(' \t'+INSTRUCTION+' \n'))
        self.assertIsNone(pattern.search('Unrelated introduction. '+INSTRUCTION))
        self.assertIsNone(pattern.search(INSTRUCTION+' Unrelated trailing promise.'))
        self.assertIsNone(pattern.search(' '*12000+'not an instruction'))
