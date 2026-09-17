"""Source-shape guards: the retired poll-based feedback path stays dead."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROCESSOR_DIR = Path(__file__).resolve().parent


class LegacyFeedbackRetirementTests(unittest.TestCase):
    def test_retired_hermes_feedback_helper_is_absent(self) -> None:
        source = (PROCESSOR_DIR / "hermes_runner.py").read_text(encoding="utf-8")
        self.assertNotIn("process_agent_reply_with_hermes", source)
        self.assertNotIn("FEEDBACK_LEGACY_OPT_IN", source)
        self.assertNotIn("Load credentials", source)

    def test_orchestrator_does_not_import_legacy_helper(self) -> None:
        source = (PROCESSOR_DIR / "orchestrator.py").read_text(encoding="utf-8")
        preamble = source.split("# ── Agent message processing", 1)[0]
        self.assertNotIn("process_agent_reply_with_hermes", preamble)

    def test_retired_stub_files_stay_absent(self) -> None:
        # The stubs were deleted (Wave 4, report 03-7): their absence is the
        # tripwire now. An ImportError on a forbidden path is as loud as the
        # guarded RuntimeError it replaced.
        self.assertFalse((PROCESSOR_DIR / "feedback_collector.py").exists())
        self.assertFalse((PROCESSOR_DIR / "gorgias_writer.py").exists())


if __name__ == "__main__":
    unittest.main()
