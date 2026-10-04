import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import qa_receipt
from qa_receipt import (build_receipt, catalog, check_receipt, judgment_template, kb_snapshot, observed_kb, run_receipt,
                        source_fingerprint, model_runtime_identity, check_run_integrity)
from qa_harness import profile_config
from qa_safety import GROUPS

HERMES = {"executable_sha256": "e" * 64, "source_sha256": "f" * 64, "source_files": 1}
POLICIES = kb_snapshot("policies-only")
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
        for name, text in {"processor/hermes_runner/prompt.py": "PROMPT = 1\n", "processor/draft_cleaner.py": "",
                           "processor/orchestrator.py": "", "webhook/src/bb_webhook/app.py": "",
                           "kb/scripts/search_kb.py": "", "testing/qa_harness.py": "", "testing/test_qa_harness.py": "",
                           "kb/policies/returns.md": "Returns within 30 days.\n"}.items():
            self.write(name, text)
        self.write("testing/scenarios.json", json.dumps([{"id": i} for i in IDS["core"]]))
        self.write("testing/reliability-scenarios.json", json.dumps([{"id": i} for i in IDS["reliability"]]))
        git = patch.object(qa_receipt, "_git", return_value=("a" * 40, False))
        git.start()
        self.addCleanup(git.stop)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_file(self, suite, ids=None, after=None, hermes=HERMES, snapshot=POLICIES, kb_calls=(), model_runtime=None):
        ids = IDS[suite] if ids is None else ids
        before = {"source": source_fingerprint(self.repo), "hermes": hermes, "kb_snapshot": snapshot,
                  "model_runtime": self.model_runtime if model_runtime is None else model_runtime}
        results = [{"id": i, "tool_calls": list(kb_calls) if n == 0 else []} for n, i in enumerate(ids)]
        receipt = run_receipt(suite, ids, results, before, {**before, **(after or {})}, self.repo)
        path = self.out / f"{suite}-run.json"
        path.write_text(json.dumps(receipt))
        return path

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
        for name in ("processor/orchestrator.py", "webhook/src/bb_webhook/message_times.py",
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
        with self.assertRaisesRegex(ValueError, "different model/runtime"):
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
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.build(core=self.run_file("core", after=changed))
        with self.assertRaisesRegex(ValueError, "different Hermes"):
            self.build(core=self.run_file("core", hermes={**HERMES, "executable_sha256": "0" * 64}))
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
