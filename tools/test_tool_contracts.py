from __future__ import annotations

import ast
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


TOOLS_DIR = Path(__file__).resolve().parent


def mcp_tools(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                names.add(node.name)
    return names


def load_function(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    namespace: dict = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class ToolContractTests(unittest.TestCase):
    def test_redo_source_matches_installed_four_tool_contract(self) -> None:
        self.assertEqual(
            mcp_tools(TOOLS_DIR / "redo_mcp.py"),
            {"list_recent_returns", "get_returns_for_order", "get_return", "get_order"},
        )

    def test_redo_trim_preserves_current_refund_and_tracking_schema(self) -> None:
        trim = load_function(TOOLS_DIR / "redo_mcp.py", "_trim")
        fixture = {
            "id": "return-1",
            "status": "processing",
            "createdAt": "2026-07-13T00:00:00Z",
            "updatedAt": "2026-07-13T01:00:00Z",
            "completeWithNoAction": False,
            "order": {"id": "order-1", "name": "12345"},
            "compensationMethods": [{"type": "refund"}],
            "refunds": [{"amount": "10.00"}],
            "totals": {"refund": "10.00", "storeCredit": "0.00"},
            "shipments": [{"trackingNumber": "TRACK", "trackingUrl": "https://carrier.test"}],
            "exchange": {"itemCount": 0},
            "giftCards": [],
            "items": [{"id": "item-1", "status": "received"}],
            "source": {"emailAddress": "customer@example.test"},
        }
        result = trim(fixture)
        self.assertEqual(result["created_at"], fixture["createdAt"])
        self.assertEqual(result["updated_at"], fixture["updatedAt"])
        self.assertEqual(result["order_name"], "12345")
        self.assertEqual(result["compensation_methods"], fixture["compensationMethods"])
        self.assertEqual(result["refunds"], fixture["refunds"])
        self.assertEqual(result["totals"], fixture["totals"])
        self.assertEqual(result["shipments"], fixture["shipments"])
        self.assertEqual(result["exchange"], fixture["exchange"])
        self.assertNotIn("source", result)

    def test_redo_path_segments_never_escape_their_url_segment(self) -> None:
        # Report 05, action 6: `../`-style ids must raise ValueError before
        # any _get call, mirroring the Gorgias cursor guard's shape.
        # Exec'd with a hand-built scope because importing redo_mcp would
        # read .env at import time (mirrors test_gorgias_read_contract.py).
        source = TOOLS_DIR / "redo_mcp.py"
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        selected = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in {"_segment", "get_return", "get_order"}
        ]
        for node in selected:
            node.decorator_list = []
        get = Mock()
        scope = {"re": re, "_get": get, "_trim": lambda x: x}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), scope)

        segment = scope["_segment"]
        for bad in ("../orders", "a/b", "a b", "a?x=1", "a#f", "#", "", "x" * 65, None, 123, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                segment(bad, "order_name")
        self.assertEqual(segment("abc-DEF_123", "order_name"), "abc-DEF_123")

        for tool in ("get_return", "get_order"):
            for bad in ("../orders", "x" * 65, "12345;DROP", "%2e%2e"):
                with self.subTest(tool=tool, bad=bad), self.assertRaises(ValueError):
                    scope[tool](bad)
        get.assert_not_called()

    def test_gorgias_source_matches_installed_six_tool_contract(self) -> None:
        self.assertEqual(
            mcp_tools(TOOLS_DIR / "gorgias_mcp.py"),
            {
                "list_recent_tickets",
                "list_inbox_tickets",
                "get_ticket",
                "get_ticket_messages",
                "get_customer",
                "search_customer",
            },
        )


class DemoReleaseGateTests(unittest.TestCase):
    """Exercise the release gate's demo branch without running the full gate."""

    ROOT = TOOLS_DIR.parent
    VERIFY_RELEASE = TOOLS_DIR / "verify_release.sh"
    DEMO_VERIFY = ROOT / "demo" / "verify_config.py"
    DEMO_ENV = ROOT / "demo" / ".env.example"

    def _fake_toolchain(
        self,
        directory: Path,
        demo_exit_code: int | None = None,
    ) -> tuple[Path, Path, Path]:
        """Return fake Python, node, and rg commands for the shell gate.

        The release gate is intentionally broad and its real test suites are
        covered elsewhere. These fakes let this contract test execute the
        actual shell branch quickly while recording whether the demo verifier
        was invoked.
        """

        log_path = directory / "python-invocations.log"
        python_path = directory / "python"
        demo_action = (
            f"    raise SystemExit({demo_exit_code})\n"
            if demo_exit_code is not None
            else "    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
        )
        python_path.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "import pathlib\n"
            "import sys\n"
            f"log = pathlib.Path({str(log_path)!r})\n"
            "with log.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(repr(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1:] == ['demo/verify_config.py', 'demo/.env.example']:\n"
            + demo_action
            + "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        node_path = directory / "node"
        node_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        rg_path = directory / "rg"
        rg_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        for path in (python_path, node_path, rg_path):
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return python_path, node_path, log_path

    def _run_gate(
        self,
        demo_mode: str | None,
        demo_exit_code: int | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory(prefix="release-gate-demo-") as temp_dir:
            temp = Path(temp_dir)
            fake_python, _node, log_path = self._fake_toolchain(temp, demo_exit_code)
            env = os.environ.copy()
            env.pop("DEMO_MODE", None)
            if demo_mode is not None:
                env["DEMO_MODE"] = demo_mode
            # Every interpreter is exported by the outer gate. Keep this shell
            # contract test isolated from its caller's real environments.
            for name in ("PYTHON", "PROCESSOR_PYTHON", "WEBHOOK_PYTHON", "INBOX_PYTHON", "QA_PYTHON", "HERMES_VERIFY_PYTHON"):
                env[name] = str(fake_python)
            env["PATH"] = f"{temp}:{env['PATH']}"
            result = subprocess.run(
                ["bash", str(self.VERIFY_RELEASE)],
                cwd=self.ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            invocations = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            return result, invocations

    def test_default_release_gate_does_not_run_demo_verifier(self) -> None:
        result, invocations = self._run_gate(demo_mode=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("demo/verify_config.py", invocations)

    def test_demo_mode_runs_checked_in_profile(self) -> None:
        result, invocations = self._run_gate(demo_mode="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("demo/verify_config.py", invocations)
        self.assertIn("demo/.env.example", invocations)

    def test_demo_verifier_failure_blocks_release_gate(self) -> None:
        result, invocations = self._run_gate(demo_mode="1", demo_exit_code=17)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("demo/verify_config.py", invocations)
        self.assertIn("demo isolation verification failed", result.stderr)

    def test_invalid_demo_mode_is_rejected_before_gate(self) -> None:
        for invalid_mode in ("0", "true", "yes"):
            with self.subTest(invalid_mode=invalid_mode):
                result, invocations = self._run_gate(demo_mode=invalid_mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DEMO_MODE must be unset or 1", result.stderr)
                self.assertNotIn("demo/verify_config.py", invocations)

    def test_release_gate_uses_only_canonical_profile_without_sourcing(self) -> None:
        script = self.VERIFY_RELEASE.read_text(encoding="utf-8")
        self.assertIn('demo_env="demo/.env.example"', script)
        self.assertEqual(script.count("demo/.env.example"), 1)
        self.assertIn("unset DEMO_MODE", script)
        self.assertNotIn("DEMO_ENV_FILE", script)
        self.assertNotIn("demo/.env", script.replace("demo/.env.example", ""))
        self.assertNotRegex(script, r"(?:^|[;&|])\s*(?:source|\.)\s+[^\n]*demo/\.env(?:[\"'\s]|$)")
        self.assertNotIn('"demo/.env"', script)

    def test_demo_verifier_rejects_non_demo_database_path(self) -> None:
        valid_profile = self.DEMO_ENV.read_text(encoding="utf-8")
        invalid_profile = valid_profile.replace(
            "WEBHOOK_DB_PATH=./data/cute-things-demo-webhook.db",
            "WEBHOOK_DB_PATH=./data/webhook.db",
        )
        with tempfile.TemporaryDirectory(prefix="invalid-demo-profile-") as temp_dir:
            profile = Path(temp_dir) / ".env"
            profile.write_text(invalid_profile, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(self.DEMO_VERIFY), str(profile)],
                cwd=self.ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("WEBHOOK_DB_PATH", result.stdout)


if __name__ == "__main__":
    unittest.main()
