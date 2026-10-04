"""Behavioral regressions for sensitive priority floors and owner alerts."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "processor"), str(ROOT), str(ROOT / "webhook" / "src")]

from classifier import classify
import orchestrator


CARE_QUESTIONS = (
    "I washed the outfit once. How do I check whether it shrank?",
    "I washed the outfit once and wondered if it shrank. Is that possible?",
    "I washed the outfit once and am not sure whether it shrank.",
    "I washed the outfit once and it might have shrunk. How can I check?",
    "I washed the outfit once and it maybe shrank. How can I check?",
    "I washed the outfit once and perhaps it shrank. How can I check?",
    "My outfit possibly shrank after washing. How can I check?",
    "I washed the outfit once; how do I check whether it shrank?",
    "I washed the outfit once? Could it have shrunk?",
)


class QualityAlertRuleTests(unittest.IsolatedAsyncioTestCase):
    async def _replay(self, *, subject: str, message: str, model_result: dict):
        payload = {
            "ticket_id": 123,
            "message_id": "synthetic-quality-alert",
            "ticket_subject": subject,
            "message_text": message,
            "customer_email": "synthetic@example.invalid",
        }
        job = {"id": 1, "ticket_id": 123, "payload": json.dumps(payload)}
        with (
            patch.object(orchestrator, "process_ticket_with_hermes", return_value=dict(model_result)),
            patch.object(orchestrator, "_save_result_to_webhook") as save,
            patch.object(orchestrator, "claim_owner_alert", new_callable=AsyncMock, return_value=True) as claim,
            patch.object(orchestrator, "send_whatsapp", return_value=True) as transport,
            patch.object(orchestrator, "finish_owner_alert", new_callable=AsyncMock) as finish,
        ):
            outcome = await orchestrator.process_customer_message(job)
            saved = dict(save.call_args.kwargs["hermes_result"])
            if saved["notify_owner"]:
                await orchestrator._notify_owner_once(job, saved, Path("synthetic-unused.sqlite3"))
            return outcome, saved, claim, transport, finish

    def test_reported_post_wash_shrinkage_is_sensitive_and_owner_notified(self):
        reports = (
            "I washed the outfit once and it shrank. Can I return it?",
            "My outfit shrank after the first wash. Can I return it?",
            "My pajamas shrank when I washed them.",
            "My pajamas shrank after I washed them.",
            "My pajamas shrank after being washed.",
            "My pajamas shrank when washed.",
            "I washed this outfit once and it shrank, could I return it?",
            "I washed this outfit once, and it shrank, could I return it?",
            "My pajamas shrank after washing, but they haven’t shrunk further.",
            "I washed the outfit and it shrank, but it hasn’t shrunk further.",
            "Could you help me because my pajamas shrank after washing?",
            "Would you help me because my pajamas shrank after washing?",
            "I washed the outfit once. It shrank. Can you help?",
            "I washed the outfit once; it has shrunk. Can you help?",
            "I washed the pajamas once. They have shrunk. Can you help?",
            "I washed the outfit once and it shrank. How can I check the care instructions?",
            "My pajamas shrank after washing, and I wondered if I could return them.",
            "I don't know if I washed it properly, but it shrank.",
            "If it helps: my pajamas shrank after washing.",
            "My hoodie shrank after washing.",
            "My jeans shrank after the wash.",
            "I've washed the outfit and it shrunk.",
            "We have washed the shirts and they shrank.",
            "I didn't like how my dress shrank after washing.",
        )
        for message in reports:
            with self.subTest(message=message):
                result = classify({"ticket_subject": "Washed item return", "message_text": message})
                self.assertEqual(result["priority"], "high")
                self.assertTrue(result["sensitive"])
                self.assertTrue(result["should_notify_owner"])
                self.assertIn("reported shrinkage after washing", result["reason"])

    def test_washing_questions_and_unreported_or_negated_damage_stay_routine(self):
        routine_messages = CARE_QUESTIONS + (
            "Will this outfit shrink when washed?",
            "A friend told me this brand's onesies shrank after washing. Is that true?",
            "The care guide says some shirts shrank after washing. How should I wash mine?",
            "For a hypothetical, a fabric shrank after washing; does that happen here?",
            "Is shrinkage after washing normal?",
            "Does shrinkage after washing qualify for a return?",
            "What if the outfit shrank after washing?",
            "If the outfit shrank after washing, could I return it?",
            "How should I wash this outfit?",
            "Can I return an outfit after washing it once?",
            "I washed it once, but it hasn't shrunk.",
            "It never shrank after washing.",
            "If I washed this outfit once and it shrank, could I return it?",
            "Hypothetically I washed this outfit once and it shrank; could I return it?",
            "My pajamas might have shrunk after washing.",
            "My pajamas have not shrunk after washing.",
            "I washed it, but it hasn't really shrunk.",
            "I washed it, but it hasn’t noticeably shrunk.",
            "Would my dress have shrunk after the wash?",
            "Maybe my dress shrunk after the wash.",
            "I don't think my dress shrunk after the wash.",
            "I do not think my dress has shrunk after the wash.",
            "If I washed the outfit but it shrank, would I qualify?",
            "I washed the dog and my shirt shrunk.",
        )
        for message in routine_messages:
            with self.subTest(message=message):
                result = classify({"message_text": message})
                self.assertEqual(result["priority"], "normal")
                self.assertFalse(result["sensitive"])
                self.assertFalse(result["should_notify_owner"])

    async def test_care_questions_remain_routine_without_an_owner_alert(self):
        model_result = {
            "priority": "normal",
            "action": "no_kb_match",
            "reason": "A care question without a reported defect.",
            "notify_owner": False,
            "draft_text": "The product-specific way to check for a size change is not confirmed.",
            "generation_state": "needs_review",
            "review_required": True,
            "missing_facts": ["Product-specific care and measurement guidance"],
            "staff_next_step": "Verify the exact garment's care and measurement guidance.",
        }
        for message in CARE_QUESTIONS:
            with self.subTest(message=message):
                _, saved, claim, transport, finish = await self._replay(
                    subject="Care question", message=message, model_result=model_result
                )
                self.assertEqual(saved["priority"], "normal")
                self.assertEqual(saved["action"], "no_kb_match")
                self.assertFalse(saved["notify_owner"])
                self.assertEqual(saved["generation_state"], "needs_review")
                self.assertEqual(saved["staff_next_step"], model_result["staff_next_step"])
                self.assertEqual(claim.await_count, 0)
                self.assertEqual(transport.call_count, 0)
                self.assertEqual(finish.await_count, 0)

    def test_received_material_color_difference_from_photos_is_sensitive(self):
        reports = (
            "The denim I got is way darker than the light denim in your photos.",
            "I wonder why the denim I got is way darker than your photos.",
            "If you have any questions, let me know. The denim I got is way darker than the photos.",
            "I got the denim and it's way darker than your photos.",
            "Yesterday I received it and it is way darker than your photos.",
            "I got the denim, and it is way darker than the photos.",
            "Yesterday I received the denim and it is way darker than your photos.",
            "The denim I got, however, is way darker than your photos.",
            "I got the denim, but the color is way darker than your photos.",
            "The item that arrived is way darker than your photos.",
            "The dress was delivered and it is much lighter than the listing.",
            "I received the dress. It is much darker than the photos.",
            "I received the dress and I think it is much darker than the photos.",
            "I received the dress and honestly it is much darker than the photos.",
        )
        for message in reports:
            with self.subTest(message=message):
                result = classify({"ticket_subject": "Doesn't look like photo", "message_text": message})
                self.assertEqual(result["priority"], "high")
                self.assertTrue(result["sensitive"])
                self.assertTrue(result["should_notify_owner"])
                self.assertIn("received item materially differs from listing photos", result["reason"])

        routine_messages = (
            "I'm thinking of buying these jeans. Are they darker than the photos?",
            "Will the denim look darker after washing?",
            "The care guide says the denim can look much darker than product photos after washing.",
            "Are the product photos darker than this shade in real life?",
            "A friend told me the denim was way darker than the photos. Is that true?",
            "I got the bodysuit last week. The denim is way darker than the photos.",
            "I got the bodysuit last week; the denim is way darker than the photos.",
            "I got the bodysuit, the denim is way darker than the photos.",
            "If I get the denim, would it be way darker than in the photos?",
            "What if I got the denim and it was way darker than the photos?",
            "I got the denim, but it's not darker than in your photos.",
            "My denim is only slightly darker than the photos.",
            "If I received the dress and I think it is much darker than photos, could I return it?",
        )
        for message in routine_messages:
            with self.subTest(message=message):
                result = classify({"message_text": message})
                self.assertEqual(result["priority"], "normal")
                self.assertFalse(result["sensitive"])
                self.assertFalse(result["should_notify_owner"])

    def test_quoted_defects_do_not_create_fresh_alerts(self):
        quoted = (
            "I washed my outfit and it shrank.",
            "I received the dress and it is much darker than the photos.",
        )
        for current in ("Thanks!", "How should I wash this outfit?"):
            for old in quoted:
                with self.subTest(current=current, old=old):
                    result = classify({"message_text": current + "\n\nOn Oct 2, 2026 Bob wrote:\n> " + old})
                    self.assertEqual(result["priority"], "normal")
                    self.assertFalse(result["sensitive"])
                    self.assertFalse(result["should_notify_owner"])
                    self.assertEqual(result["should_draft"], current != "Thanks!")
        for old in quoted:
            result = classify({"message_text": "> " + old})
            self.assertEqual(result["priority"], "normal")
            self.assertFalse(result["sensitive"])
            self.assertFalse(result["should_draft"])
            self.assertFalse(result["should_notify_owner"])

    async def test_saved_e07_color_mismatch_is_raised_and_alert_attempt_is_mocked(self):
        message = "The denim I got is way darker than the light denim in your photos."
        model_result = {
            "priority": "normal",
            "reason": "The customer has an ordinary product-color concern, with no reported defect or other sensitive issue.",
            "action": "no_kb_match",
            "notify_owner": False,
            "review_required": True,
            "staff_next_step": "Identify the denim product and verify its listed color and product-photo details against the customer's report.",
            "missing_facts": [
                "Product identity and whether the photographed color accurately represents the item"
            ],
            "gorgias_priority_set": False,
            "note_posted": False,
            "draft_text": "Hi there, colors can look different in photos, but I don’t have enough information to confirm why this denim appears darker. Could you share the product link or style name so we can identify it?",
            "generation_state": "needs_review",
        }
        _, saved, claim, transport, finish = await self._replay(
            subject="Doesn't look like photo", message=message, model_result=model_result
        )
        self.assertEqual(saved["priority"], "high")
        self.assertEqual(saved["action"], "sensitive_draft")
        self.assertTrue(saved["notify_owner"])
        self.assertTrue(saved["review_required"])
        self.assertEqual(saved["staff_next_step"], model_result["staff_next_step"])
        self.assertEqual(saved["missing_facts"], model_result["missing_facts"])
        self.assertEqual(saved["generation_state"], "needs_review")
        self.assertIn("share the product link or style name", saved["draft_text"])
        self.assertNotIn("eligible for return", saved["draft_text"].lower())
        self.assertEqual(claim.await_count, 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(transport.call_args.kwargs["max_retries"], 0)
        self.assertEqual(finish.await_count, 1)

    async def test_saved_low_final_sale_verdict_is_raised_before_persistence_and_alert(self):
        message = "I know it was final sale but it doesn't fit — can you make an exception and take it back?"
        model_result = {
            "priority": "low",
            "action": "no_kb_match",
            "reason": "A final-sale exception request.",
            "notify_owner": False,
            "draft_text": "I understand the item does not fit. Could you share your order number?",
            "generation_state": "ready",
            "review_required": False,
            "missing_facts": [],
            "staff_next_step": "",
        }
        _, saved, claim, transport, finish = await self._replay(
            subject="Final sale exception", message=message, model_result=model_result
        )
        self.assertEqual(saved["priority"], "high")
        self.assertEqual(saved["action"], "sensitive_draft")
        self.assertTrue(saved["notify_owner"])
        self.assertIn("[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]", saved["draft_text"])
        self.assertEqual(saved["generation_state"], "ready")
        self.assertEqual(claim.await_count, 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(transport.call_args.kwargs["max_retries"], 0)
        self.assertEqual(finish.await_count, 1)

    async def test_saved_normal_shrinkage_answer_becomes_high_without_claiming_return_eligibility(self):
        message = "I washed the outfit once and it shrank — can I return it?"
        draft = "Could you share your order number so the team can check whether it is eligible for return?"
        model_result = {
            "priority": "normal",
            "action": "drafted",
            "reason": "This is a return eligibility question about a washed item.",
            "notify_owner": False,
            "draft_text": draft,
            "generation_state": "ready",
            "review_required": False,
            "missing_facts": [],
            "staff_next_step": "",
        }
        _, saved, claim, transport, finish = await self._replay(
            subject="Washed item return", message=message, model_result=model_result
        )
        self.assertEqual(saved["priority"], "high")
        self.assertEqual(saved["action"], "sensitive_draft")
        self.assertTrue(saved["notify_owner"])
        self.assertIn("[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]", saved["draft_text"])
        self.assertIn("check whether it is eligible for return", saved["draft_text"])
        self.assertEqual(saved["generation_state"], "ready")
        self.assertFalse(saved["review_required"])
        self.assertEqual(claim.await_count, 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(finish.await_count, 1)

    async def test_wrong_customer_package_stays_critical_and_identifier_clarification_ready(self):
        message = "This box has another customer's name and items in it, not mine."
        draft = "I am sorry you received another customer's order. Could you send your order number so we can identify the shipment?"
        model_result = {
            "priority": "high",
            "action": "sensitive_draft",
            "reason": "The customer received another customer's order.",
            "notify_owner": True,
            "draft_text": "[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]\n" + draft,
            "generation_state": "ready",
            "review_required": False,
            "missing_facts": [],
            "staff_next_step": "",
        }
        _, saved, claim, transport, finish = await self._replay(
            subject="Someone else's order", message=message, model_result=model_result
        )
        self.assertEqual(saved["priority"], "critical")
        self.assertEqual(saved["action"], "sensitive_draft")
        self.assertTrue(saved["notify_owner"])
        self.assertFalse(saved["review_required"])
        self.assertEqual(saved["generation_state"], "ready")
        self.assertIn("Could you send your order number", saved["draft_text"])
        self.assertEqual(claim.await_count, 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(finish.await_count, 1)

    def test_nonurgent_delivery_method_changes_stay_normal_but_explicit_urgency_alerts(self):
        messages = (
            "I ordered for shipping but I'd rather pick it up — can you switch it? Order #10360.",
            "I set my order to pickup but now I need it shipped. Order #10361.",
        )
        for message in messages:
            with self.subTest(message=message):
                result = classify({"message_text": message})
                self.assertEqual(result["priority"], "normal")
                self.assertFalse(result["should_notify_owner"])

        urgent = classify({
            "message_text": "I urgently need this order's shipping method changed before dispatch.",
        })
        self.assertEqual(urgent["priority"], "high")
        self.assertTrue(urgent["should_notify_owner"])

    async def test_urgent_delivery_change_raises_normal_model_result_and_alerts(self):
        message = "I urgently need this order's shipping method changed before dispatch."
        model_result = {
            "priority": "normal",
            "action": "drafted",
            "reason": "The customer asks to change the delivery method.",
            "notify_owner": False,
            "draft_text": "I can see the delivery method change is not confirmed yet.",
            "generation_state": "ready",
            "review_required": False,
            "missing_facts": [],
            "staff_next_step": "",
        }
        _, saved, claim, transport, finish = await self._replay(
            subject="Urgent delivery change", message=message, model_result=model_result
        )
        self.assertEqual(saved["priority"], "high")
        self.assertEqual(saved["action"], "sensitive_draft")
        self.assertTrue(saved["notify_owner"])
        self.assertIn("[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]", saved["draft_text"])
        self.assertEqual(saved["generation_state"], "ready")
        self.assertEqual(claim.await_count, 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(finish.await_count, 1)


if __name__ == "__main__":
    unittest.main()
