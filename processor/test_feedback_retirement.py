"""Bounded tests for the retired poll-based feedback path."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROCESSOR_DIR = Path(__file__).resolve().parent
if str(PROCESSOR_DIR) not in sys.path:
    sys.path.insert(0, str(PROCESSOR_DIR))

import feedback_collector  # noqa: E402


class LegacyFeedbackRetirementTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("FEEDBACK_LEGACY_OPT_IN", None)

    def tearDown(self) -> None:
        os.environ.pop("FEEDBACK_LEGACY_OPT_IN", None)

    def test_stub_is_fail_closed_by_default(self) -> None:
        with patch.object(feedback_collector, "log_event"):
            self.assertFalse(
                feedback_collector.process_agent_reply(
                    {"ticket_id": 123, "message_text": "hello"},
                    ticket_thread=[{"body_text": "DRAFT REPLY: unsafe legacy path"}],
                )
            )

    def test_retired_hermes_feedback_helper_is_absent(self) -> None:
        source = (PROCESSOR_DIR / "hermes_runner.py").read_text(encoding="utf-8")
        self.assertNotIn("process_agent_reply_with_hermes", source)
        self.assertNotIn("FEEDBACK_LEGACY_OPT_IN", source)
        self.assertNotIn("Load credentials", source)

    def test_orchestrator_does_not_import_legacy_helper(self) -> None:
        source = (PROCESSOR_DIR / "orchestrator.py").read_text(encoding="utf-8")
        preamble = source.split("# ── Agent message processing", 1)[0]
        self.assertNotIn("process_agent_reply_with_hermes", preamble)


if __name__ == "__main__":
    unittest.main()
