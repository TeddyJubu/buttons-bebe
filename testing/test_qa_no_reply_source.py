"""Adversarial tests for source-bound deterministic no-reply validation.

These tests copy only synthetic scenario catalogs and reviewed Python source into
private temporary roots. They do not import the Hermes runner module or load
credential/configuration files.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import qa_receipt


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    "processor/draft_cleaner.py",
    "processor/hermes_runner/constants.py",
    "processor/hermes_runner/runner.py",
    "testing/scenarios.json",
    "testing/reliability-scenarios.json",
)
CLEANER = SOURCE_PATHS[0]
CONSTANTS = SOURCE_PATHS[1]
RUNNER = SOURCE_PATHS[2]
ACK_REASON = "no question to answer (thanks/ack only)"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_source_tree(destination: Path) -> Path:
    repo = destination / "repo"
    for relative in SOURCE_PATHS:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    return repo


def source_bindings(repo: Path) -> dict:
    return {"source": {"files": {relative: file_sha256(repo / relative)
                                  for relative in SOURCE_PATHS}}}


def no_draft_template() -> dict:
    tree = ast.parse((ROOT / CONSTANTS).read_text(encoding="utf-8"))
    matches = [node.value for node in tree.body
               if isinstance(node, ast.AnnAssign)
               and isinstance(node.target, ast.Name)
               and node.target.id == "_NO_DRAFT_RESULT"]
    if len(matches) != 1:
        raise AssertionError("Expected one literal _NO_DRAFT_RESULT in reviewed constants")
    value = ast.literal_eval(matches[0])
    if not isinstance(value, dict):
        raise AssertionError("Reviewed no-draft template must be a literal dictionary")
    return value


class DeterministicNoReplySourceTests(unittest.TestCase):
    def fixture_repo(self):
        temp = tempfile.TemporaryDirectory(prefix="qa-no-reply-source-")
        self.addCleanup(temp.cleanup)
        return copy_source_tree(Path(temp.name))

    def assert_rejected_after_rehash(self, mutate, marker: Path | None = None):
        repo = self.fixture_repo()
        path = repo / CLEANER
        before = file_sha256(path)
        path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
        bindings = source_bindings(repo)
        self.assertNotEqual(bindings["source"]["files"][CLEANER], before)
        with self.assertRaises(ValueError):
            qa_receipt._deterministic_no_reply("E02", bindings=bindings, repo=repo)
        if marker is not None:
            self.assertFalse(marker.exists(), "unreviewed cleaner source executed a filesystem effect")

    def test_exact_e02_source_still_builds_canonical_no_reply_record(self):
        repo = self.fixture_repo()
        bindings = source_bindings(repo)
        scenario, record = qa_receipt._deterministic_no_reply("E02", bindings=bindings, repo=repo)
        self.assertEqual(scenario["message"], "thanks!")
        self.assertEqual(scenario["subject"], "Just thanks")
        self.assertEqual(record["kind"], "deterministic_no_reply")
        self.assertEqual(record["result"], {**no_draft_template(), "reason": f"No draft generated — {ACK_REASON}"})
        self.assertFalse(record["model_called"])
        self.assertEqual(record["child_attempts"], 0)
        self.assertEqual(record["tool_call_count"], 0)

    def test_rehashed_cleaner_mutations_fail_closed_before_any_effect(self):
        with tempfile.TemporaryDirectory(prefix="qa-no-reply-marker-") as temp:
            marker = Path(temp) / "cleaner-ran.txt"
            safe_prefix = "from __future__ import annotations\n"

            def inject_after_future(statement):
                def mutate(source):
                    self.assertIn(safe_prefix, source)
                    return source.replace(safe_prefix, safe_prefix + statement + "\n", 1)
                return mutate

            attacks = {
                "filesystem_marker": inject_after_future(
                    f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed', encoding='utf-8')"),
                "unsupported_import": inject_after_future("import pathlib"),
                "dynamic_import_call": inject_after_future("__import__('pathlib')"),
                "dunder_attribute_side_effect": inject_after_future(
                    f"from pathlib import Path\nPath({str(marker)!r}).__getattribute__('write_text')('executed')"),
                "decorator_side_effect": lambda source: source.replace(
                    "def should_draft(message: str, subject: str = \"\") -> ShouldDraft:",
                    f"@__import__('pathlib').Path({str(marker)!r}).write_text('executed')\n"
                    "def should_draft(message: str, subject: str = \"\") -> ShouldDraft:", 1),
                "gate_reason_rebind": lambda source: source.replace(
                    f'ShouldDraft(False, "{ACK_REASON}")',
                    'ShouldDraft(False, "changed gate reason")', 1),
                "changed_default": lambda source: source.replace(
                    'def should_draft(message: str, subject: str = "") -> ShouldDraft:',
                    'def should_draft(message: str, subject: str = "thanks") -> ShouldDraft:', 1),
                "changed_annotation": lambda source: source.replace(
                    'def should_draft(message: str, subject: str = "") -> ShouldDraft:',
                    'def should_draft(message: object, subject: str = "") -> ShouldDraft:', 1),
                "unreviewed_dependency_call": lambda source: source.replace(
                    "import re\n", "import re\nre.purge()\n", 1),
                "unsupported_attribute_shape": lambda source: source.replace(
                    "import re\n", "import re\nre.__dict__\n", 1),
                "gate_dependency_rebinding": lambda source: source + "\n_ACK_ANCHORS = _ACK_ANCHORS\n",
                "duplicate_gate_binding": lambda source: source + (
                    "\ndef should_draft(message, subject=\"\"):\n"
                    "    return ShouldDraft(False, \"duplicate binding\")\n"),
            }
            for label, mutate in attacks.items():
                with self.subTest(mutation=label):
                    if marker.exists():
                        marker.unlink()
                    self.assert_rejected_after_rehash(mutate, marker if "marker" in label or "side_effect" in label else None)
                    self.assertFalse(marker.exists(), f"{label} wrote the synthetic marker")

    def test_rehashed_template_rebinding_is_rejected(self):
        repo = self.fixture_repo()
        constants = repo / CONSTANTS
        original = constants.read_text(encoding="utf-8")
        constants.write_text(original + "\n_NO_DRAFT_RESULT = {**_NO_DRAFT_RESULT, 'priority': 'high'}\n", encoding="utf-8")
        bindings = source_bindings(repo)
        with self.assertRaises(ValueError):
            qa_receipt._deterministic_no_reply("E02", bindings=bindings, repo=repo)

    def test_rehashed_runner_bypasses_and_prefix_changes_are_rejected(self):
        expected = {**no_draft_template(), "reason": f"No draft generated — {ACK_REASON}"}
        body = repr(expected)

        def literal_return(source):
            target = "    gate = should_draft(message_text, ticket_subject)\n    if not gate.ok:\n"
            self.assertIn(target, source)
            return source.replace(target, f"    return {body}\n    if not gate.ok:\n", 1)

        mutations = {
            "literal_canonical_return_without_gate": literal_return,
            "swapped_gate_arguments": lambda source: source.replace(
                "should_draft(message_text, ticket_subject)",
                "should_draft(ticket_subject, message_text)", 1),
            "changed_no_reply_reason": lambda source: source.replace(
                'f\"No draft generated — {gate.reason}\"',
                'f\"No draft generated — altered {gate.reason}\"', 1),
            "duplicate_runner_binding": lambda source: source + (
                "\ndef process_ticket_with_hermes(*args, **kwargs):\n"
                f"    return {body}\n"),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label):
                repo = self.fixture_repo()
                path = repo / RUNNER
                original_hash = file_sha256(path)
                path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
                bindings = source_bindings(repo)
                self.assertNotEqual(bindings["source"]["files"][RUNNER], original_hash)
                with self.assertRaises(ValueError):
                    qa_receipt._deterministic_no_reply("E02", bindings=bindings, repo=repo)

    def test_rehashed_runner_side_effect_before_gate_is_rejected_without_execution(self):
        with tempfile.TemporaryDirectory(prefix="qa-runner-marker-") as temp:
            marker = Path(temp) / "runner-was-not-imported.txt"
            repo = self.fixture_repo()
            path = repo / RUNNER
            source = path.read_text(encoding="utf-8")
            target = "    gate = should_draft(message_text, ticket_subject)\n"
            self.assertIn(target, source)
            path.write_text(source.replace(
                target, f"    open({str(marker)!r}, 'w').write('executed')\n" + target, 1),
                encoding="utf-8")
            bindings = source_bindings(repo)
            with self.assertRaises(ValueError):
                qa_receipt._deterministic_no_reply("E02", bindings=bindings, repo=repo)
            self.assertFalse(marker.exists(), "receipt validation executed altered runner source")

    def test_actual_runner_false_gate_is_spied_without_credentials_or_child(self):
        """Import the real runner with dotenv disabled; every settings/child call is stubbed."""
        processor = str(ROOT / "processor")
        original_path = list(sys.path)
        def owned_module(name):
            return (name == "config" or name == "draft_cleaner" or name == "logging_setup"
                    or name == "shared" or name.startswith(("hermes_runner", "shared.", "bb_webhook")))
        prior_modules = {name: module for name, module in sys.modules.items() if owned_module(name)}
        for name in list(sys.modules):
            if owned_module(name):
                sys.modules.pop(name, None)
        sys.path.insert(0, processor)
        try:
            # processor/config.py skips load_dotenv when DEMO_MODE is set before import.
            with patch.dict(os.environ, {"DEMO_MODE": "1"}, clear=False):
                runner = importlib.import_module("hermes_runner.runner")
            gate_calls = []
            setting_calls = []
            child_calls = []
            log_calls = []

            def gate(message, subject):
                gate_calls.append((message, subject))
                return SimpleNamespace(ok=False, reason=ACK_REASON)

            def forbidden_settings():
                setting_calls.append("called")
                raise AssertionError("no-reply branch must return before settings access")

            with patch.object(runner, "should_draft", side_effect=gate), \
                    patch.object(runner, "get_settings", side_effect=forbidden_settings), \
                    patch.object(runner, "run_bounded", side_effect=lambda *a, **k: child_calls.append("child")), \
                    patch.object(runner, "log_event", side_effect=lambda *a, **k: log_calls.append((a, k))):
                actual = runner.process_ticket_with_hermes(
                    900000001, "thanks!", "Just thanks", "qa@example.com", [])
            expected = {**runner._NO_DRAFT_RESULT, "reason": f"No draft generated — {ACK_REASON}"}
            self.assertEqual(gate_calls, [("thanks!", "Just thanks")])
            self.assertEqual(actual, expected)
            self.assertEqual(setting_calls, [])
            self.assertEqual(child_calls, [])
            self.assertEqual(len(log_calls), 1)

            actionable_calls = []
            settings_sentinel = RuntimeError("synthetic settings reached")

            def actionable_gate(message, subject):
                actionable_calls.append((message, subject))
                return SimpleNamespace(ok=True, reason="actionable")

            def sentinel_settings():
                setting_calls.append("actionable")
                raise settings_sentinel

            with patch.object(runner, "should_draft", side_effect=actionable_gate), \
                    patch.object(runner, "get_settings", side_effect=sentinel_settings), \
                    patch.object(runner, "run_bounded", side_effect=lambda *a, **k: child_calls.append("child")):
                with self.assertRaisesRegex(RuntimeError, "synthetic settings reached"):
                    runner.process_ticket_with_hermes(
                        900000002, "When will it ship?", "Shipping", "qa@example.com", [])
            self.assertEqual(actionable_calls, [("When will it ship?", "Shipping")])
            self.assertEqual(setting_calls, ["actionable"])
            self.assertEqual(child_calls, [])
        finally:
            sys.path[:] = original_path
            for name in list(sys.modules):
                if owned_module(name):
                    sys.modules.pop(name, None)
            sys.modules.update(prior_modules)


if __name__ == "__main__":
    unittest.main()
