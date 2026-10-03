#!/usr/bin/env python3
"""Run every required Inbox browser suite against this checkout's own preview.

The synthetic loopback preview is started on a free port. It is accepted only
after that spawned process prints its ready line and answers /health while
still alive, so a preview from another checkout can never satisfy the gate.
The exact process is stopped and reaped on every exit path.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "skills/buttonsbebe-support-webapp/scripts/serve_inbox_preview.py"
SUITE_DIR = ROOT / "console-src/inbox2/tests"
SUITES = ("layout.mjs", "customer-loading.mjs", "rewrite-draft.mjs", "draft-recovery.mjs",
          "send-access.mjs", "conversation-state.mjs")
HEALTH = {"ok": True, "synthetic": True, "readOnly": True}
_LOOPBACK = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class GateError(RuntimeError):
    pass


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _drain(stream, lines: queue.Queue) -> None:
    for line in stream:
        lines.put(line.rstrip("\n"))


def _wait_until_ready(proc: subprocess.Popen, lines: queue.Queue, port: int, timeout: float) -> None:
    ready = f"Synthetic Inbox preview: http://127.0.0.1:{port}/inbox/"
    deadline = time.monotonic() + timeout
    output: list[str] = []
    while True:
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            if proc.poll() is not None:
                raise GateError(f"preview exited ({proc.returncode}) before it was ready: {output[-5:]}") from None
            if time.monotonic() > deadline:
                raise GateError("preview did not print its ready line") from None
            continue
        output.append(line)
        if line == ready:
            break
    while True:
        if proc.poll() is not None:
            raise GateError(f"preview exited ({proc.returncode}) during its health check")
        try:
            with _LOOPBACK.open(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                health = json.load(response)
        except (OSError, ValueError):
            if time.monotonic() > deadline:
                raise GateError("preview did not answer /health") from None
            time.sleep(0.1)
            continue
        if health != HEALTH:
            raise GateError(f"unexpected preview health: {health}")
        if proc.poll() is not None:
            raise GateError(f"preview exited ({proc.returncode}) during its health check")
        return


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextmanager
def preview(command: list[str], port: int, timeout: float = 20):
    """Yield (process, base URL) for a ready, live, healthy spawned preview."""
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_drain, args=(proc.stdout, lines), daemon=True)
    reader.start()
    try:
        _wait_until_ready(proc, lines, port, timeout)
        yield proc, f"http://127.0.0.1:{port}"
    finally:
        stop(proc)
        reader.join(timeout=5)
        proc.stdout.close()


def run_suites(proc: subprocess.Popen, base: str, paths: list[Path], runner: tuple[str, ...] = ("node",)) -> None:
    env = {**os.environ, "INBOX_TEST_URL": base}
    failed = []
    for path in paths:
        print(f"inbox browser gate: {path.name} -> {base}", flush=True)
        try:
            result = subprocess.run([*runner, str(path)], cwd=ROOT, env=env, timeout=300)
            ok = result.returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
        if not ok:
            failed.append(path.name)
        if proc.poll() is not None:
            raise GateError(f"preview exited ({proc.returncode}) during {path.name}")
    if failed:
        raise GateError(f"browser suites failed: {', '.join(failed)}")


def main() -> int:
    missing = [name for name in SUITES if not (SUITE_DIR / name).is_file()]
    try:
        if missing:
            raise GateError(f"missing required browser suites: {', '.join(missing)}")
        port = free_port()
        command = [sys.executable, str(PREVIEW), "--repo", str(ROOT), "--port", str(port)]
        with preview(command, port) as (proc, base):
            run_suites(proc, base, [SUITE_DIR / name for name in SUITES])
    except GateError as error:
        print(f"inbox browser gate failed: {error}", file=sys.stderr)
        return 1
    print(f"inbox browser gate: {len(SUITES)} suites passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
