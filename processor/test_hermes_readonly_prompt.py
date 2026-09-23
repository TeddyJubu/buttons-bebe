from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROCESSOR_DIR = Path(__file__).resolve().parent
WEBHOOK_SRC = PROCESSOR_DIR.parent / "webhook" / "src"
sys.path[:0] = [str(PROCESSOR_DIR), str(WEBHOOK_SRC)]

from hermes_runner import draft_for_console, process_ticket_with_hermes  # noqa: E402
from hermes_runner import extract, prompt, runner  # noqa: E402
from classifier import classify  # noqa: E402


TOKEN = "0123456789abcdef"


def tokenized_verdict(**fields: object) -> str:
    payload = {
        "priority": "high",
        "reason": "test",
        "action": "sensitive_draft",
        "notify_owner": True,
        "gorgias_priority_set": False,
        "note_posted": False,
        **fields,
    }
    return f"JSON_RESULT[{TOKEN}]: " + json.dumps(payload, separators=(",", ":"))


class HermesReadOnlyPromptTests(unittest.TestCase):
    def test_prompt_and_runner_expose_no_write_toggle(self) -> None:
        built_prompt = prompt._build_prompt(
            ticket_id=12345,
            message_text="Please refund order 123456",
            ticket_subject="Refund request",
            customer_email="customer@example.com",
            intents=["refund"],
            token=TOKEN,
        )

        self.assertIn(f"<DRAFT:{TOKEN}>", built_prompt)
        self.assertIn(f"JSON_RESULT[{TOKEN}]", built_prompt)
        self.assertIn("READ-ONLY", built_prompt)
        self.assertIn("note_posted=false", built_prompt)
        self.assertIn("gorgias_priority_set=false", built_prompt)
        self.assertNotIn("curl PUT", built_prompt)
        self.assertNotIn("curl POST", built_prompt)
        self.assertNotIn("Post the draft as an internal note", built_prompt)
        self.assertNotIn("get_order", built_prompt)
        self.assertIn("get_returns_for_order", built_prompt)
        self.assertIn("get_customer", built_prompt)
        self.assertIn("Everything between the DRAFT tags must be customer-facing prose only", built_prompt)
        self.assertIn("Never claim that a human or the store has already or will definitely", built_prompt)
        self.assertIn("4 sentences for normal tickets, 5 for sensitive", built_prompt)
        # Audit fixes: greeting/name discipline, can-do-first, direct
        # questions, signoff+links, paragraph break, 24-48h restraint,
        # refund-topic naming, spam/thanks routing.
        for phrase in (
            "Take the name ONLY from the newest customer-authored message",
            "Never open a draft with 'We don't",
            "end with a direct question ending in '?'",
            "Close with a brief sign-off line",
            "Put the request or next step on its own line",
            "only when the order could still be inside it",
            "Naming the topic is REQUIRED, not forbidden",
            "Never prefix it [SENSITIVE]",
            "Never write 'from the information available'",
        ):
            self.assertIn(phrase, built_prompt)
        self.assertNotIn("gorgias_writes_enabled", inspect.signature(prompt._build_prompt).parameters)
        self.assertNotIn(
            "gorgias_writes_enabled",
            inspect.signature(process_ticket_with_hermes).parameters,
        )

    def test_model_cannot_claim_read_only_writes_happened(self) -> None:
        parsed = extract._parse_json_result(
            tokenized_verdict(gorgias_priority_set=True, note_posted=True),
            token=TOKEN,
        )
        self.assertFalse(parsed["gorgias_priority_set"])
        self.assertFalse(parsed["note_posted"])

    def test_failed_generation_never_echoes_customer_message_as_draft(self) -> None:
        customer_message = "Where is my order?"
        fallback = extract._parse_json_result(
            "Hermes did not return JSON", token=TOKEN
        )
        draft = draft_for_console(fallback)
        self.assertEqual(fallback["action"], "sensitive_draft")
        self.assertTrue(fallback["notify_owner"])
        self.assertTrue(fallback["no_draft"])
        self.assertEqual(draft, "")
        self.assertNotEqual(
            draft, customer_message
        )
        self.assertEqual(
            draft_for_console({"draft_text": "  A real generated draft.  ", "priority": "normal", "action": "drafted"}),
            "A real generated draft.",
        )
        self.assertTrue(
            draft_for_console({
                "action": "sensitive_draft",
                "draft_text": "Hi! We're reviewing this for you.",
            }).startswith("[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]")
        )
        self.assertTrue(
            draft_for_console({}).startswith(
                "[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]"
            )
        )

    def test_valid_json_without_draft_fails_closed_to_non_sendable_result(self) -> None:
        with patch.object(
            runner,
            "get_settings",
            return_value=SimpleNamespace(job_timeout=30, hermes_toolsets="buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias"),
        ), patch.object(
            runner,
            "run_bounded",
            return_value=SimpleNamespace(
                returncode=0,
                stderr="",
                stdout=tokenized_verdict(
                    priority="normal", action="drafted", notify_owner=False
                ),
            ),
        ):
            result = process_ticket_with_hermes(
                ticket_id=123,
                message_text="Where is my order?",
                ticket_subject="Order status",
                customer_email="customer@example.com",
                intents=["shipping"],
            )

        self.assertEqual(result["priority"], "high")
        self.assertEqual(result["action"], "sensitive_draft")
        self.assertTrue(result["notify_owner"])
        # Round 8: the canned "[SENSITIVE — REVIEW…] we're reviewing your
        # request" reply used to be stored here, contradicting the reason on
        # the same card and handing the reviewer something sendable the model
        # never wrote. That draft belongs to the Hermes-CRASHED path (see
        # test_hermes_failure_still_uses_the_reviewable_fallback), where a
        # holding reply is genuinely useful. Hermes ran here, so: no draft.
        self.assertTrue(result["no_draft"])
        self.assertEqual(result["draft_text"], "")
        self.assertIn("no draft", result["reason"].lower())

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
