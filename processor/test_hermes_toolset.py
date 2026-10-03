"""Tests for the Hermes tool allow-list (DEV-ISSUES #8).

The processor used to launch `hermes --yolo -z "..."`. --yolo skips approval
for EVERY tool call, and ~/.hermes/config.yaml grants the CLI platform the
`terminal` and `file` toolsets - so the only thing stopping a prompt-injected
ticket from running a shell command was convention.

These tests pin the replacement: an explicit toolset allow-list, no --yolo, and
an escape hatch that has to be switched on deliberately.

Nothing here executes Hermes. build_hermes_command() is a pure function.
"""

from __future__ import annotations

import json
import itertools
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROCESSOR_DIR = Path(__file__).resolve().parent
WEBHOOK_SRC = PROCESSOR_DIR.parent / "webhook" / "src"
sys.path[:0] = [str(PROCESSOR_DIR), str(WEBHOOK_SRC)]

from config import ProcessorSettings  # noqa: E402
from hermes_runner import build_hermes_command, process_ticket_with_hermes  # noqa: E402
from hermes_runner import runner  # noqa: E402

DEFAULT_TOOLSETS = "buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias"
TOKEN = "0123456789abcdef"


def tagged_output() -> str:
    return (
        f"<DRAFT:{TOKEN}>Hi! Your order ships in 24-48 hours.</DRAFT:{TOKEN}>\n"
        f"JSON_RESULT[{TOKEN}]: "
        + json.dumps(
            {
                "priority": "normal",
                "reason": "ok",
                "action": "drafted",
                "notify_owner": False,
                "gorgias_priority_set": False,
                "note_posted": False,
            },
            separators=(",", ":"),
        )
    )


def _settings(**overrides):
    base = {"hermes_toolsets": DEFAULT_TOOLSETS, "hermes_skip_approval": False,
            "job_timeout": 30}
    base.update(overrides)
    return SimpleNamespace(**base)


class CommandShapeTests(unittest.TestCase):
    def test_live_diagnostic_rejects_invalid_policy_before_any_operation(self):
        diagnostic = PROCESSOR_DIR / 'test_e2e.py'
        for tools in ('', 'buttonsbebe_kb', DEFAULT_TOOLSETS + ',terminal'):
            result = subprocess.run([sys.executable, '-c',
                f"import runpy; runpy.run_path({str(diagnostic)!r}, run_name='offline_permission_probe')"],
                env={'HERMES_TOOLSETS': tools, 'PYTHONDONTWRITEBYTECODE': '1'},
                capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('exactly the three approved read-only toolsets', result.stderr)
            self.assertEqual(result.stdout, '')

    def test_diagnostic_defaults_and_reordering_use_canonical_tools(self):
        diagnostic = PROCESSOR_DIR / 'test_e2e.py'
        for tools in (None, 'buttonsbebe_gorgias,buttonsbebe_redo,buttonsbebe_kb'):
            environment = {'PYTHONDONTWRITEBYTECODE': '1'}
            if tools is not None:
                environment['HERMES_TOOLSETS'] = tools
            result = subprocess.run([sys.executable, '-c',
                f"import json,runpy; n=runpy.run_path({str(diagnostic)!r}, run_name='offline_permission_probe'); print(json.dumps(n['HERMES_BASE_CMD']))"],
                env=environment, capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual(json.loads(result.stdout), ['hermes', '-t', DEFAULT_TOOLSETS])

    def test_default_command_has_no_yolo(self):
        cmd = build_hermes_command("hello", _settings())
        self.assertNotIn("--yolo", cmd)

    def test_default_command_passes_the_three_read_only_toolsets(self):
        cmd = build_hermes_command("hello", _settings())
        self.assertEqual(cmd[0], "hermes")
        self.assertIn("-t", cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], DEFAULT_TOOLSETS)
        self.assertEqual(cmd[-2:], ["-z", "hello"])

    def test_shell_and_file_toolsets_are_never_requested(self):
        joined = " ".join(build_hermes_command("hello", _settings()))
        for dangerous in ("terminal", "file", "code_execution", "browser",
                          "computer_use", "shell"):
            self.assertNotIn(dangerous, joined)

    def test_toolset_list_is_normalised(self):
        for names in itertools.permutations(DEFAULT_TOOLSETS.split(',')):
            cmd = build_hermes_command("hi", _settings(hermes_toolsets=" , ".join(names)))
            self.assertEqual(cmd[cmd.index("-t") + 1], DEFAULT_TOOLSETS)

    def test_invalid_toolsets_fail_closed(self):
        for invalid in ("", "mcp-a,mcp-b", DEFAULT_TOOLSETS + ",shell",
                        DEFAULT_TOOLSETS + ",", "buttonsbebe_kb" * 3):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_hermes_command("hi", _settings(hermes_toolsets=invalid))

    def test_prompt_is_always_the_final_argument(self):
        for toolsets in (DEFAULT_TOOLSETS,):
            for skip in (True, False):
                with self.subTest(toolsets=bool(toolsets), skip=skip):
                    cmd = build_hermes_command(
                        "the prompt",
                        _settings(hermes_toolsets=toolsets, hermes_skip_approval=skip))
                    self.assertEqual(cmd[-1], "the prompt")
                    self.assertEqual(cmd[-2], "-z")


class EscapeHatchTests(unittest.TestCase):
    def test_yolo_only_appears_when_explicitly_enabled(self):
        cmd = build_hermes_command("hi", _settings(hermes_skip_approval=True))
        self.assertIn("--yolo", cmd)
        # even then, the allow-list still applies
        self.assertEqual(cmd[cmd.index("-t") + 1], DEFAULT_TOOLSETS)

    def test_enabling_the_escape_hatch_is_logged_as_a_warning(self):
        with patch.object(runner, "log_event") as log_event:
            build_hermes_command("hi", _settings(hermes_skip_approval=True))
        self.assertTrue(log_event.called)
        level = log_event.call_args[0][1]
        self.assertEqual(level, "WARNING")


class DefaultsTests(unittest.TestCase):
    def test_shipped_defaults_are_the_locked_down_ones(self):
        # Read the real Settings defaults, not the test doubles above, so a
        # careless edit to config.py fails here.
        settings = ProcessorSettings(DEMO_MODE=False)
        self.assertEqual(settings.hermes_toolsets, DEFAULT_TOOLSETS)
        self.assertFalse(settings.hermes_skip_approval)


class RunnerIntegrationTests(unittest.TestCase):
    def test_the_runner_actually_uses_the_allow_list(self):
        with patch.object(
            runner, "get_settings", return_value=_settings()
        ), patch.object(
            runner, "_make_run_token", return_value=TOKEN
        ), patch.object(
            runner,
            "run_bounded",
            return_value=SimpleNamespace(
                returncode=0, stderr="", stdout=tagged_output()
            ),
        ) as run:
            process_ticket_with_hermes(
                ticket_id=1,
                message_text="Where is my order?",
                ticket_subject="WISMO",
                customer_email="c@example.com",
                intents=[],
            )
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "hermes")
        self.assertNotIn("--yolo", cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], DEFAULT_TOOLSETS)

    def test_invalid_policy_does_not_launch_a_model(self):
        with patch.object(runner, "get_settings", return_value=_settings(hermes_toolsets="")), \
             patch.object(runner, "run_bounded") as run:
            result = process_ticket_with_hermes(1, "Where is my order?", "Order", "test@example.test", [])
        self.assertEqual(result['generation_state'], 'failed')
        self.assertTrue(result['no_draft'])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
