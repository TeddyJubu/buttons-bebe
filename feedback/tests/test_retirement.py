"""Tests proving the superseded poller is inert without explicit opt-in."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Make both `python feedback/tests/test_retirement.py` and module discovery from
# the repository root resolve the package consistently.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from feedback import collector, config


class LegacyPollerRetirementTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop(config.LEGACY_OPT_IN_ENV, None)

    def tearDown(self) -> None:
        os.environ.pop(config.LEGACY_OPT_IN_ENV, None)

    def test_run_poll_does_not_call_gorgias_by_default(self) -> None:
        with patch.object(collector.gorgias_read, "list_tickets_updated_since") as list_tickets:
            result = collector.run_poll(limit=1)
        self.assertTrue(result["disabled"])
        self.assertEqual(result["reason"], "legacy_feedback_disabled")
        list_tickets.assert_not_called()


if __name__ == "__main__":
    unittest.main()
