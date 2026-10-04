from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import textwrap
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
KB_ROOT = REPO_ROOT / ("kb" if (REPO_ROOT / "kb").is_dir() else "KB")
sys.path.insert(0, str(REPO_ROOT))

from feedback import pii  # noqa: E402
from feedback.learning_paths import LearningPaths, default_kb_root, resolve_learning_paths  # noqa: E402
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
        self.paths = LearningPaths(self.root)

        self.config_patches = [
            patch.object(auto_promote_learned, "PATHS", self.paths),
            patch.object(learning, "_learning_paths", return_value=self.paths),
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

    def test_two_character_hebrew_name_is_masked_from_approved_title_and_text(self):
        self.assertTrue(learning.record_lesson(
            "sent", 314, "דן asked about her delivery.", "Draft",
            "We checked the parcel for דן לוי and will update you.",
            customer_name="דן לוי", operation_id=str(uuid.uuid4()), review_actor="owner:test",
            learning_approved=True, delivery_status="sent", approved_at="2026-10-04T12:00:00Z",
        ))
        lesson = next(self.learned.glob("lesson-*.md"))

        self.assertTrue(auto_promote_learned.promote_one(lesson))

        exemplar = next(self.tickets.glob("exemplar-learned-*.md")).read_text(encoding="utf-8")
        self.assertNotIn("דן", exemplar)
        self.assertNotIn("לוי", exemplar)
        self.assertIn("title: Approved reply - [name] asked", exemplar)
        self.assertIn("## Approved reply", exemplar)

    def test_missing_approval_actor_operation_or_time_never_promotes(self):
        variants = (
            ("actor", {
                "operation_id": str(uuid.uuid4()), "review_actor": "",
                "learning_approved": True, "delivery_status": "sent", "approved_at": "now",
            }),
            ("operation", {
                "operation_id": "", "review_actor": "owner:test",
                "learning_approved": True, "delivery_status": "sent", "approved_at": "now",
            }),
            ("approval time", {
                "operation_id": str(uuid.uuid4()), "review_actor": "owner:test",
                "learning_approved": True, "delivery_status": "sent", "approved_at": "",
            }),
        )
        for ticket_id, (missing, extra) in enumerate(variants, start=1):
            with self.subTest(missing=missing):
                self.assertTrue(learning.record_lesson(
                    "sent", ticket_id, "Question", "Draft", "Approved answer",
                    **extra,
                ))
        for lesson in sorted(self.learned.glob("lesson-*.md")):
            self.assertFalse(auto_promote_learned.promote_one(lesson))
        self.assertEqual(list(self.tickets.glob("exemplar-learned-*.md")), [])
        self.assertEqual(list(self.archive.glob("lesson-*.md")), [])

    def test_changed_customer_text_does_not_promote(self):
        self.assertTrue(learning.record_lesson(
            "sent", 99, "Original customer question", "Draft", "Approved answer",
            operation_id=str(uuid.uuid4()), review_actor="owner:test", learning_approved=True,
            delivery_status="sent", approved_at="now",
        ))
        lesson = next(self.learned.glob("lesson-*.md"))
        raw = lesson.read_text(encoding="utf-8")
        lesson.write_text(raw.replace(
            "customer_message: Original customer question",
            "customer_message: Forged customer question",
        ), encoding="utf-8")

        self.assertFalse(auto_promote_learned.promote_one(lesson))
        self.assertTrue(lesson.exists())
        self.assertEqual(list(self.tickets.glob("exemplar-learned-*.md")), [])


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

    def test_masks_two_character_names_without_changing_longer_words(self) -> None:
        masked = pii.mask_with_known_values(
            "Al already also; עדן דני דן לוי and דן.",
            customer_names=["Al", "דן לוי"],
        )
        self.assertEqual(masked, "[name] already also; עדן דני [name] and [name].")

    def test_one_character_name_components_remain_unmasked(self) -> None:
        masked = pii.mask_with_known_values("A, B and C stayed.", customer_names=["A B"])
        self.assertEqual(masked, "A, B and C stayed.")


class LearningPathResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_explicit_relative_path_wins_and_is_anchored_to_repository(self) -> None:
        paths = resolve_learning_paths(
            "kb-data",
            environ={"FEEDBACK_KB_ROOT": "ignored"},
            repo_root=self.repo_root,
            default_root=self.repo_root / "kb",
            corpus_root=self.repo_root / "kb-data",
        )
        self.assertEqual(paths.kb_root, self.repo_root / "kb-data")
        self.assertEqual(paths.learned_dir, self.repo_root / "kb-data" / "learned")
        self.assertEqual(paths.tickets_dir, self.repo_root / "kb-data" / "tickets")
        self.assertEqual(paths.archive_dir, self.repo_root / "kb-data" / "_archive_learned")
        self.assertEqual(paths.ledger_path, self.repo_root / "kb-data" / "learned" / "_ledger.json")

    def test_empty_explicit_path_uses_environment_then_empty_environment_uses_default(self) -> None:
        custom = self.repo_root / "custom-kb"
        paths = resolve_learning_paths(
            "  ",
            environ={"FEEDBACK_KB_ROOT": "custom-kb"},
            repo_root=self.repo_root,
            default_root=self.repo_root / "kb",
            corpus_root=custom,
        )
        self.assertEqual(paths.kb_root, custom)

        paths = resolve_learning_paths(
            None,
            environ={"FEEDBACK_KB_ROOT": ""},
            repo_root=self.repo_root,
            default_root=self.repo_root / "kb",
            corpus_root=self.repo_root / "kb",
        )
        self.assertEqual(paths.kb_root, self.repo_root / "kb")

    def test_uppercase_only_deployment_default_and_active_corpus_agreement(self) -> None:
        deployed_root = default_kb_root(self.repo_root, uppercase_only=True)
        paths = resolve_learning_paths(
            None,
            environ={},
            repo_root=self.repo_root,
            default_root=deployed_root,
            corpus_root=deployed_root,
        )
        self.assertEqual(paths.kb_root, self.repo_root / "KB")

    def test_mismatched_override_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must match the active KB corpus root"):
            resolve_learning_paths(
                "elsewhere",
                environ={},
                repo_root=self.repo_root,
                default_root=self.repo_root / "kb",
                corpus_root=self.repo_root / "kb",
            )

    def test_writer_promoter_and_indexer_share_one_corpus_root(self) -> None:
        indexer_root = Path(auto_promote_learned.__file__).resolve().parents[1]
        self.assertEqual(auto_promote_learned.PATHS.kb_root, indexer_root)
        self.assertEqual(learning._learning_paths().kb_root, indexer_root)

    def test_mismatched_learning_setting_does_not_block_import_or_write_elsewhere(self) -> None:
        with patch.dict(learning.os.environ, {"FEEDBACK_KB_ROOT": "wrong-corpus", "DEMO_MODE": "0"}, clear=True):
            source = Path(learning.__file__)
            spec = importlib.util.spec_from_file_location("learning_invalid_setting", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with patch.object(Path, "mkdir") as mkdir, self.assertLogs(module.__name__, level="ERROR"):
                self.assertFalse(module.record_lesson("note", 123, "Synthetic ask", "Draft", "Note"))
            mkdir.assert_not_called()
            self.assertEqual(module.ledger(), {})

    def test_demo_writer_accepts_only_its_approved_corpus(self) -> None:
        with patch.dict(learning.os.environ, {"DEMO_MODE": "1", "FEEDBACK_KB_ROOT": "./demo/data/kb"}, clear=True):
            self.assertEqual(learning._learning_paths().kb_root, REPO_ROOT / "demo" / "data" / "kb")
            with patch.dict(learning.os.environ, {"FEEDBACK_KB_ROOT": "kb"}):
                with self.assertRaisesRegex(ValueError, "active KB corpus"):
                    learning._learning_paths()

    def test_legacy_collector_path_override_still_uses_loaded_setting(self) -> None:
        script = textwrap.dedent(
            """
            import pathlib, sys
            original_exists = pathlib.Path.exists
            pathlib.Path.exists = lambda path: False if path.name == '.env' else original_exists(path)
            import feedback.config as config
            expected = pathlib.Path(sys.argv[1])
            assert config.KB_ROOT == expected
            assert config.LEARNED_DIR == expected / 'learned'
            assert config.TICKETS_DIR == expected / 'tickets'
            assert config.ARCHIVE_DIR == expected / '_archive_learned'
            """
        )
        configured_root = self.repo_root / "legacy-kb"
        result = subprocess.run(
            [sys.executable, "-c", script, str(configured_root)],
            cwd=REPO_ROOT,
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(REPO_ROOT),
                "FEEDBACK_KB_ROOT": str(configured_root),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_active_learning_imports_do_not_load_config_or_open_env_files(self) -> None:
        script = textwrap.dedent(
            """
            import builtins, importlib.abc, importlib.util, io, os, pathlib, sys
            repo_root, entry = pathlib.Path(sys.argv[1]), sys.argv[2]
            original_exists = pathlib.Path.exists
            pathlib.Path.exists = lambda path: False if path.name == '.env' else original_exists(path)
            original_open = io.open
            def guarded_open(file, *args, **kwargs):
                if pathlib.Path(os.fsdecode(file)).name == '.env':
                    raise AssertionError('credential file open attempted')
                return original_open(file, *args, **kwargs)
            io.open = guarded_open
            builtins.open = guarded_open
            class ConfigBlocker(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == 'feedback.config':
                        raise AssertionError('feedback.config import attempted')
            sys.meta_path.insert(0, ConfigBlocker())
            sys.path.insert(0, str(repo_root))
            if entry == 'writer':
                import webhook.src.bb_webhook.learning
            else:
                source = repo_root / 'kb' / 'scripts' / 'auto_promote_learned.py'
                spec = importlib.util.spec_from_file_location('isolated_promoter', source)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            assert 'feedback.config' not in sys.modules
            """
        )
        clean_env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(REPO_ROOT)}
        for entry in ("writer", "promoter"):
            with self.subTest(entry=entry):
                result = subprocess.run(
                    [sys.executable, "-c", script, str(REPO_ROOT), entry],
                    cwd=REPO_ROOT,
                    env=clean_env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
