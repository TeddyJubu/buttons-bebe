"""Draft statements must distinguish confirmed facts from work not yet performed."""
import time
import unittest
import draft_cleaner as cleaner
from hermes_runner.prompt import _build_prompt


class EvidenceDraftTests(unittest.TestCase):
    def test_explicit_uncertainty_is_not_a_completed_cancellation_or_refund_claim(self):
        safe="The order has not shipped, but I can’t confirm that it has been canceled or that a refund will be issued."
        self.assertEqual(cleaner.clean_draft(safe).text,safe)
        for unsafe in (
            "I can't confirm that it was canceled. A refund will be issued.",
            "I can't confirm that it was canceled, but a refund will be issued.",
            "I can't confirm that it was canceled but a refund will be issued.",
            "I can't confirm that it was canceled and we will issue a refund.",
            "I can't confirm that it was canceled; a refund will be issued.",
        ):
            with self.subTest(unsafe=unsafe):self.assertTrue(cleaner.clean_draft(unsafe).no_draft)
    def test_first_person_work_and_followup_promises_are_not_safe_acknowledgments(self):
        for text in (
            "We're reviewing this for you and will get back shortly.",
            "We are currently checking the stock.",
            "I'm looking into your request.",
            "Our team is investigating this.",
            "We'll follow up when the measurements are available.",
            "We will send you an update shortly.",
            "We'll review the gift note request.",
            "I'll check the measurements.",
            "We'll make it right.",
        ):
            with self.subTest(text=text):
                result=cleaner.clean_draft(text)
                self.assertTrue(result.no_draft)
                self.assertTrue(result.reasons)
                self.assertNotEqual(result.text,text)
                self.assertFalse(cleaner._find_action_claim(result.text))
                self.assertLessEqual(len(result.text),len(text))

    def test_policy_facts_and_needed_customer_questions_are_preserved(self):
        for text in (
            "Processing time is separate from carrier delivery time.",
            "The order is marked unfulfilled. I don't have a confirmed dispatch date.",
            "Fit varies by brand. Which item or brand/style is this for?",
            "Could you share a photo of the item with its tag?",
            "Are you checking the product's care label?",
            "The policy allows a refund when its eligibility conditions are met.",
            "A review is required before a decision can be confirmed.",
        ):
            with self.subTest(text=text):
                self.assertEqual(cleaner.clean_draft(text).text,text)

    def test_fallback_preserves_sensitive_header_and_removed_reviewer_warning(self):
        result=cleaner.clean_draft('[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]\n\nWe are reviewing your request.\n\nAGENT NOTE: Verify identity before responding.')
        self.assertEqual(result.text, '')
        self.assertIn('Verify identity',result.removed_note)
        self.assertNotIn('reviewing your request',result.text)
        self.assertTrue(result.no_draft)

    def test_runner_execution_fallback_also_makes_no_work_commitment(self):
        from hermes_runner.constants import _FALLBACK_RESULT, _TOKEN_FAILURE_RESULT
        text=_FALLBACK_RESULT['draft_text']
        self.assertEqual(text, '')
        self.assertEqual(_FALLBACK_RESULT['generation_state'], 'failed')
        self.assertFalse(cleaner._find_action_claim(text))
        self.assertEqual(cleaner.clean_draft(text).text,text)
        self.assertTrue(_TOKEN_FAILURE_RESULT['no_draft'])
        self.assertEqual(_TOKEN_FAILURE_RESULT['draft_text'],'')

    def test_prompt_demands_product_evidence_and_exact_observed_order_state(self):
        prompt=_build_prompt(ticket_id=123,message_text='Sizing question',ticket_subject='Question',customer_email='synthetic@example.invalid',intents=[],token='0123456789abcdef')
        for phrase in ('Never map age or weight alone to a size','exact brand/product','measurements required by its chart',"does NOT mean being prepared",'general processing window is policy','not a promised dispatch date','ask only for a genuinely missing detail','review_required=true','READ-ONLY','<DRAFT:0123456789abcdef>','JSON_RESULT[0123456789abcdef]'):
            self.assertIn(phrase,prompt)
        self.assertNotIn("write 'We're checking on that for you and will follow up shortly.'",prompt)
        self.assertNotIn("say that it is being reviewed",prompt)

    def test_customer_clarification_is_allowed_without_guessing_catalog_identity(self):
        prompt=_build_prompt(ticket_id=123,message_text='Product question',ticket_subject='Question',customer_email='synthetic@example.invalid',intents=[],token='0123456789abcdef')
        for phrase in ('A retrieved catalog candidate is not proof',
                       'do not silently select a brand',
                       'verified ticket/order context identifies it',
                       'a count alone is not an answer',
                       'a few grounded examples',
                       'without implying current stock',
                       'essential missing identifier',
                       'Do not ask again for information already provided',
                       'Do not ask the CLI operator questions',
                       'The customer-facing draft may ask'):
            self.assertIn(phrase,prompt)
        self.assertNotIn('Do not ask questions.',prompt)
        self.assertIn('review_required=true',prompt)
        self.assertIn('READ-ONLY',prompt)
        self.assertIn('JSON_RESULT[0123456789abcdef]',prompt)

    def test_customer_tone_and_service_scope_do_not_erase_financial_uncertainty(self):
        prompt=_build_prompt(ticket_id=123,message_text='General inquiry',ticket_subject='Question',customer_email='synthetic@example.invalid',intents=[],token='0123456789abcdef')
        for phrase in ('acknowledgments warm and specific',
                       "limitations that affect the customer's decision",
                       'Staff routing belongs in staff_next_step',
                       'explicit financial-uncertainty rules below still apply',
                       'does not establish a specialist department',
                       'generic contact page or a product keyword',
                       'For refunds/chargebacks without a confirmed outcome'):
            self.assertIn(phrase,prompt)
        self.assertIn('without claiming it has happened',prompt)
        # Audit fix: the customer draft must name the refund topic plainly and
        # keep uncertainty to one short sentence; the old hedging formula is
        # banned from customer-facing text (it belongs in AGENT NOTE at most).
        self.assertIn('about your refund request',prompt)
        self.assertIn('from the information available',prompt)
        self.assertIn('authenticated JSON_RESULT',prompt)
        self.assertNotIn("I can't confirm an outcome for this request from the information",prompt)

    def test_review_commitment_detector_has_bounded_cpu_on_adversarial_near_matches(self):
        samples=('we '+' '*100000+'are not checking',('we will follow '+'x'*100+' ')*1000,('our team is '+ 'currently '*20)*1000)
        start=time.process_time()
        for sample in samples:cleaner._REVIEW_COMMITMENT_RE.search(sample)
        self.assertLess(time.process_time()-start,0.5)


if __name__=='__main__':unittest.main()
