from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path


PROCESSOR_DIR = Path(__file__).resolve().parent
WEBHOOK_SRC = PROCESSOR_DIR.parent / "webhook" / "src"
sys.path[:0] = [str(PROCESSOR_DIR), str(WEBHOOK_SRC)]

from hermes_runner import _build_prompt, process_ticket_with_hermes  # noqa: E402


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
        self.assertNotIn("gorgias_writes_enabled", inspect.signature(_build_prompt).parameters)
        self.assertNotIn(
            "gorgias_writes_enabled",
            inspect.signature(process_ticket_with_hermes).parameters,
        )


if __name__ == "__main__":
    unittest.main()
