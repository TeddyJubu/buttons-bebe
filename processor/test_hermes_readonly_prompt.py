from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path


PROCESSOR_DIR = Path(__file__).resolve().parent
WEBHOOK_SRC = PROCESSOR_DIR.parent / "webhook" / "src"
sys.path[:0] = [str(PROCESSOR_DIR), str(WEBHOOK_SRC)]

from hermes_runner import (  # noqa: E402
    _build_prompt,
    _parse_json_result,
    draft_for_console,
    process_ticket_with_hermes,
)
from classifier import classify  # noqa: E402


class HermesReadOnlyPromptTests(unittest.TestCase):
    def test_prompt_and_runner_expose_no_write_toggle(self) -> None:
        prompt = _build_prompt(
            ticket_id=12345,
            message_text="Please refund order 123456",
            ticket_subject="Refund request",
            customer_email="customer@example.com",
            intents=["refund"],
        )

        self.assertIn("<DRAFT>", prompt)
        self.assertIn("READ-ONLY", prompt)
        self.assertIn("note_posted=false", prompt)
        self.assertIn("gorgias_priority_set=false", prompt)
        self.assertNotIn("curl PUT", prompt)
        self.assertNotIn("curl POST", prompt)
        self.assertNotIn("Post the draft as an internal note", prompt)
        self.assertNotIn("get_order", prompt)
        self.assertIn("get_returns_for_order", prompt)
        self.assertIn("get_customer", prompt)
        self.assertNotIn("gorgias_writes_enabled", inspect.signature(_build_prompt).parameters)
        self.assertNotIn(
            "gorgias_writes_enabled",
            inspect.signature(process_ticket_with_hermes).parameters,
        )

    def test_model_cannot_claim_read_only_writes_happened(self) -> None:
        parsed = _parse_json_result(
            'JSON_RESULT: {"priority":"high","reason":"test",'
            '"action":"sensitive_draft","notify_owner":true,'
            '"gorgias_priority_set":true,"note_posted":true}'
        )
        self.assertFalse(parsed["gorgias_priority_set"])
        self.assertFalse(parsed["note_posted"])

    def test_failed_generation_never_echoes_customer_message_as_draft(self) -> None:
        customer_message = "Where is my order?"
        fallback = _parse_json_result("Hermes did not return JSON")
        draft = draft_for_console(fallback)
        self.assertTrue(draft.startswith("[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]"))
        self.assertIn("reviewing your request", draft)
        self.assertNotEqual(
            draft, customer_message
        )
        self.assertEqual(
            draft_for_console({"draft_text": "  A real generated draft.  "}),
            "A real generated draft.",
        )

    def test_documented_high_risk_topics_are_sensitive(self) -> None:
        for message in (
            "Can you make a final sale exception?",
            "Please change my shipping address",
            "I need to cancel my order",
            "My package was stolen",
        ):
            with self.subTest(message=message):
                result = classify({"ticket_id": 1, "message_text": message})
                self.assertTrue(result["sensitive"])
                self.assertTrue(result["should_notify_owner"])


if __name__ == "__main__":
    unittest.main()
