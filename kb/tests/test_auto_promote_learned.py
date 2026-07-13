from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
KB_ROOT = REPO_ROOT / ("kb" if (REPO_ROOT / "kb").is_dir() else "KB")
sys.path.insert(0, str(REPO_ROOT))

from feedback import config, pii  # noqa: E402
from webhook.src.bb_webhook import learning  # noqa: E402


def _load_promoter():
    path = KB_ROOT / "scripts" / "auto_promote_learned.py"
    spec = importlib.util.spec_from_file_location("auto_promote_learned_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


auto_promote_learned = _load_promoter()


class LearningPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.learned = self.root / "learned"
        self.tickets = self.root / "tickets"
        self.archive = self.root / "_archive_learned"

        self.config_patches = [
            patch.object(config, "LEARNED_DIR", self.learned),
            patch.object(config, "TICKETS_DIR", self.tickets),
            patch.object(config, "ARCHIVE_DIR", self.archive),
            patch.object(learning, "LEARNED_DIR", self.learned),
            patch.object(learning, "LEDGER", self.learned / "_ledger.json"),
        ]
        for item in self.config_patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.config_patches):
            item.stop()
        self.tmp.cleanup()

    def test_two_same_second_actions_for_one_ticket_survive_capture_and_promotion(self) -> None:
        with patch.object(learning, "_now", return_value="2026-07-14T10-00-00Z"):
            self.assertTrue(
                learning.record_lesson(
                    "note",
                    260291615,
                    "Hi, this is Jane Doe. My order is #123456.",
                    "Initial draft",
                    "Internal note from Jane Doe",
                    customer_name="Jane Doe",
                )
            )
            self.assertTrue(
                learning.record_lesson(
                    "sent",
                    260291615,
                    "Hi, this is Jane Doe. My order is #123456.",
                    "Initial draft",
                    "Final reply sent to Jane Doe",
                    customer_name="Jane Doe",
                )
            )

        lessons = sorted(self.learned.glob("lesson-*.md"))
        self.assertEqual(len(lessons), 2)

        for lesson in lessons:
            self.assertTrue(auto_promote_learned.promote_one(lesson))

        exemplars = sorted(self.tickets.glob("exemplar-learned-*.md"))
        self.assertEqual(len(exemplars), 2)
        combined = "\n".join(path.read_text(encoding="utf-8") for path in exemplars)
        self.assertIn("kind: note", combined)
        self.assertIn("kind: sent", combined)
        self.assertIn("Internal note", combined)
        self.assertIn("Final reply sent", combined)
        self.assertNotIn("Jane", combined)
        self.assertNotIn("Doe", combined)
        self.assertNotIn("123456", combined)
        self.assertNotIn("260291615", combined)
        self.assertTrue(all("260291615" not in path.name for path in exemplars))
        self.assertEqual(len(list(self.archive.glob("lesson-*.md"))), 2)

    def test_concurrent_actions_do_not_lose_ledger_totals(self) -> None:
        actions = [("sent", index % 2 == 0) for index in range(40)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda item: learning._bump_ledger(*item), actions))

        stats = learning.ledger()
        self.assertEqual(stats["total"], 40)
        self.assertEqual(stats["sent"], 40)
        self.assertEqual(stats["edited"], 20)
        self.assertEqual(stats["unchanged"], 20)


class KnownValueMaskingTests(unittest.TestCase):
    def test_masks_greeting_name_when_legacy_lesson_has_no_customer_name(self) -> None:
        masked = pii.mask_with_known_values("Hi Marjana, sure thing!")
        self.assertEqual(masked, "Hi [name], sure thing!")

    def test_masks_known_customer_name_in_latin_and_hebrew_scripts(self) -> None:
        text = "Jane Doe spoke with רות כהן about PO Box 42 and card 4111 1111 1111 1111."
        masked = pii.mask_with_known_values(
            text,
            customer_names=["Jane Doe", "רות כהן"],
        )
        self.assertNotIn("Jane", masked)
        self.assertNotIn("Doe", masked)
        self.assertNotIn("רות", masked)
        self.assertNotIn("כהן", masked)
        self.assertNotIn("PO Box 42", masked)
        self.assertNotIn("4111 1111 1111 1111", masked)


if __name__ == "__main__":
    unittest.main()
