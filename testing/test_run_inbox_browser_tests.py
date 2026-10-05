from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_inbox_browser_tests as browser_runner


REQUIRED_SUITES = (
    "conversation-state.mjs",
    "customer-loading.mjs",
    "draft-recovery.mjs",
    "layout.mjs",
    "local-controls.mjs",
    "local-state.mjs",
    "redo-details.mjs",
    "rewrite-draft.mjs",
    "send-access.mjs",
    "ticket-views.mjs",
)
FIXTURE_HELPER = "fixture-runtime.mjs"


class BrowserGateManifestTests(unittest.TestCase):
    def make_repo(self, *, missing_suite: str | None = None, fixture_helper: bool = True) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="inbox-browser-manifest-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "testing").mkdir()
        tests_dir = root / "console-src" / "inbox2" / "tests"
        tests_dir.mkdir(parents=True)
        for name in REQUIRED_SUITES:
            if name != missing_suite:
                (tests_dir / name).write_text("// synthetic browser suite stub\n", encoding="utf-8")
        if fixture_helper:
            (tests_dir / FIXTURE_HELPER).write_text("// synthetic full-stack helper stub\n", encoding="utf-8")
        preview_helper = root / "skills" / "buttonsbebe-support-webapp" / "scripts" / "serve_inbox_preview.py"
        preview_helper.parent.mkdir(parents=True)
        preview_helper.write_text("# synthetic preview helper stub\n", encoding="utf-8")
        runner_path = root / "testing" / "run_inbox_browser_tests.py"
        runner_path.write_text("# module path anchor\n", encoding="utf-8")
        return root, runner_path

    def invoke_main(self, root: Path, runner_path: Path) -> tuple[int, str, str, dict[str, object]]:
        @contextmanager
        def fake_preview(*args, **kwargs):
            yield object(), io.StringIO(), "http://127.0.0.1:18888"

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(browser_runner, "__file__", str(runner_path)),
            patch.object(browser_runner.shutil, "which", return_value="/synthetic/node") as which,
            patch.object(browser_runner, "verify_playwright") as verify,
            patch.object(browser_runner, "running_preview", side_effect=fake_preview) as preview,
            patch.object(browser_runner, "run_browser_test", return_value=0) as run_test,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = browser_runner.main()
        calls: dict[str, object] = {"which": which, "verify": verify, "preview": preview, "run_test": run_test}
        return result, stdout.getvalue(), stderr.getvalue(), calls

    def test_each_required_suite_is_refused_before_playwright_or_preview_starts(self):
        for missing in REQUIRED_SUITES:
            with self.subTest(missing=missing):
                root, runner_path = self.make_repo(missing_suite=missing)
                result, _stdout, stderr, calls = self.invoke_main(root, runner_path)
                self.assertEqual(result, 1)
                self.assertIn(missing, stderr)
                calls["verify"].assert_not_called()
                calls["preview"].assert_not_called()
                calls["run_test"].assert_not_called()

    def test_fixture_runtime_is_required_but_reported_as_separate_helper(self):
        root, runner_path = self.make_repo()
        result, stdout, _stderr, calls = self.invoke_main(root, runner_path)
        self.assertEqual(result, 0)
        self.assertIn("running all 10 required browser suites", stdout)
        self.assertIn("running full-stack fixture helper separately", stdout)
        names = [Path(call.args[1]).name for call in calls["run_test"].call_args_list]
        self.assertCountEqual(names[:10], REQUIRED_SUITES)
        self.assertEqual(names[10:], [FIXTURE_HELPER])

    def test_missing_fixture_runtime_helper_is_refused_before_playwright_or_preview(self):
        root, runner_path = self.make_repo(fixture_helper=False)
        result, _stdout, stderr, calls = self.invoke_main(root, runner_path)
        self.assertEqual(result, 1)
        self.assertIn(FIXTURE_HELPER, stderr)
        calls["verify"].assert_not_called()
        calls["preview"].assert_not_called()


class BrowserPortRetryTests(unittest.TestCase):
    class ExitedPreview:
        def __init__(self, message: str, stdout) -> None:
            stdout.write(message + "\n")
            stdout.flush()

        @staticmethod
        def poll() -> int:
            return 1

    def test_windows_10048_collision_retries_only_within_the_existing_bound(self):
        with tempfile.TemporaryDirectory(prefix="inbox-browser-port-retry-") as scratch_name:
            scratch = Path(scratch_name)
            root = scratch / "repo"
            root.mkdir()
            helper = root / "serve.py"
            helper.write_text("# no process is started by this test\n", encoding="utf-8")
            windows_collision = "OSError: [WinError 10048] Only one usage of each socket address is normally permitted"
            with (
                patch.object(browser_runner, "free_loopback_port", side_effect=range(31001, 31001 + browser_runner.PORT_ATTEMPTS)),
                patch.object(browser_runner.subprocess, "Popen",
                             side_effect=lambda *args, **kwargs: self.ExitedPreview(windows_collision, kwargs["stdout"])) as start,
                patch.object(browser_runner, "wait_for_preview", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "Could not reserve a loopback port"):
                    with browser_runner.running_preview(root, helper, {}, scratch):
                        self.fail("an exited colliding preview cannot be yielded")
            self.assertEqual(start.call_count, browser_runner.PORT_ATTEMPTS)

    def test_non_collision_startup_error_is_not_retried(self):
        with tempfile.TemporaryDirectory(prefix="inbox-browser-port-error-") as scratch_name:
            scratch = Path(scratch_name)
            root = scratch / "repo"
            root.mkdir()
            helper = root / "serve.py"
            helper.write_text("# no process is started by this test\n", encoding="utf-8")
            with (
                patch.object(browser_runner, "free_loopback_port", return_value=31001),
                patch.object(browser_runner.subprocess, "Popen",
                             side_effect=lambda *args, **kwargs: self.ExitedPreview(
                                 "OSError: [WinError 5] Access is denied", kwargs["stdout"])) as start,
                patch.object(browser_runner, "wait_for_preview", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                    with browser_runner.running_preview(root, helper, {}, scratch):
                        self.fail("a failed preview cannot be yielded")
            self.assertEqual(start.call_count, 1)


if __name__ == "__main__":
    unittest.main()
