"""Synthetic startup lifecycle: no Hermes runtime, provider or credentials."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_live_tests
import qa_receipt
from qa_harness import atomic_json, profile_config
from qa_safety import GROUPS


class StartupBaselineTests(unittest.TestCase):
    def run_synthetic(self, startup_mutation=None, case_mutation=None, limit=1, setup_mutation=None):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "run"
            state = {"model_calls": 0, "closed": False}

            class SyntheticHarness:
                def __init__(self, **kwargs):
                    self.output = output
                    self.home = output / "home"
                    (self.home / ".hermes").mkdir(parents=True)
                    self.hermes_python = Path(sys.executable)
                    self.hermes_source = Path(name) / "unused-source"
                    self.launch = []
                    self.essentials = {}
                    self.profile = self.home / ".hermes/config.yaml"
                    self.config = profile_config({"default": "qa-model", "provider": "custom",
                                                  "base_url": "https://example.invalid"},
                                                 {g: 19000 + i for i, g in enumerate(GROUPS)})
                    atomic_json(self.profile, self.config)
                    self.requested_model_runtime = qa_receipt.model_runtime_identity(self.profile, self.hermes_python)
                    if setup_mutation:
                        setup_mutation(self.config)
                        atomic_json(self.profile, self.config)

                def start(self, fixture):
                    self.config["_config_version"] = 46
                    if startup_mutation:
                        startup_mutation(self.config)
                    atomic_json(self.profile, self.config)

                def run(self, scenario, ordinal):
                    state["model_calls"] += 1  # Synthetic call marker only.
                    if case_mutation:
                        case_mutation(self.config)
                        atomic_json(self.profile, self.config)
                    return {"id": scenario["id"], "tool_calls": []}

                def close(self):
                    state["closed"] = True

            def capture_receipt(suite, ids, results, before, after):
                state["before"], state["after"] = before, after
                return {"schema": qa_receipt.RECEIPT_SCHEMA, "suite": suite,
                        "complete": False, "bindings": before, "ids": ids,
                        "bindings_after_sha256": qa_receipt.digest(after), "kb_observed": []}

            argv = ["run_live_tests.py", "--hermes-python", sys.executable,
                    "--hermes-source", name, "--model-config", str(Path(name) / "unused-model.json"),
                    "--output", str(output), "--limit", str(limit)]
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(sys, "argv", argv))
                stack.enter_context(patch.object(run_live_tests, "Harness", SyntheticHarness))
                for key in ("source_fingerprint", "hermes_identity", "instruction_identity", "kb_snapshot"):
                    stack.enter_context(patch.object(run_live_tests, key, return_value={"sha256": "a" * 64}))
                stack.enter_context(patch.object(run_live_tests, "run_receipt", side_effect=capture_receipt))
                # Retain the real digest/KB integrity comparison; synthetic unrelated
                # source/skill shapes are not an instruction identity claim.
                stack.enter_context(patch.object(qa_receipt, "_require_model_runtime"))
                stack.enter_context(patch.object(qa_receipt, "_require_execution_rows"))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                state["exit_code"] = run_live_tests.main()
            if (output / "run.json").exists():
                state["receipt"] = json.loads((output / "run.json").read_text())
            return state

    def test_startup_version_is_normalized_before_the_first_case_and_retained(self):
        state = self.run_synthetic()
        self.assertEqual(state["exit_code"], 0)
        self.assertEqual(state["model_calls"], 1)
        self.assertEqual(state["before"]["model_runtime"]["_config_version"], 46)
        self.assertEqual(state["before"], state["after"])
        self.assertTrue(state["closed"])

    def test_requested_semantic_changes_during_preflight_abort_before_any_case(self):
        mutations = {
            "model": lambda c: c["model"].update(default="other-model"),
            "provider": lambda c: c["model"].update(provider="other-provider"),
            "endpoint": lambda c: c["model"].update(base_url="https://other.invalid"),
            "agent": lambda c: c["agent"].update(max_turns=40),
            "memory": lambda c: c["memory"].update(memory_enabled=True),
            "tools": lambda c: c["mcp_servers"]["buttonsbebe_kb"]["tools"].update(include=[]),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                state = self.run_synthetic(startup_mutation=mutation)
                self.assertEqual(state["exit_code"], 1)
                self.assertEqual(state["model_calls"], 0)
                self.assertNotIn("receipt", state)
                self.assertTrue(state["closed"])

    def test_version_change_during_a_case_still_invalidates_the_receipt(self):
        state = self.run_synthetic(case_mutation=lambda c: c.update(_config_version=47))
        self.assertEqual(state["exit_code"], 1)
        self.assertEqual(state["model_calls"], 1)
        self.assertEqual(state["before"]["model_runtime"]["_config_version"], 46)
        self.assertEqual(state["after"]["model_runtime"]["_config_version"], 47)


    def test_changed_profile_stops_before_another_case(self):
        state = self.run_synthetic(case_mutation=lambda c: c["model"].update(default="unexpected-model"), limit=2)
        self.assertEqual(state["exit_code"], 1)
        self.assertEqual(state["model_calls"], 1)
        self.assertTrue(state["closed"])
        self.assertNotIn("receipt", state)

    def test_setup_bootstrap_cannot_replace_requested_model_semantics(self):
        state = self.run_synthetic(setup_mutation=lambda c: c["model"].update(provider="unexpected-provider"))
        self.assertEqual(state["exit_code"], 1)
        self.assertEqual(state["model_calls"], 0)
        self.assertTrue(state["closed"])
        self.assertNotIn("receipt", state)


if __name__ == "__main__":
    unittest.main()
