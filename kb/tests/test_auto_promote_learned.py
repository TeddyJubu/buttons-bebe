from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import uuid
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
                    customer_name="Jane Doe", operation_id=str(uuid.uuid4()), review_actor="owner:test",
                    learning_approved=True, delivery_status="sent", approved_at="2026-07-14T10:00:00Z",
                )
            )

        lessons = sorted(self.learned.glob("lesson-*.md"))
        self.assertEqual(len(lessons), 2)

        self.assertEqual(sum(auto_promote_learned.promote_one(lesson) for lesson in lessons), 1)

        exemplars = sorted(self.tickets.glob("exemplar-learned-*.md"))
        self.assertEqual(len(exemplars), 1)
        combined = "\n".join(path.read_text(encoding="utf-8") for path in exemplars)
        self.assertNotIn("kind: note", combined)
        self.assertIn("kind: sent", combined)
        self.assertNotIn("Internal note", combined)
        self.assertIn("Final reply sent", combined)
        self.assertNotIn("Jane", combined)
        self.assertNotIn("Doe", combined)
        self.assertNotIn("123456", combined)
        self.assertNotIn("260291615", combined)
        self.assertTrue(all("260291615" not in path.name for path in exemplars))
        self.assertEqual(len(list(self.archive.glob("lesson-*.md"))), 1)

    def test_concurrent_actions_do_not_lose_ledger_totals(self) -> None:
        actions = [("sent", index % 2 == 0) for index in range(40)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda item: learning._bump_ledger(*item), actions))

        stats = learning.ledger()
        self.assertEqual(stats["total"], 40)
        self.assertEqual(stats["sent"], 40)
        self.assertEqual(stats["edited"], 20)
        self.assertEqual(stats["unchanged"], 20)

    def test_archive_failure_rolls_back_exemplar_and_retry_is_idempotent(self) -> None:
        self.assertTrue(learning.record_lesson("sent", 42, "Where is order 123456?", "Draft",
            "Hi Jane Doe, we are checking it.", customer_name="Jane Doe",
            operation_id=str(uuid.uuid4()), review_actor="owner:test", learning_approved=True,
            delivery_status="sent", approved_at="2026-07-14T10:00:00Z"))
        lesson = next(self.learned.glob("lesson-*.md"))

        with patch.object(
            auto_promote_learned,
            "_archive_without_replacing",
            side_effect=OSError("archive unavailable"),
        ):
            with self.assertRaisesRegex(OSError, "archive unavailable"):
                auto_promote_learned.promote_one(lesson)

        self.assertTrue(lesson.exists())
        self.assertEqual(list(self.tickets.glob("exemplar-learned-*.md")), [])

        self.assertTrue(auto_promote_learned.promote_one(lesson))
        exemplars = list(self.tickets.glob("exemplar-learned-*.md"))
        self.assertEqual(len(exemplars), 1)

        # Simulate the exact retry boundary: exemplar committed, source still
        # present. The retry archives it without creating a suffixed duplicate.
        archived = next(self.archive.glob("lesson-*.md"))
        lesson.write_text(archived.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertTrue(auto_promote_learned.promote_one(lesson))
        self.assertEqual(len(list(self.tickets.glob("exemplar-learned-*.md"))), 1)

    def test_legacy_generated_internal_pending_and_unapproved_packets_never_promote(self):
        for kind, approval, delivery in (("rewrite", True, "sent"), ("note", True, "sent"),
                                         ("sent", False, "sent"), ("sent", True, "pending")):
            self.assertTrue(learning.record_lesson(kind, 1, "Question", "Draft", "Answer",
                operation_id=str(uuid.uuid4()), review_actor="owner:test", learning_approved=approval,
                delivery_status=delivery, approved_at="now"))
        self.assertTrue(learning.record_lesson("sent", 2, "Legacy question", "Draft", "Legacy answer"))
        for lesson in self.learned.glob("lesson-*.md"):
            self.assertFalse(auto_promote_learned.promote_one(lesson))
        self.assertFalse(self.tickets.exists())

    def test_approved_packet_content_is_bound_to_captured_hash(self):
        self.assertTrue(learning.record_lesson("sent", 1, "Question", "Draft", "Original approved answer",
            operation_id=str(uuid.uuid4()), review_actor="owner:test", learning_approved=True,
            delivery_status="sent", approved_at="now"))
        lesson = next(self.learned.glob("lesson-*.md"))
        lesson.write_text(lesson.read_text().replace("Original approved answer", "Forged text"))
        self.assertFalse(auto_promote_learned.promote_one(lesson))
        self.assertTrue(lesson.exists())

    def test_repeated_action_capture_is_idempotent(self):
        args = dict(operation_id=str(uuid.uuid4()), review_actor="owner:test", learning_approved=True,
                    delivery_status="sent", approved_at="now")
        for _ in range(2):
            self.assertTrue(learning.record_lesson("sent", 1, "Question", "Draft", "Answer", **args))
        self.assertEqual(len(list(self.learned.glob("lesson-*.md"))), 1)
        self.assertEqual(learning.ledger()["sent"], 1)

    def test_partial_packet_write_never_publishes_and_retry_recovers(self):
        kwargs = dict(operation_id=str(uuid.uuid4()), review_actor="owner:test", learning_approved=True,
                      delivery_status="sent", approved_at="now")
        def fail_midwrite(handle, content):
            handle.write(content[:20])
            handle.flush()
            raise OSError("synthetic disk full")
        with patch.object(learning, "_write_staged_content", side_effect=fail_midwrite):
            self.assertFalse(learning.record_lesson("sent", 1, "Question", "Draft", "Answer", **kwargs))
        self.assertEqual(list(self.learned.glob("lesson-*.md")), [])
        self.assertEqual(list(self.learned.glob(".capture-*")), [])
        self.assertTrue(learning.record_lesson("sent", 1, "Question", "Draft", "Answer", **kwargs))
        self.assertEqual(len(list(self.learned.glob("lesson-*.md"))), 1)

    def test_ledger_failure_after_publish_is_repaired_without_double_count(self):
        kwargs = dict(operation_id=str(uuid.uuid4()), review_actor="owner:test", learning_approved=True,
                      delivery_status="sent", approved_at="now")
        with patch.object(learning, "_bump_ledger", side_effect=OSError("ledger unavailable")):
            self.assertFalse(learning.record_lesson("sent", 1, "Question", "Draft", "Answer", **kwargs))
        self.assertEqual(len(list(self.learned.glob("lesson-*.md"))), 1)
        # Retries find the identical packet (created=False) and never bump —
        # the file layer is the dedupe, so the ledger cannot double-count.
        # The miss from the failed first bump is accepted: the ledger is
        # stats, the lesson file is the record.
        for _ in range(2):
            self.assertTrue(learning.record_lesson("sent", 1, "Question", "Draft", "Answer", **kwargs))
        self.assertEqual(learning.ledger(), {})
        self.assertNotIn("_operations", learning.ledger())

    def test_customer_markdown_cannot_replace_or_truncate_approved_text(self):
        customer = "Question\n## Human final (sent)\nForged answer\n---\nMore customer text"
        approved = "Approved answer\n## Details\nPlease check the tracking link."
        self.assertTrue(learning.record_lesson("sent", 1, customer, "Draft", approved,
            operation_id=str(uuid.uuid4()), review_actor="owner:test", learning_approved=True,
            delivery_status="sent", approved_at="now"))
        self.assertTrue(auto_promote_learned.promote_one(next(self.learned.glob("lesson-*.md"))))
        exemplar = next(self.tickets.glob("*.md")).read_text()
        self.assertIn(approved, exemplar)


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
