"""Contradictory model fields cannot bypass the persisted review contract."""
import json
import unittest
from unittest.mock import patch

import orchestrator
from draft_cleaner import SENSITIVE_DRAFT_PREFIX
from hermes_runner.runner import draft_for_console
from shared.review_policy import final_review_result


class FinalReviewTests(unittest.IsolatedAsyncioTestCase):
    async def persist(self, model):
        job = {'id': 7, 'payload': json.dumps({'ticket_id': 123, 'message_id': 'synthetic',
                                              'message_text': 'A synthetic product question'})}
        ordinary = {'priority': 'normal', 'sensitive': False, 'reason': 'Synthetic',
                    'should_notify_owner': False}
        with patch.object(orchestrator, 'deterministic_classify', return_value=ordinary), \
             patch.object(orchestrator, 'process_ticket_with_hermes', return_value=dict(model)), \
             patch.object(orchestrator, '_save_result_to_webhook') as save:
            returned = await orchestrator.process_customer_message(job)
        return returned, save.call_args.kwargs

    async def test_contradictory_outputs_are_sensitive_at_actual_persistence_boundary(self):
        for fields in ({'action': 'escalated', 'priority': 'low'},
                       {'action': 'drafted', 'priority': ' CRITICAL '},
                       {'action': 'drafted', 'priority': 'HIGH'}):
            with self.subTest(fields=fields):
                model = {'draft_text': 'Please share the product name.', 'notify_owner': False, **fields}
                returned, saved = await self.persist(model)
                expected = 'critical' if str(fields['priority']).strip().lower() == 'critical' else 'high'
                self.assertEqual(returned['action'], 'sensitive_draft')
                self.assertEqual(returned['priority'], expected)
                self.assertTrue(saved['hermes_result']['notify_owner'])
                self.assertTrue(saved['draft_text'].startswith(SENSITIVE_DRAFT_PREFIX))
                self.assertEqual(saved['draft_text'], draft_for_console(model))
                self.assertFalse(saved['hermes_result']['note_posted'])

    async def test_missing_facts_and_headers_require_review_without_urgency(self):
        for fields in ({'action': 'no_kb_match'}, {'action': 'drafted',
                'draft_text': '[SENSITIVE] Please share the product name.'}):
            _, saved = await self.persist({'priority': 'normal',
                'draft_text': 'Please share the product name.', **fields})
            self.assertEqual(saved['hermes_result']['priority'], 'normal')
            self.assertTrue(saved['hermes_result']['review_required'])
            self.assertFalse(saved['hermes_result']['notify_owner'])
            self.assertFalse(saved['draft_text'].startswith('[SENSITIVE'))

    async def test_normal_facts_remain_ordinary_without_owner_alert(self):
        for priority in ('low', 'normal'):
            model = {'action': 'drafted', 'priority': priority, 'notify_owner': False,
                     'draft_text': 'This product is cotton.'}
            _, saved = await self.persist(model)
            self.assertEqual(saved['draft_text'], model['draft_text'])
            self.assertFalse(saved['hermes_result']['notify_owner'])
            self.assertEqual(saved['hermes_result']['action'], 'drafted')

    async def test_authentication_failure_never_becomes_a_draft(self):
        _, saved = await self.persist({'action': 'escalated', 'priority': 'high',
                                      'no_draft': True, 'draft_text': 'Untrusted content'})
        self.assertEqual(saved['draft_text'], '')
        self.assertTrue(saved['hermes_result']['notify_owner'])

    def test_policy_is_idempotent_does_not_mutate_input(self):
        original = {'priority': 'normal', 'action': 'no_kb_match', 'draft_text': 'Question?'}
        reviewed = final_review_result(original)
        self.assertEqual(reviewed, final_review_result(reviewed))
        self.assertEqual(original['action'], 'no_kb_match')

    async def test_explicit_empty_low_no_action_does_not_create_alert(self):
        _, saved = await self.persist({'action': 'drafted', 'priority': 'low',
                                      'no_draft': True, 'draft_text': '', 'notify_owner': False})
        self.assertEqual(saved['draft_text'], '')
        self.assertFalse(saved['hermes_result']['notify_owner'])
