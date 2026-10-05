import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import qa_receipt
import yaml
from qa_receipt import (build_receipt, catalog, check_receipt, judgment_template, kb_snapshot, observed_kb, run_receipt,
                        source_fingerprint, model_runtime_identity, check_run_integrity,
                        instruction_identity, seed_instructions, INSTRUCTION_FILES)
from qa_harness import profile_config
from qa_safety import GROUPS

HERMES = {"launch_sha256": "e" * 64, "source_sha256": "f" * 64, "source_files": 1}
POLICIES = kb_snapshot("policies-only")
ESSENTIALS = {"hermes-agent": "autonomous-ai-agents/hermes-agent"}
IDS = {"core": [f"S{i:02}" for i in range(1, 49)], "reliability": [f"Q{i:02}" for i in range(1, 11)]}


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name).resolve() / "repo"
        self.out = Path(self._tmp.name).resolve() / "private"
        self.out.mkdir()
        self.profile = self.out / "config.yaml"
        self.interpreter = self.out / "python"
        self.interpreter.write_bytes(b"synthetic interpreter")
        self.profile.write_text(json.dumps(profile_config(
            {"default": "test-model", "provider": "custom", "api_key": "synthetic-key"},
            {group: 19000 + i for i, group in enumerate(GROUPS)})))
        self.model_runtime = model_runtime_identity(self.profile, self.interpreter)
        # This fixture pin is synthetic; it never claims a real binary execution.
        fixture_pin = patch.object(qa_receipt, "APPROVED_INTERPRETER_SHA256", self.model_runtime["interpreter_sha256"])
        fixture_pin.start()
        self.addCleanup(fixture_pin.stop)
        for name, text in {"processor/hermes_runner/prompt.py": "PROMPT = 1\n", "processor/draft_cleaner.py": "",
                           "intake/message_content.py": "CLEANUP_VERSION = 'fixture'\n",
                           "processor/orchestrator.py": "", "processor/hermes_runner/process.py": "synthetic helper\n", "webhook/src/bb_webhook/app.py": "",
                           "kb/scripts/search_kb.py": "", "testing/qa_harness.py": "", "testing/test_qa_harness.py": "",
                           "kb/policies/returns.md": "Returns within 30 days.\n"}.items():
            self.write(name, text)
        for name in (*INSTRUCTION_FILES, "skills/buttonsbebe/gorgias/SKILL.md"):
            self.write(f"hermes/{name}", f"reviewed {name}\n")
        self.home = self.out / "home"
        (self.home / ".hermes").mkdir(parents=True)
        seed_instructions(self.home / ".hermes", self.repo)
        self.hsrc = self.out / "hermes-source"
        for name, text in {"skills/autonomous-ai-agents/DESCRIPTION.md": "agents\n",
                           "skills/autonomous-ai-agents/hermes-agent/SKILL.md": "essential\n",
                           "skills/autonomous-ai-agents/hermes-agent/references/a.md": "ref\n",
                           "skills/other/not-essential/SKILL.md": "never seeded\n"}.items():
            (self.hsrc / name).parent.mkdir(parents=True, exist_ok=True)
            (self.hsrc / name).write_text(text)
        self.install_essentials()
        self.write("testing/scenarios.json", json.dumps([{"id": i} for i in IDS["core"]]))
        self.write("testing/reliability-scenarios.json", json.dumps([{"id": i} for i in IDS["reliability"]]))
        git = patch.object(qa_receipt, "_git", return_value=("a" * 40, False))
        git.start()
        self.addCleanup(git.stop)
        self.addCleanup(self._tmp.cleanup)

    def install_essentials(self):
        """What Hermes essential-only sync writes, computed here independently (literal md5 manifest)."""
        hermes_home = self.home / ".hermes"
        (hermes_home / ".no-bundled-skills").write_text("")
        source = self.hsrc / "skills/autonomous-ai-agents/hermes-agent"
        md5 = hashlib.md5()
        for path in sorted(source.rglob("*")):
            if path.is_file():
                target = hermes_home / "skills/autonomous-ai-agents/hermes-agent" / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
                md5.update(str(path.relative_to(source)).encode()); md5.update(path.read_bytes())
        (hermes_home / "skills/autonomous-ai-agents/DESCRIPTION.md").write_text("agents\n")
        (hermes_home / "skills/.bundled_manifest").write_text(f"hermes-agent:{md5.hexdigest()}\n")

    def identity(self):
        return instruction_identity(self.home, ESSENTIALS, self.hsrc, self.repo)

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_file(self, suite, ids=None, after=None, hermes=HERMES, snapshot=POLICIES, kb_calls=(), model_runtime=None):
        ids = IDS[suite] if ids is None else ids
        before = {"source": source_fingerprint(self.repo), "hermes": hermes, "kb_snapshot": snapshot,
                  "model_runtime": self.model_runtime if model_runtime is None else model_runtime,
                  "instructions": self.identity()}
        results = [{"id": i, "tool_calls": list(kb_calls) if n == 0 else [],
                    "execution": self.synthetic_execution(before, n)} for n, i in enumerate(ids)]
        receipt = run_receipt(suite, ids, results, before, {**before, **(after or {})}, self.repo)
        path = self.out / f"{suite}-run.json"
        path.write_text(json.dumps(receipt))
        return path

    def synthetic_execution(self, bindings, ordinal=0):
        pin = bindings["model_runtime"]["interpreter_sha256"]
        helper = bindings["source"]["files"]["processor/hermes_runner/process.py"]
        image = {"path": "/synthetic/private/home/.hermes/tools/python-3.14.7/bin/python3.14",
                 "normalized_path": "${QA_HOME}/.hermes/tools/python-3.14.7/bin/python3.14",
                 "role": "private_managed_python", "device": 1, "inode": 2, "size": 3,
                 "device_after": 1, "inode_after": 2, "size_after": 3,
                 "path_source": "/proc/PID/exe", "sha256_before": pin, "sha256_after": pin, "samples": 2}
        return {"base_launch_sha256": bindings["hermes"]["launch_sha256"], "command_sha256": "c" * 64,
                "observation": {"schema": 1, "status": "verified_sampled", "pid": 1000 + ordinal,
                    "pid_start_ticks": 100, "samples": 2, "reader_kind": "linux_proc",
                    "required_effective_role": "private_managed_python", "expected_sha256": pin,
                    "requested_launch_sha256": "c" * 64, "helper_sha256_before": helper, "helper_sha256_after": helper,
                    "effective_observed_image": 0, "all_exec_transitions_observed": False,
                    "process_exit_observed": True, "helper_completed": True, "helper_cleanup_completed": True,
                    "child_returncode": 0, "helper_returncode": 0, "final_pid_state": "absent_after_helper_reap",
                    "selected_python": "/synthetic/approved/python3.14", "images": [image],
                    "observed_transitions": [{"image": 0, "elapsed_seconds": 0.01}]}}

    def judge(self, run, **overrides):
        value = judgment_template(run)
        value["verdicts"] = {i: "PASS" for i in value["verdicts"]}
        value["verdicts"].update(overrides.pop("verdicts", {}))
        value.update(overrides)
        path = run.with_name(run.stem + "-judgments.json")
        path.write_text(json.dumps(value))
        return path

    def build(self, core=None, reliability=None, **judged):
        core = core or self.run_file("core")
        reliability = reliability or self.run_file("reliability")
        return build_receipt(core, self.judge(core, **judged), reliability, self.judge(reliability), self.repo)

    def test_fingerprint_hashes_behavior_and_policy_content_but_not_tests_state_or_secrets(self):
        first = source_fingerprint(self.repo)
        self.assertEqual((first["head"], first["dirty"]), ("a" * 40, False))
        for name, text in {"processor/test_orchestrator.py": "", "processor/.env": "SECRET=1",
                           "webhook/src/bb_webhook/data/webhook.db": "rows", "webhook/src/bb_webhook/tests/x.py": "",
                           "processor/.venv/lib/site.py": "", "kb/learned/lesson-1.md": "unapproved"}.items():
            self.write(name, text)
        self.assertEqual(source_fingerprint(self.repo)["sha256"], first["sha256"])
        for name in ("intake/message_content.py", "processor/orchestrator.py", "webhook/src/bb_webhook/message_times.py",
                     "console-src/inbox2/app.js", "kb/scripts/search_kb.py", "kb/policies/returns.md",
                     "tools/gorgias_mcp.py", "tools/redo_mcp.py", "tools/gorgias_content.py", "tools/_common.py",
                     "feedback/pii.py", "feedback/learning_paths.py"):
            with self.subTest(name=name):
                self.write(name, "changed\n")
                changed = source_fingerprint(self.repo)
                self.assertIn(name, changed["files"])
                self.assertNotEqual(changed["sha256"], first["sha256"])
                first = changed
        (self.repo / "processor/orchestrator.py").unlink()
        with self.assertRaisesRegex(ValueError, "processor/orchestrator.py"):
            source_fingerprint(self.repo)

    def test_catalogs_require_exact_unique_counts(self):
        self.assertEqual(catalog("reliability", self.repo)[1], IDS["reliability"])
        self.write("testing/reliability-scenarios.json", json.dumps([{"id": "Q01"}] * 10))
        with self.assertRaises(ValueError):
            catalog("reliability", self.repo)
        self.write("testing/scenarios.json", json.dumps([{"id": i} for i in IDS["core"][:47]]))
        with self.assertRaises(ValueError):
            catalog("core", self.repo)

    def test_all_pass_full_runs_on_current_source_pass_release(self):
        receipt = self.build()
        self.assertEqual((receipt["review_complete"], receipt["release_passed"]), (True, True))
        self.assertEqual(receipt["suites"]["core"]["counts"]["PASS"], 48)
        self.assertEqual(check_receipt(receipt, self.repo), {"review_complete": True, "release_passed": True})
        self.assertNotIn("hermes_output", json.dumps(receipt))
        self.assertNotIn("/synthetic/private/home", json.dumps(receipt))

    def test_actual_child_evidence_required_even_when_all_other_hashes_match(self):
        original = self.build()
        mutations = [
            lambda s: s.pop("execution"),
            lambda s: s["execution"].pop(),
            lambda s: s["execution"].reverse(),
            lambda s: s["execution"][0].update(base_launch_sha256="0" * 64),
        ]
        for key, value in (("reader_kind", "injected"), ("status", "incomplete"),
                           ("required_effective_role", "selected_python"), ("effective_observed_image", 7),
                           ("expected_sha256", "0" * 64), ("requested_launch_sha256", "0" * 64),
                           ("helper_sha256_after", "0" * 64), ("pid_start_ticks", 0),
                           ("helper_completed", False), ("helper_cleanup_completed", False),
                           ("process_exit_observed", False), ("child_returncode", 1),
                           ("final_pid_state", "live"), ("all_exec_transitions_observed", True)):
            mutations.append(lambda s, k=key, v=value: s["execution"][0]["observation"].update({k: v}))
        for key, value in (("sha256_after", "0" * 64), ("inode_after", 99), ("samples", 1),
                           ("path_source", "injected"), ("role", "selected_python"),
                           ("normalized_path", "${QA_HOME}/../host/python3")):
            mutations.append(lambda s, k=key, v=value: s["execution"][0]["observation"]["images"][0].update({k: v}))
        for ordinal, mutate in enumerate(mutations):
            with self.subTest(ordinal=ordinal):
                receipt = json.loads(json.dumps(original))
                summary = receipt["suites"]["core"]
                mutate(summary)
                if "execution" in summary:
                    summary["execution_sha256"] = qa_receipt.digest(summary["execution"])
                with self.assertRaises(ValueError):
                    check_receipt(receipt, self.repo)
        run = json.loads(self.run_file("core").read_text())
        run.pop("execution")
        with self.assertRaises(ValueError):
            check_run_integrity(run)
        receipt = json.loads(json.dumps(original))
        receipt["source"]["files"]["processor/hermes_runner/process.py"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source file identity"):
            check_receipt(receipt, self.repo)

    def test_execution_shareable_paths_and_transition_shapes(self):
        bindings = {"source": source_fingerprint(self.repo), "hermes": HERMES, "model_runtime": self.model_runtime,
                    "instructions": self.identity(), "kb_snapshot": POLICIES}
        case = {"id": IDS["core"][0], "execution": self.synthetic_execution(bindings)}
        observed = case["execution"]["observation"]
        observed["selected_python"] = "/private/synthetic/tenant/.hermes/tools/python3"
        selected = dict(observed["images"][0], role="selected_python",
                        normalized_path=observed["selected_python"], path=observed["selected_python"])
        observed["images"].insert(0, selected)
        observed["effective_observed_image"] = 1
        observed["samples"] = 4
        observed["observed_transitions"] = [{"image": 0, "elapsed_seconds": 0.0}, {"image": 1, "elapsed_seconds": 0.01}]
        run = run_receipt("core", [case["id"]], [case], bindings, bindings, self.repo)
        self.assertNotIn("/private/synthetic", json.dumps(run))
        self.assertEqual(run["execution"][0]["observation"]["selected_python"], "${SELECTED_PYTHON}")
        check_run_integrity(run)
        forged = json.loads(json.dumps(run))
        consumer = forged["execution"][0]["observation"]
        consumer["selected_python"] = "/private/synthetic/tenant/.hermes/tools/python3"
        consumer["images"][0]["normalized_path"] = consumer["selected_python"]
        with self.assertRaisesRegex(ValueError, "canonical shareable"):
            check_run_integrity(forged)
        receipt = self.build()
        receipt["suites"]["core"]["execution"][0]["observation"]["selected_python"] = "/private/synthetic/tenant/python3"
        receipt["suites"]["core"]["execution_sha256"] = qa_receipt.digest(receipt["suites"]["core"]["execution"])
        with self.assertRaisesRegex(ValueError, "canonical shareable"):
            check_receipt(receipt, self.repo)
        observed["observed_transitions"][-1]["private_path"] = "/private/synthetic/tenant/config.yaml"
        with self.assertRaises(ValueError):
            run_receipt("core", [case["id"]], [case], bindings, bindings, self.repo)
        receipt = self.build()
        receipt["suites"]["core"]["execution"][0]["observation"]["observed_transitions"][0]["private_path"] = "private"
        receipt["suites"]["core"]["execution_sha256"] = qa_receipt.digest(receipt["suites"]["core"]["execution"])
        with self.assertRaises(ValueError):
            check_receipt(receipt, self.repo)

    def test_malformed_direct_consumer_shapes_raise_bounded_value_error(self):
        good = self.build()
        for malformed in (None, [], {**good, "suites": None}, {**good, "blocking_defects": None}):
            with self.subTest(receipt=type(malformed).__name__), self.assertRaises(ValueError):
                check_receipt(malformed, self.repo)
        run = json.loads(self.run_file("core").read_text())
        variants = [None, [], {k: v for k, v in run.items() if k != "bindings"},
                    {**run, "bindings": None}, {k: v for k, v in run.items() if k != "ids"},
                    {**run, "suite": []}, {**run, "kb_observed": None}]
        for malformed in variants:
            with self.subTest(run=type(malformed).__name__), self.assertRaises(ValueError):
                check_run_integrity(malformed)
        with self.assertRaises(ValueError):
            run_receipt("core", ["S01"], [None], run["bindings"], run["bindings"], self.repo)
        path = self.out / "null-run.json"
        path.write_text("null")
        with self.assertRaises(ValueError):
            build_receipt(path, path, path, path, self.repo)

    def test_different_models_providers_endpoints_and_runtime_settings_cannot_combine(self):
        original = json.loads(self.profile.read_text())
        for section, key, value in (("model", "default", "different-model"),
                                    ("model", "provider", "different-provider"),
                                    ("model", "base_url", "https://models.example.test/v1"),
                                    ("agent", "max_turns", 29)):
            with self.subTest(section=section, key=key):
                config = json.loads(json.dumps(original))
                config[section][key] = value
                self.profile.write_text(json.dumps(config))
                other = model_runtime_identity(self.profile, self.interpreter)
                with self.assertRaisesRegex(ValueError, "different model/runtime"):
                    self.build(reliability=self.run_file("reliability", model_runtime=other))
        self.profile.write_text(json.dumps(original))
        self.interpreter.write_bytes(b"different interpreter")
        with self.assertRaisesRegex(ValueError, "different model/runtime|execution identity"):
            self.build(reliability=self.run_file("reliability", model_runtime=model_runtime_identity(self.profile, self.interpreter)))

    def test_shard_ports_homes_auth_and_api_keys_do_not_change_identity_or_leak(self):
        other_home = self.out / "other-shard"
        other_home.mkdir()
        profile = other_home / "config.yaml"
        config = json.loads(self.profile.read_text())
        config["model"]["api_key"] = "different-synthetic-key"
        for i, server in enumerate(config["mcp_servers"].values()):
            server["url"] = f"http://127.0.0.1:{20000+i}/mcp"
        profile.write_text(json.dumps(config))
        (other_home / "auth.json").write_text(json.dumps({"access_token": "synthetic-oauth-token"}))
        identity = model_runtime_identity(profile, self.interpreter)
        self.assertEqual(identity, self.model_runtime)
        receipt = self.build(reliability=self.run_file("reliability", model_runtime=identity))
        self.assertTrue(check_receipt(receipt, self.repo)["release_passed"])
        for forbidden in ("synthetic-key", "different-synthetic-key", "synthetic-oauth-token", "api_key", "access_token"):
            self.assertNotIn(forbidden, json.dumps(receipt))

    def test_profile_is_reread_and_mutation_during_run_is_rejected(self):
        before = model_runtime_identity(self.profile, self.interpreter)
        config = json.loads(self.profile.read_text())
        config["model"]["default"] = "changed-on-disk"
        self.profile.write_text(json.dumps(config))
        after = model_runtime_identity(self.profile, self.interpreter)
        self.assertNotEqual(before, after)
        run = self.run_file("core", model_runtime=before, after={"model_runtime": after})
        with self.assertRaisesRegex(ValueError, "model/runtime.*changed during"):
            check_run_integrity(json.loads(run.read_text()))

    def test_unsafe_actual_profiles_and_rehashed_receipt_settings_are_refused(self):
        original = json.loads(self.profile.read_text())
        mutations = {
            "memory": lambda c: c["memory"].update(memory_enabled=True),
            "user profile": lambda c: c["memory"].update(user_profile_enabled=True),
            "native tools": lambda c: c["agent"].update(disabled_toolsets=[]),
            "CLI": lambda c: c["platform_toolsets"].update(cli=["terminal"]),
            "extra capability": lambda c: c["mcp_servers"]["buttonsbebe_gorgias"]["tools"]["include"].append("send_reply"),
            "missing capability": lambda c: c["mcp_servers"]["buttonsbebe_redo"]["tools"].update(include=[]),
            "trusted server": lambda c: c["mcp_servers"]["buttonsbebe_kb"].update(trust="trusted"),
            "disabled server": lambda c: c["mcp_servers"]["buttonsbebe_redo"].update(enabled=False),
            "missing server": lambda c: c["mcp_servers"].pop("buttonsbebe_gorgias"),
            "additional server": lambda c: c["mcp_servers"].update(other=c["mcp_servers"]["buttonsbebe_kb"].copy()),
            "remote server": lambda c: c["mcp_servers"]["buttonsbebe_kb"].update(url="http://example.invalid:19000/mcp"),
            "resource scope": lambda c: c["mcp_servers"]["buttonsbebe_kb"]["tools"].update(resources=False),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label, surface="actual profile"):
                config = json.loads(json.dumps(original))
                mutate(config)
                self.profile.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    model_runtime_identity(self.profile, self.interpreter)
            with self.subTest(label=label, surface="rehashed receipt"):
                identity = json.loads(json.dumps(self.model_runtime))
                mutate(identity)
                identity["sha256"] = qa_receipt.digest({k: v for k, v in identity.items() if k != "sha256"})
                with self.assertRaises(ValueError):
                    self.run_file("core", model_runtime=identity)
        self.profile.write_text(json.dumps(original))

    def test_legacy_or_missing_model_identity_cannot_be_reviewed_combined_or_checked(self):
        core, reliability = self.run_file("core"), self.run_file("reliability")
        core_judgments, rel_judgments = self.judge(core), self.judge(reliability)
        original = json.loads(core.read_text())
        for missing in (False, True):
            run = json.loads(json.dumps(original))
            if missing:
                del run["bindings"]["model_runtime"]
                run["bindings_after_sha256"] = qa_receipt.digest(run["bindings"])
            else:
                run["schema"] = 2
            core.write_text(json.dumps(run))
            with self.assertRaisesRegex(ValueError, "model/runtime"):
                judgment_template(core)
            with self.assertRaisesRegex(ValueError, "model/runtime"):
                build_receipt(core, core_judgments, reliability, rel_judgments, self.repo)
        core.write_text(json.dumps(original))
        for change in ("schema", "model_runtime"):
            receipt = self.build()
            if change == "schema":
                receipt["schema"] = 2
            else:
                del receipt["model_runtime"]
            with self.assertRaisesRegex(ValueError, "model/runtime"):
                check_receipt(receipt, self.repo)

    def test_instruction_identity_binds_exact_seeded_bytes(self):
        identity = self.identity()
        self.assertEqual(list(identity["files"]), ["SOUL.md", "skills/buttonsbebe/support-agent/SKILL.md",
                                                   "skills/buttonsbebe/ticket-processor/SKILL.md"])
        self.assertIs(identity["ignore_rules"], False)
        self.assertEqual(identity["files"]["SOUL.md"], hashlib.sha256(b"reviewed SOUL.md\n").hexdigest())
        self.assertEqual((self.home / ".hermes/SOUL.md").read_bytes(), (self.repo / "hermes/SOUL.md").read_bytes())
        self.assertFalse((self.home / ".hermes/skills/buttonsbebe/gorgias").exists())
        essential = identity["essential_skills"]
        self.assertEqual(essential["skills"], ESSENTIALS)
        self.assertEqual(essential["opt_out_marker"], ".no-bundled-skills")
        self.assertEqual(essential["source_sha256"], essential["installed_sha256"])
        # A changed essential source no longer matches the installed bytes.
        (self.hsrc / "skills/autonomous-ai-agents/hermes-agent/SKILL.md").write_text("upstream change\n")
        with self.assertRaisesRegex(ValueError, "pinned source"):
            self.identity()
        (self.hsrc / "skills/autonomous-ai-agents/hermes-agent/SKILL.md").write_text("essential\n")
        hermes_home = self.home / ".hermes"
        soul = hermes_home / "SOUL.md"
        def restore():
            for path in (soul, self.home / ".hermes.md", self.home / ".cursorrules", self.out / "AGENTS.md",
                         self.out / ".git", hermes_home / "skills/extra/SKILL.md", hermes_home / "memories/MEMORY.md",
                         hermes_home / "skills/other/not-essential/SKILL.md", hermes_home / ".no-bundled-skills",
                         hermes_home / "skills/autonomous-ai-agents/DESCRIPTION.md"):
                if path.is_symlink() or path.exists():
                    path.unlink()
            soul.write_bytes(b"reviewed SOUL.md\n")
            seed_instructions(hermes_home, self.repo)
            self.install_essentials()
        cases = {"differs": lambda: soul.write_bytes(b"tampered\n"),
                 "traverse links": lambda: (soul.unlink(), soul.symlink_to(self.repo / "hermes/SOUL.md")),
                 "regular file": lambda: soul.unlink(),
                 "context from .*home: .hermes.md": lambda: (self.home / ".hermes.md").write_text("x"),
                 "context from .*home: .cursorrules": lambda: (self.home / ".cursorrules").write_text("x"),
                 # A .git above the QA cwd makes Hermes walk up to it and load the ancestor's AGENTS.md.
                 "context from .*: AGENTS.md": lambda: ((self.out / ".git").write_text("gitdir: x"),
                                                         (self.out / "AGENTS.md").write_text("x")),
                 "skill files": lambda: ((hermes_home / "skills/extra").mkdir(exist_ok=True),
                                         (hermes_home / "skills/extra/SKILL.md").write_text("x")),
                 "opt-out marker": lambda: (hermes_home / ".no-bundled-skills").unlink(),
                 "missing QA skill files": lambda: (hermes_home / "skills/autonomous-ai-agents/hermes-agent/references/a.md").unlink(),
                 "differs from the pinned source": lambda: (hermes_home / "skills/autonomous-ai-agents/hermes-agent/SKILL.md").write_text("x"),
                 "differs from the reviewed source": lambda: (hermes_home / "skills/buttonsbebe/ticket-processor/SKILL.md").write_text("x"),
                 "Unexpected or missing QA skill files": lambda: ((hermes_home / "skills/other/not-essential").mkdir(parents=True, exist_ok=True),
                                                                  (hermes_home / "skills/other/not-essential/SKILL.md").write_text("never seeded\n")),
                 "pinned source": lambda: (hermes_home / "skills/.bundled_manifest").write_text("hermes-agent:0\n"),
                 "must not contain links": lambda: ((hermes_home / "skills/autonomous-ai-agents/DESCRIPTION.md").unlink(),
                     (hermes_home / "skills/autonomous-ai-agents/DESCRIPTION.md").symlink_to(self.hsrc / "skills/autonomous-ai-agents/DESCRIPTION.md")),
                 "memory": lambda: ((hermes_home / "memories").mkdir(exist_ok=True),
                                    (hermes_home / "memories/MEMORY.md").write_text("x"))}
        for message, mutate in cases.items():
            with self.subTest(message):
                mutate()
                with self.assertRaisesRegex(ValueError, message):
                    self.identity()
                restore()
        # Without a .git ancestor Hermes reads the cwd only, so ancestor AGENTS.md is not loaded.
        (self.out / "AGENTS.md").write_text("x")
        self.assertEqual(self.identity(), identity)
        restore()
        self.assertEqual(self.identity(), identity)

    def test_seeding_refuses_linked_directories_before_writing(self):
        elsewhere = self.out / "elsewhere"
        elsewhere.mkdir()
        target = self.out / "fresh/.hermes"
        target.mkdir(parents=True)
        (target / "skills").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not be a link"):
            seed_instructions(target, self.repo)
        self.assertEqual(list(elsewhere.iterdir()), [])
        linked_home = self.out / "linked-home"
        linked_home.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not be a link"):
            seed_instructions(linked_home, self.repo)
        self.assertEqual(list(elsewhere.iterdir()), [])
        # A linked source directory is refused even though each leaf is a regular file.
        source = self.repo / "hermes/skills/buttonsbebe/support-agent"
        source.rename(self.out / "moved-skill")
        source.symlink_to(self.out / "moved-skill", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "traverse links"):
            seed_instructions(self.out / "fresh2", self.repo)

    def test_memory_identity_accepts_only_absent_or_empty_real_directory(self):
        memories = self.home / '.hermes/memories'
        self.identity()
        memories.mkdir()
        self.identity()
        memories.rmdir()
        targets = [self.out / 'missing-memory', self.out / 'existing-memory']
        targets[1].mkdir()
        for target in targets:
            memories.symlink_to(target, target_is_directory=True)
            try:
                with self.assertRaisesRegex(ValueError, 'memory'):
                    self.identity()
            finally:
                memories.unlink()
        memories.write_text('')
        with self.assertRaisesRegex(ValueError, 'memory'):
            self.identity()
        memories.unlink()
        memories.mkdir()
        (memories / 'MEMORY.md').write_text('synthetic context')
        with self.assertRaisesRegex(ValueError, 'memory'):
            self.identity()

    def test_receipts_require_instruction_parity_bound_to_source(self):
        core = self.run_file("core")
        run = json.loads(core.read_text())
        self.assertEqual(run["schema"], 4)
        check_run_integrity(run)
        for tamper in ("delete", "ignore", "hash", "essential", "marker"):
            bad = json.loads(json.dumps(run))
            if tamper == "delete":
                del bad["bindings"]["instructions"]
            elif tamper == "ignore":
                bad["bindings"]["instructions"]["ignore_rules"] = True
            elif tamper == "essential":
                bad["bindings"]["instructions"]["essential_skills"]["installed_sha256"] = "0" * 64
            elif tamper == "marker":
                del bad["bindings"]["instructions"]["essential_skills"]["opt_out_marker"]
            else:
                bad["bindings"]["instructions"]["files"]["SOUL.md"] = "0" * 64
            bad["bindings_after_sha256"] = qa_receipt.digest(bad["bindings"])
            with self.subTest(tamper), self.assertRaisesRegex(ValueError, "instruction parity"):
                check_run_integrity(bad)
        (self.home / ".hermes/SOUL.md").write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "differs"):
            self.run_file("reliability")

    def test_instruction_identity_rejects_linked_roots_and_source_ancestors(self):
        roots=[self.home / '.hermes',self.repo / 'hermes',
               self.hsrc / 'skills',self.hsrc / 'skills/autonomous-ai-agents',
               self.hsrc / 'skills/autonomous-ai-agents/hermes-agent']
        for index,root in enumerate(roots):
            moved=self.out / f'moved-root-{index}'
            root.rename(moved)
            root.symlink_to(moved,target_is_directory=True)
            try:
                with self.subTest(root=root),self.assertRaisesRegex(ValueError,'links'):
                    instruction_identity(self.home,ESSENTIALS,self.hsrc,self.repo)
            finally:
                root.unlink()
                moved.rename(root)

    def test_forged_essential_metadata_cannot_pass_receipt_validation(self):
        run=json.loads(self.run_file('core').read_text())
        variants=[{'skills':{'arbitrary':'../../unbound'},'source_sha256':None,'installed_sha256':None},
                  {'source_sha256':None,'installed_sha256':None},
                  {'source_sha256':'not-a-hash','installed_sha256':'not-a-hash'},
                  {'skills':{'hermes-agent':'/absolute/path'}},
                  {'skills':{'hermes-agent':'autonomous-ai-agents/../unbound'}},
                  {'skills':{'hermes-agent':17}}]
        for changes in variants:
            bad=json.loads(json.dumps(run))
            identity=bad['bindings']['instructions']
            identity['essential_skills'].update(changes)
            identity['sha256']=qa_receipt.digest({key:value for key,value in identity.items() if key!='sha256'})
            bad['bindings_after_sha256']=qa_receipt.digest(bad['bindings'])
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'instruction parity'):
                check_run_integrity(bad)

    def test_profile_rejects_secret_urls_and_unknown_inference_settings_without_echoing(self):
        config = json.loads(self.profile.read_text())
        for url in ("https://user:synthetic-password@models.example.test/v1",
                    "https://models.example.test/v1?api_key=synthetic-url-key",
                    "https://models.example.test/v1#synthetic-url-secret"):
            config["model"]["base_url"] = url
            self.profile.write_text(json.dumps(config))
            with self.assertRaises(ValueError) as failure:
                model_runtime_identity(self.profile, self.interpreter)
            self.assertNotIn("synthetic", str(failure.exception))
        del config["model"]["base_url"]
        config["model"]["temperature"] = 0.3
        self.profile.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            model_runtime_identity(self.profile, self.interpreter)

    def test_yaml_profile_matches_equivalent_json_profile(self):
        config = json.loads(self.profile.read_text())
        config["model"]["base_url"] = "https://models.example.test/v1"
        json_profile = self.out / "equivalent.json"
        yaml_profile = self.out / "equivalent.yaml"
        json_profile.write_text(json.dumps(config))
        yaml_profile.write_text(yaml.safe_dump(config, sort_keys=False))

        json_identity = model_runtime_identity(json_profile, self.interpreter)
        yaml_identity = model_runtime_identity(yaml_profile, self.interpreter)

        self.assertEqual(yaml_identity, json_identity)
        self.assertNotIn("synthetic-key", json.dumps(yaml_identity))

    def test_optional_hermes_config_version_is_semantic_and_serialization_independent(self):
        config = json.loads(self.profile.read_text())
        without_version_json = self.out / "without-version.json"
        without_version_yaml = self.out / "without-version.yaml"
        without_version_json.write_text(json.dumps(config))
        without_version_yaml.write_text(yaml.safe_dump(config, sort_keys=False))
        baseline_json = model_runtime_identity(without_version_json, self.interpreter)
        baseline_yaml = model_runtime_identity(without_version_yaml, self.interpreter)
        self.assertEqual(baseline_json, baseline_yaml)

        identities = {}
        for version in (4, 5):
            versioned = {**config, "_config_version": version}
            json_profile = self.out / f"version-{version}.json"
            yaml_profile = self.out / f"version-{version}.yaml"
            json_profile.write_text(json.dumps(versioned))
            yaml_profile.write_text(yaml.safe_dump(versioned, sort_keys=False))
            identities[version] = model_runtime_identity(json_profile, self.interpreter)
            self.assertEqual(identities[version], model_runtime_identity(yaml_profile, self.interpreter))
            self.assertNotEqual(identities[version], baseline_json)
        self.assertNotEqual(identities[4], identities[5])
        for identity in (*identities.values(), baseline_json):
            self.assertNotIn("synthetic-key", json.dumps(identity))

        core = self.run_file("core", model_runtime=identities[5])
        reliability = self.run_file("reliability", model_runtime=identities[5])
        receipt = self.build(core=core, reliability=reliability)
        self.assertEqual(receipt["model_runtime"]["_config_version"], 5)
        self.assertEqual(check_receipt(receipt, self.repo), {"review_complete": True, "release_passed": True})
        self.assertNotIn("synthetic-key", json.dumps(receipt))

    def test_yaml_profile_ignores_only_api_key_and_rejects_credential_urls(self):
        config = json.loads(self.profile.read_text())
        config["model"]["api_key"] = "different-synthetic-key"
        key_only = self.out / "different-key.yaml"
        key_only.write_text(yaml.safe_dump(config, sort_keys=False))
        identity = model_runtime_identity(key_only, self.interpreter)
        self.assertEqual(identity, self.model_runtime)
        self.assertNotIn("different-synthetic-key", json.dumps(identity))

        for url in ("https://user:synthetic-password@models.example.test/v1",
                    "https://models.example.test/v1?api_key=synthetic-url-key",
                    "https://models.example.test/v1#synthetic-url-secret"):
            config["model"]["base_url"] = url
            profile = self.out / "credential-url.yaml"
            profile.write_text(yaml.safe_dump(config, sort_keys=False))
            with self.subTest(url=url), self.assertRaises(ValueError) as failure:
                model_runtime_identity(profile, self.interpreter)
            self.assertNotIn("synthetic", str(failure.exception))
        self.assertNotIn("synthetic", json.dumps(identity))

    def test_yaml_profile_rejects_unknown_keys_and_unsafe_tags(self):
        config = json.loads(self.profile.read_text())
        variants = []
        top_level = dict(config)
        top_level["unreviewed_setting"] = True
        variants.append(top_level)
        nested = json.loads(json.dumps(config))
        nested["model"]["temperature"] = 0.3
        variants.append(nested)
        for index, variant in enumerate(variants):
            profile = self.out / f"unknown-{index}.yaml"
            profile.write_text(yaml.safe_dump(variant, sort_keys=False))
            with self.subTest(unknown=index), self.assertRaisesRegex(ValueError, "Unsupported"):
                model_runtime_identity(profile, self.interpreter)

        marker = self.out / "unsafe-yaml-tag-was-executed"
        unsafe_profile = self.out / "unsafe-tag.yaml"
        unsafe_profile.write_text(
            "!!python/object/apply:builtins.open\n"
            f"- {json.dumps(str(marker))}\n"
            "- w\n"
        )
        with self.assertRaises(ValueError):
            model_runtime_identity(unsafe_profile, self.interpreter)
        self.assertFalse(marker.exists(), "YAML loading must not construct or execute Python objects")

    def test_yaml_and_json_profiles_reject_invalid_hermes_config_versions(self):
        config = json.loads(self.profile.read_text())
        for version in (True, -1, 1001):
            with self.subTest(version=version):
                versioned = {**config, "_config_version": version}
                for suffix, contents in (("json", json.dumps(versioned)),
                                         ("yaml", yaml.safe_dump(versioned, sort_keys=False))):
                    profile = self.out / f"invalid-version-{suffix}"
                    profile.write_text(contents)
                    with self.subTest(format=suffix), self.assertRaisesRegex(ValueError, "Unsupported"):
                        model_runtime_identity(profile, self.interpreter)

    def test_malformed_hermes_identity_is_rejected_at_every_receipt_boundary(self):
        core = self.run_file("core")
        reliability = self.run_file("reliability")
        run = json.loads(core.read_text())
        release = self.build(core=core, reliability=reliability)
        variants = [None, {}, {"launch_sha256": None, "source_sha256": None, "source_files": 0},
                    {**HERMES, "launch_sha256": "x" * 64}, {**HERMES, "source_sha256": "F" * 64},
                    {**HERMES, "source_files": True}, {**HERMES, "source_files": 1.0},
                    {**HERMES, "source_files": 100_001}, {**HERMES, "extra": "unbound"}]
        for hermes in variants:
            with self.subTest(hermes=hermes):
                bad = json.loads(json.dumps(run))
                bad["bindings"]["hermes"] = hermes
                bad["bindings_after_sha256"] = qa_receipt.digest(bad["bindings"])
                with self.assertRaisesRegex(ValueError, "Hermes launch/source identity"):
                    run_receipt("core", IDS["core"], [{"id": i} for i in IDS["core"]],
                                bad["bindings"], bad["bindings"], self.repo)
                core.write_text(json.dumps(bad))
                with self.assertRaisesRegex(ValueError, "Hermes launch/source identity"):
                    judgment_template(core)
                # Even independently forged PASS judgments cannot admit the malformed runtime.
                judge = core.with_name("forged-judgments.json")
                judge.write_text(json.dumps({"schema": qa_receipt.RECEIPT_SCHEMA, "suite": "core",
                    "run_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
                    "verdicts": {i: "PASS" for i in IDS["core"]}, "blocking_defects": []}))
                with self.assertRaisesRegex(ValueError, "Hermes launch/source identity"):
                    build_receipt(core, judge, reliability, self.judge(reliability), self.repo)
                bad_release = json.loads(json.dumps(release))
                bad_release["hermes"] = hermes
                with self.assertRaisesRegex(ValueError, "Hermes launch/source identity"):
                    check_receipt(bad_release, self.repo)

    def test_pending_verdict_is_neither_review_complete_nor_release(self):
        receipt = self.build(verdicts={"S07": "pending"})
        self.assertEqual((receipt["review_complete"], receipt["release_passed"]), (False, False))
        for release in (True, False):
            with self.assertRaisesRegex(ValueError, "pending"):
                check_receipt(receipt, self.repo, release=release)

    def test_needs_work_fail_and_blocking_defects_complete_review_but_block_release(self):
        for overrides in ({"verdicts": {"S01": "NEEDS_WORK"}}, {"verdicts": {"S48": "FAIL"}},
                          {"blocking_defects": ["private defect description"]}):
            with self.subTest(overrides=overrides):
                receipt = self.build(**overrides)
                self.assertNotIn("private defect", json.dumps(receipt))
                self.assertEqual(check_receipt(receipt, self.repo, release=False)["release_passed"], False)
                with self.assertRaisesRegex(ValueError, "Release not passed"):
                    check_receipt(receipt, self.repo)

    def test_partial_runs_and_missing_or_unknown_verdicts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "partial"):
            self.build(core=self.run_file("core", IDS["core"][:1]))
        core = self.run_file("core")
        verdicts = {i: "PASS" for i in IDS["core"][:47]}
        for bad in (verdicts, {**verdicts, "S48": "MAYBE"}):
            judgments = self.judge(core)
            value = json.loads(judgments.read_text())
            value["verdicts"] = bad
            judgments.write_text(json.dumps(value))
            reliability = self.run_file("reliability")
            with self.assertRaisesRegex(ValueError, "verdict"):
                build_receipt(core, judgments, reliability, self.judge(reliability), self.repo)

    def test_stale_or_drifting_source_and_foreign_judgments_are_rejected(self):
        receipt = self.build()
        self.write("processor/hermes_runner/prompt.py", "PROMPT = 2\n")
        with self.assertRaisesRegex(ValueError, "Stale"):
            check_receipt(receipt, self.repo)
        changed = {"source": dict(source_fingerprint(self.repo), sha256="0" * 64)}
        with self.assertRaisesRegex(ValueError, "source file identity"):
            self.build(core=self.run_file("core", after=changed))
        with self.assertRaisesRegex(ValueError, "different Hermes"):
            self.build(core=self.run_file("core", hermes={**HERMES, "launch_sha256": "0" * 64}))
        core, other = self.run_file("core"), self.out / "other-run.json"
        other.write_text(core.read_text() + "\n")
        reliability = self.run_file("reliability")
        with self.assertRaisesRegex(ValueError, "different run"):
            build_receipt(core, self.judge(other), reliability, self.judge(reliability), self.repo)

    def test_stored_pass_claim_must_match_verdicts(self):
        receipt = self.build(verdicts={"S02": "FAIL"})
        receipt["release_passed"] = True
        with self.assertRaisesRegex(ValueError, "does not match"):
            check_receipt(receipt, self.repo, release=False)

    def test_approved_kb_snapshot_must_match_across_suites_and_hold_during_each_run(self):
        overlay = self.out / "overlay.json"
        overlay.write_text(json.dumps({"policies/returns.md": {
            "file": "policies/returns.md", "category": "policies", "status": "confirmed",
            "title": "Returns", "heading": "", "text": "Returns within 45 days."}}))
        pinned = hashlib.sha256(overlay.read_bytes()).hexdigest()
        other = kb_snapshot("policies-only", policy_overlay=overlay, policy_overlay_sha256=pinned, repo=self.repo)
        self.assertEqual(other["policy_overlay_sha256"], pinned)
        with self.assertRaisesRegex(ValueError, "different approved KB snapshot"):
            self.build(core=self.run_file("core", snapshot=other))
        with self.assertRaisesRegex(ValueError, "different approved KB snapshot"):
            self.build(core=self.run_file("core", snapshot=kb_snapshot("fixture")))
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.build(core=self.run_file("core", after={"kb_snapshot": other}))
        overlay.write_text(overlay.read_text().replace("45", "60"))
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            kb_snapshot("policies-only", policy_overlay=overlay, policy_overlay_sha256=pinned, repo=self.repo)

    def test_observed_sections_may_differ_by_suite_but_content_must_not_change(self):
        def call(sha, name="policies/returns.md", heading="Window"):
            return {"tool": "kb_projection", "files": [name], "headings": [heading], "content_sha256": [sha]}
        receipt = self.build(core=self.run_file("core", kb_calls=[call("1" * 64)]),
                             reliability=self.run_file("reliability", kb_calls=[call("2" * 64, heading="Fees")]))
        self.assertEqual((receipt["suites"]["core"]["kb_observed_documents"],
                          receipt["suites"]["reliability"]["kb_observed_documents"]), (1, 1))
        with self.assertRaisesRegex(ValueError, "KB content changed during the run: policies/returns.md 'Window'"):
            self.build(core=self.run_file("core", kb_calls=[call("1" * 64), call("2" * 64)]))
        with self.assertRaisesRegex(ValueError, "differs between core and reliability"):
            self.build(core=self.run_file("core", kb_calls=[call("1" * 64)]),
                       reliability=self.run_file("reliability", kb_calls=[call("2" * 64)]))
        self.assertEqual(observed_kb([{"tool_calls": [call("1" * 64), {"tool": "search_kb"}]}]),
                         [("policies/returns.md", "Window", "1" * 64)])

    def test_kb_snapshot_reverifies_pinned_product_content(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from qa_catalog import snapshot
        products = self.out / "products"
        products.mkdir()
        product = products / "product-blue-shirt.md"
        product.write_text("---\ncategory: products\nstatus: confirmed\nsource: shopify-sync\n---\nBlue, $10\n")
        generator = self.out / "sync.py"
        generator.write_text("reviewed")
        manifest = self.out / "manifest.json"
        pinned = snapshot(products, generator, hashlib.sha256(b"reviewed").hexdigest(), manifest)["sha256"]
        first = kb_snapshot("policies-only", manifest, pinned)
        self.assertEqual(first, kb_snapshot("policies-only", manifest, pinned))
        product.write_text(product.read_text().replace("$10", "$12"))
        with self.assertRaisesRegex(ValueError, "Product content differs"):
            kb_snapshot("policies-only", manifest, pinned)

if __name__ == "__main__":
    unittest.main()
