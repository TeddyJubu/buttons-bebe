#!/usr/bin/env python3
"""Run all Inbox browser regressions against an owned synthetic loopback preview."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


STARTUP_TIMEOUT_SECONDS = 15
PORT_ATTEMPTS = 5
TEST_TIMEOUT_SECONDS = 120
REQUIRED_BROWSER_SUITES = (
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
FIXTURE_RUNTIME_HELPER = "fixture-runtime.mjs"


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def required_browser_files(tests_dir: Path) -> tuple[list[Path], Path]:
    """Require every named regression before any browser or preview is started."""
    missing = [name for name in REQUIRED_BROWSER_SUITES if not (tests_dir / name).is_file()]
    if missing:
        raise FileNotFoundError("required Inbox browser suites are missing: " + ", ".join(missing))
    helper = tests_dir / FIXTURE_RUNTIME_HELPER
    if not helper.is_file():
        raise FileNotFoundError(f"required full-stack Inbox fixture helper is missing: {helper.name}")
    return [tests_dir / name for name in REQUIRED_BROWSER_SUITES], helper


def is_loopback_bind_collision(output: str) -> bool:
    """Recognize common Unix and Windows address-in-use diagnostics only."""
    message = output.casefold()
    return any(marker in message for marker in (
        "address already in use",
        "winerror 10048",
        "only one usage of each socket address",
        "wsaeaddrinuse",
    ))


def browser_cache_path() -> str:
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured:
        return configured
    if sys.platform == "darwin":
        return str(Path.home() / "Library" / "Caches" / "ms-playwright")
    if os.name == "nt":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return str(local_app_data / "ms-playwright")
    return str(Path.home() / ".cache" / "ms-playwright")


def isolated_environment(repo: Path, scratch: Path, preview_url: str = "") -> dict[str, str]:
    """Pass only runtime basics; tests do not inherit provider credentials or env files."""
    env: dict[str, str] = {}
    for name in ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL"):
        if name in os.environ:
            env[name] = os.environ[name]
    env["HOME"] = str(scratch / "home")
    env["TMPDIR"] = str(scratch)
    env["TMP"] = str(scratch)
    env["TEMP"] = str(scratch)
    env["XDG_CONFIG_HOME"] = str(scratch / "config")
    env["XDG_CACHE_HOME"] = str(scratch / "cache")
    env["PLAYWRIGHT_BROWSERS_PATH"] = browser_cache_path()
    inbox_python = os.environ.get("INBOX_PYTHON", sys.executable)
    if not Path(inbox_python).is_absolute() and len(Path(inbox_python).parts) > 1:
        inbox_python = str(repo / inbox_python)
    env["INBOX_TEST_PYTHON"] = inbox_python

    configured_module = os.environ.get("PLAYWRIGHT_MODULE")
    if configured_module:
        module = Path(configured_module)
        if not module.is_absolute():
            module = (repo / module).resolve()
    else:
        module = repo / "console-src" / "node_modules" / "playwright"
    env["PLAYWRIGHT_MODULE"] = str(module)
    evidence_dir = os.environ.get("INBOX_EVIDENCE_DIR")
    if evidence_dir:
        evidence_path = Path(evidence_dir)
        if not evidence_path.is_absolute():
            evidence_path = (repo / evidence_path).resolve()
        env["INBOX_EVIDENCE_DIR"] = str(evidence_path)
    else:
        env["INBOX_EVIDENCE_DIR"] = str(scratch / "evidence")
    if preview_url:
        env["INBOX_TEST_URL"] = preview_url
    return env


def verify_playwright(node: str, env: dict[str, str], repo: Path) -> None:
    module = Path(env["PLAYWRIGHT_MODULE"])
    if not module.exists():
        raise RuntimeError(
            f"Playwright is required but missing at {module}; run npm ci --prefix console-src"
        )
    smoke = (
        "const {chromium}=require(process.env.PLAYWRIGHT_MODULE);"
        "(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});"
        "await browser.close();})().catch(error=>{console.error(error.stack||error);process.exit(1);});"
    )
    result = subprocess.run([node, "-e", smoke], cwd=repo, env=env, check=False)
    if result.returncode:
        raise RuntimeError(
            "Playwright Chromium is required but could not launch; install the locked "
            "browser with console-src/node_modules/.bin/playwright install chromium"
        )


def wait_for_preview(process: subprocess.Popen[bytes], health_url: str) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with build_opener(ProxyHandler({})).open(health_url, timeout=1) as response:
                body = json.loads(response.read())
            if body == {"ok": True, "synthetic": True, "readOnly": True}:
                return True
        except (OSError, URLError, json.JSONDecodeError, TimeoutError):
            pass
        time.sleep(0.1)
    return False


def stop_preview(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_browser_test(node: str, test: Path, repo: Path, env: dict[str, str]) -> int:
    options = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    }
    process = subprocess.Popen([node, str(test)], cwd=repo, env=env, **options)
    try:
        return process.wait(timeout=TEST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)
        raise TimeoutError(f"{test.name} exceeded {TEST_TIMEOUT_SECONDS} seconds") from error


@contextmanager
def running_preview(repo: Path, helper: Path, env: dict[str, str], scratch: Path):
    log_path = scratch / "preview.log"
    for attempt in range(PORT_ATTEMPTS):
        port = free_loopback_port()
        log_handle = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                str(helper),
                "--repo",
                str(repo),
                "--port",
                str(port),
            ],
            cwd=repo,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            if wait_for_preview(process, f"{base_url}/health"):
                yield process, log_handle, base_url
                return

            log_handle.flush()
            output = log_path.read_text(encoding="utf-8", errors="replace")
            if is_loopback_bind_collision(output):
                if attempt + 1 < PORT_ATTEMPTS:
                    continue
                raise RuntimeError("Could not reserve a loopback port for the synthetic Inbox preview after bounded retries")
            raise RuntimeError(
                "The synthetic Inbox preview did not become ready on loopback. "
                f"Preview output: {output.strip() or '(empty)'}"
            )
        finally:
            stop_preview(process)
            log_handle.flush()
            log_handle.close()
    raise RuntimeError("Could not reserve a loopback port for the synthetic Inbox preview")


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    tests_dir = repo / "console-src" / "inbox2" / "tests"
    helper = repo / "skills" / "buttonsbebe-support-webapp" / "scripts" / "serve_inbox_preview.py"
    try:
        tests, fixture_helper = required_browser_files(tests_dir)
    except OSError as error:
        print(f"inbox browser gate failed: {error}", file=sys.stderr)
        return 1
    node = shutil.which("node")
    if not node:
        print("inbox browser gate failed: Node.js is required", file=sys.stderr)
        return 1
    if not helper.is_file():
        print(f"inbox browser gate failed: preview helper is missing: {helper}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="inbox-browser-gate-") as scratch_name:
        scratch = Path(scratch_name)
        (scratch / "home").mkdir()
        (scratch / "config").mkdir()
        (scratch / "cache").mkdir()
        env = isolated_environment(repo, scratch)
        try:
            verify_playwright(node, env, repo)
        except (OSError, RuntimeError) as error:
            print(f"inbox browser gate failed: {error}", file=sys.stderr)
            return 1

        failures = []
        try:
            with running_preview(repo, helper, env, scratch) as (_, _, base_url):
                env["INBOX_TEST_URL"] = base_url
                print(f"inbox browser gate: synthetic preview ready at {base_url}")
                print(f"inbox browser gate: running all {len(tests)} required browser suites")
                for test in tests:
                    print(f"\n--- {test.relative_to(repo)} ---", flush=True)
                    try:
                        return_code = run_browser_test(node, test, repo, env)
                        if return_code:
                            failures.append(f"{test.name} exited with status {return_code}")
                    except TimeoutError as error:
                        failures.append(str(error))
                    except OSError as error:
                        failures.append(f"{test.name} could not start: {error}")
        except (OSError, RuntimeError) as error:
            print(f"inbox browser gate failed: {error}", file=sys.stderr)
            return 1

        print("\ninbox browser gate: running full-stack fixture helper separately", flush=True)
        try:
            helper_result = run_browser_test(node, fixture_helper, repo, env)
            if helper_result:
                failures.append(f"{FIXTURE_RUNTIME_HELPER} exited with status {helper_result}")
        except TimeoutError as error:
            failures.append(str(error))
        except OSError as error:
            failures.append(f"{FIXTURE_RUNTIME_HELPER} could not start: {error}")

        if failures:
            for failure in failures:
                print(f"inbox browser gate failed: {failure}", file=sys.stderr)
            return 1
        if "INBOX_EVIDENCE_DIR" not in os.environ:
            print("inbox browser gate: temporary screenshots were removed after the run")
        print(f"inbox browser gate passed: all {len(tests)} required suites and the separate fixture helper completed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
