import json
import unittest
from unittest.mock import patch

import orchestrator
from draft_cleaner import SENSITIVE_DRAFT_PREFIX
from hermes_runner.extract import _parse_json_result


class KBOutcomeReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_guidance_holds_specific_staff_task_without_creating_urgency(self):
        for message, expected_priority, notify in (
            ("What is the return window?", "normal", False),
            ("I want a refund for order #1234.", "critical", True),
            ("The romper arrived torn at the seam and is defective.", "critical", True),
        ):
            with self.subTest(message=message):
                token = "0123456789abcdef"
                verdict = {
                    "priority": "normal", "reason": "Current policy needs staff verification.",
                    "action": "no_kb_match", "notify_owner": False, "review_required": True,
                    "missing_facts": ["Current return policy and active owner overrides"],
                    "staff_next_step": "Verify the current return policy and owner notices before completing this reply.",
                }
                model = _parse_json_result(f"JSON_RESULT[{token}]: " + json.dumps(verdict), token=token)
                model["draft_text"] = "The current return policy needs to be confirmed for your request."
                job = {"id": 708, "payload": json.dumps({"ticket_id": 708, "message_id": "synthetic",
                        "message_text": message})}
                orchestrator._classification_cache.clear()
                with patch.object(orchestrator, "process_ticket_with_hermes", return_value=model), \
                        patch.object(orchestrator, "_save_result_to_webhook") as save:
                    await orchestrator.process_customer_message(job)
                saved = save.call_args.kwargs
                result = saved["hermes_result"]
                self.assertEqual(result["priority"], expected_priority)
                self.assertEqual(result["notify_owner"], notify)
                self.assertEqual(result["generation_state"], "needs_review")
                self.assertEqual(result["missing_facts"], verdict["missing_facts"])
                self.assertEqual(result["staff_next_step"], verdict["staff_next_step"])
                self.assertEqual(saved["draft_text"].startswith(SENSITIVE_DRAFT_PREFIX), notify)
                self.assertIn("current return policy", saved["draft_text"].lower())
                self.assertNotIn("Thanks for reaching out", saved["draft_text"])
                self.assertFalse(result["gorgias_priority_set"])
                self.assertFalse(result["note_posted"])
