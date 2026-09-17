"""Tests for tools/verify_hermes_toolset.sh — the pre-deploy safety check.

This script's whole job is to prove the Hermes tool lockdown is real before
anyone restarts the processor. Code review found it reported "All checks
passed - safe to restart" in several situations where it had proved nothing:

  * Hermes failing with a connection error (it only grepped for three
    specific failure strings and threw the exit status away).
  * Six of eight realistic ~/.hermes/config.yaml layouts that DID grant the
    shell and file toolsets (the awk parser recognised exactly one shape).
  * And it ran a live root agent even after reporting that the lockdown might
    not be real.

Everything here is offline: `hermes` is a stub on PATH.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "verify_hermes_toolset.sh"

_GOOD_LIST = "buttonsbebe_kb\nbuttonsbebe_redo\nbuttonsbebe_gorgias\n"

_HERMES_STUB = """#!/bin/sh
if [ "$1" = "mcp" ] && [ "$2" = "list" ]; then
    printf '%s' "$FAKE_MCP_LIST"
    exit "${FAKE_MCP_STATUS:-0}"
fi
printf '%s\\n' "$FAKE_SMOKE_OUT"
exit "${FAKE_SMOKE_STATUS:-0}"
"""


class VerifyToolsetScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        stub = self.bin / "hermes"
        stub.write_text(_HERMES_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.addCleanup(self._tmp.cleanup)

    def run_script(self, *, mcp_list: str = _GOOD_LIST, mcp_status: int = 0,
                   smoke_out: str = "KBOK: returns accepted within 7 days.",
                   smoke_status: int = 0, config: str | None = None,
                   toolsets: str | None = None,
                   hermes_home: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HERMES_VERIFY_PYTHON"] = sys.executable
        env["PATH"] = f"{self.bin}:{env.get('PATH', '')}"
        env["FAKE_MCP_LIST"] = mcp_list
        env["FAKE_MCP_STATUS"] = str(mcp_status)
        env["FAKE_SMOKE_OUT"] = smoke_out
        env["FAKE_SMOKE_STATUS"] = str(smoke_status)
        if toolsets is not None:
            env["HERMES_TOOLSETS"] = toolsets
        if hermes_home is not None:
            env["HERMES_HOME"] = str(hermes_home)
        else:
            env["HERMES_HOME"] = str(self.tmp / "absent-hermes-home")
        if config is None:
            env["HERMES_CONFIG"] = str(self.tmp / "absent.yaml")
        else:
            path = self.tmp / "config.yaml"
            path.write_text(textwrap.dedent(config), encoding="utf-8")
            env["HERMES_CONFIG"] = str(path)
        return subprocess.run(["bash", str(SCRIPT)], env=env,
                              capture_output=True, text=True, timeout=120)

    # ── the happy path still passes ─────────────────────────────────────
    def test_a_correct_setup_passes(self):
        proc = self.run_script(config="platform_toolsets:\n  cli: []\n")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("All checks passed", proc.stdout)

    def test_prepared_interpreter_is_used_instead_of_path_python3(self):
        shadow=self.bin / "python3"
        shadow.write_text("#!/bin/sh\necho 'wrong interpreter' >&2\nexit 91\n")
        shadow.chmod(0o755)
        proc=self.run_script(config="platform_toolsets:\n  cli: []\n")
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertNotIn("wrong interpreter",proc.stdout+proc.stderr)

    # ── failures that used to read as a pass ────────────────────────────
    def test_a_hermes_connection_error_is_not_a_pass(self):
        proc = self.run_script(
            smoke_out="error: failed to connect to MCP server buttonsbebe_kb: "
                      "connection refused",
            smoke_status=1,
            config="platform_toolsets:\n  cli: []\n")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Do NOT restart", proc.stdout)

    def test_a_reply_without_the_positive_token_is_not_a_pass(self):
        # No error string present, but the KB clearly did not answer.
        proc = self.run_script(smoke_out="I do not have access to any tools.",
                               config="platform_toolsets:\n  cli: []\n")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("did not answer", proc.stdout)

    def test_mcp_list_failing_is_not_a_pass(self):
        proc = self.run_script(mcp_list="error: cannot reach daemon",
                               mcp_status=1)
        self.assertNotEqual(proc.returncode, 0)

    def test_a_misspelled_toolset_is_caught(self):
        proc = self.run_script(toolsets="buttonsbebe_kb,buttonsbebe_reddo")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SILENTLY LOSE THE TOOL", proc.stdout)

    def test_a_prefix_lookalike_server_is_not_accepted(self):
        proc = self.run_script(mcp_list="buttonsbebe_kb_old\nbuttonsbebe_redo\n"
                                        "buttonsbebe_gorgias\n")
        self.assertNotEqual(proc.returncode, 0)

    # ── the YAML shapes the awk version waved through ───────────────────
    DANGEROUS = {
        "two-space": "platform_toolsets:\n  cli:\n    - terminal\n    - file\n",
        "four-space": "platform_toolsets:\n    cli:\n        - terminal\n",
        "inline-list": "platform_toolsets:\n  cli: [terminal, file]\n",
        "quoted": 'platform_toolsets:\n  cli:\n    - "terminal"\n',
        "trailing-comment": "platform_toolsets:\n  cli:\n    - terminal   # needed\n",
        "nested": ("agents:\n  default:\n    platform_toolsets:\n"
                   "      cli:\n        - terminal\n"),
        "other-keys-first": ("model:\n  default: glm-5.2\n"
                             "platform_toolsets:\n  telegram:\n    - hermes-telegram\n"
                             "  cli:\n    - file\n"),
    }

    def test_every_dangerous_config_shape_is_reported(self):
        for label, config in self.DANGEROUS.items():
            with self.subTest(shape=label):
                proc = self.run_script(config=config)
                self.assertNotEqual(proc.returncode, 0,
                                    f"{label} reported OK: {proc.stdout}")
                self.assertIn("shell/file tools", proc.stdout)

    SAFE = {
        "empty-list": "platform_toolsets:\n  cli: []\n",
        "no-cli-key": "platform_toolsets:\n  telegram:\n    - hermes-telegram\n",
        "harmless-tools": "platform_toolsets:\n  cli:\n    - skills\n    - todo\n",
        "no-platform-key": "model:\n  default: glm-5.2\n",
    }

    def test_safe_config_shapes_pass(self):
        for label, config in self.SAFE.items():
            with self.subTest(shape=label):
                proc = self.run_script(config=config)
                self.assertEqual(proc.returncode, 0,
                                 f"{label} reported a failure: {proc.stdout}")

    # ── the live step must not run under an unproven lockdown ───────────
    def test_the_live_agent_is_not_launched_after_a_failure(self):
        proc = self.run_script(config=self.DANGEROUS["two-space"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("skipping the live run", proc.stdout)
        self.assertNotIn("KBOK", proc.stdout)

    # ── the hermes home mirror check ────────────────────────────────────
    def _mirror_home(self, soul_text: str | None = None) -> Path:
        import shutil
        home = self.tmp / "live-hermes"
        home.mkdir(exist_ok=True)
        repo = Path(__file__).resolve().parents[2] / "hermes"
        shutil.copytree(repo, home, dirs_exist_ok=True)
        # Live-only files (config, credentials) must not fail the check —
        # all of them, not just config.yaml: auth.json and .env are exactly
        # the files a live install holds and the repo mirror must not.
        (home / "config.yaml").write_text("model:\n  default: glm-5.2\n", encoding="utf-8")
        (home / "auth.json").write_text('{"api_key": "sk-live"}\n', encoding="utf-8")
        (home / ".env").write_text("HERMES_API_KEY=sk-live\n", encoding="utf-8")
        if soul_text is not None:
            (home / "SOUL.md").write_text(soul_text, encoding="utf-8")
        return home

    def test_matching_hermes_home_passes(self):
        proc = self.run_script(config="platform_toolsets:\n  cli: []\n",
                               hermes_home=self._mirror_home())
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("matches the repo mirror", proc.stdout)

    def test_live_only_credentials_do_not_mask_content_drift(self):
        # auth.json/.env in the live home must be ignored as live-only state,
        # but they must not blind the check to a real SOUL.md drift.
        proc = self.run_script(config="platform_toolsets:\n  cli: []\n",
                               hermes_home=self._mirror_home("drifted brain\n"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("drifted", proc.stdout)
        self.assertIn("runbook", proc.stdout)

    def test_drifted_soul_fails_with_runbook_pointer(self):
        proc = self.run_script(config="platform_toolsets:\n  cli: []\n",
                               hermes_home=self._mirror_home("drifted brain instructions\n"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("drifted", proc.stdout)
        self.assertIn("runbook", proc.stdout)

    def test_missing_hermes_home_skips_without_failing(self):
        proc = self.run_script(config="platform_toolsets:\n  cli: []\n")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("skipping", proc.stdout.lower())


if __name__ == "__main__":
    unittest.main()
