from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from unittest.mock import patch

import serve_inbox_fixture_stack as fixture_server


REPO = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO / "testing" / "inbox_fixtures" / "long_email.json"


def fixture_interrupt_signal(platform_name: str | None = None):
    platform_name = os.name if platform_name is None else platform_name
    if platform_name == "nt":
        return getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
    return signal.SIGINT


def stop_fixture_child(process, *, timeout: float = 8, platform_name: str | None = None):
    """Record interrupt success before owned terminate/kill fallback cleanup."""
    signal_sent = False
    graceful_exit_code = process.poll()
    if graceful_exit_code is None:
        try:
            process.send_signal(fixture_interrupt_signal(platform_name))
            signal_sent = True
        except (OSError, ValueError):
            pass
        if signal_sent:
            try:
                graceful_exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                graceful_exit_code = None

    # Freeze the graceful outcome before any emergency cleanup. A forced exit
    # can prevent a leaked test child, but it cannot turn a failed stop into a pass.
    graceful = signal_sent and graceful_exit_code == 0
    forced_cleanup = False
    if not graceful and process.poll() is None:
        forced_cleanup = True
        try:
            process.terminate()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except (OSError, ValueError):
                pass
            process.wait(timeout=5)
    return graceful, graceful_exit_code, forced_cleanup


class FixtureLoadingTests(unittest.TestCase):
    def load_value(self, ticket_id: object) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps({
                "version": 1,
                "tickets": [{"id": ticket_id}],
                "cases": {"example": str(ticket_id)},
            }), encoding="utf-8")
            return fixture_server.load_fixture(path)

    def test_provider_ids_match_the_ascii_positive_one_to_eighteen_digit_contract(self):
        for ticket_id in (1, "123456789012345678"):
            with self.subTest(ticket_id=ticket_id):
                loaded = self.load_value(ticket_id)
                self.assertEqual(str(loaded["tickets"][0]["id"]), str(ticket_id))

        for ticket_id in ("0", "01", "1000000000000000000", "١"):
            with self.subTest(ticket_id=ticket_id), self.assertRaises(ValueError):
                self.load_value(ticket_id)

    def test_long_email_order_keeps_headband_prospective_and_totals_reconcile(self):
        fixture = fixture_server.load_fixture(FIXTURE_PATH)
        ticket_id = fixture["cases"]["long-email"]
        projection = fixture["shopify"][ticket_id]
        order = projection["order"]
        rows = order["lineItems"]["nodes"]
        item_total = sum(
            Decimal(str(row["originalUnitPriceSet"]["shopMoney"]["amount"])) * row["quantity"]
            for row in rows
        )
        order_total = Decimal(order["currentTotalPriceSet"]["shopMoney"]["amount"])
        lifetime_total = Decimal(projection["customer"]["amountSpent"]["amount"])
        prior_orders_total = sum(
            Decimal(row["currentTotalPriceSet"]["shopMoney"]["amount"])
            for row in projection["history"]
        )
        latest = fixture["messages"][ticket_id][-1]
        latest_content = latest.get("stripped_html", "") + latest.get("body_html", "")
        readonly_draft = fixture["drafts"][ticket_id]["readonlyDraft"]

        self.assertEqual(item_total, order_total)
        self.assertEqual(prior_orders_total + order_total, lifetime_total)
        self.assertFalse(any("headband" in row["title"].casefold() for row in rows))
        self.assertIn("would like to order the matching headband", latest_content)
        self.assertIn("decide whether to add the matching headband", readonly_draft)


class FixturePortTests(unittest.TestCase):
    def test_command_line_accepts_zero_to_request_an_owned_ephemeral_port(self):
        with patch.object(sys, "argv", ["serve_inbox_fixture_stack.py", "--port", "0"]):
            args = fixture_server.parse_args()
        self.assertEqual(args.port, 0)

    def test_owned_listener_sets_reuseaddr_before_bind(self):
        calls: list[tuple[str, tuple[object, ...]]] = []

        class TrackedSocket:
            def setsockopt(self, *args):
                calls.append(("setsockopt", args))

            def bind(self, *args):
                calls.append(("bind", args))

            def listen(self, *args):
                calls.append(("listen", args))

            def getsockname(self):
                return ("127.0.0.1", 43210)

            def close(self):
                calls.append(("close", ()))

        tracked = TrackedSocket()
        with patch.object(fixture_server.socket, "socket", return_value=tracked):
            listener, port = fixture_server.bind_loopback_listener(0)

        self.assertIs(listener, tracked)
        self.assertEqual(port, 43210)
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(calls[0], ("setsockopt", (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)))
        self.assertEqual(calls[1][0], "bind")

    def test_active_loopback_listener_refuses_second_owner_and_remains_usable(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
            owner.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            owner.bind(("127.0.0.1", 0))
            owner.listen(1)
            owner.settimeout(2)
            port = owner.getsockname()[1]

            with self.assertRaises(OSError):
                fixture_server.bind_loopback_listener(port)

            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                accepted, address = owner.accept()
                with accepted:
                    self.assertEqual(address[0], "127.0.0.1")
                    self.assertEqual(client.getpeername()[1], port)

    def test_windows_interrupt_uses_ctrl_break_and_valueerror_cleanup_cannot_pass(self):
        with patch.object(signal, "CTRL_BREAK_EVENT", 12345, create=True):
            self.assertEqual(fixture_interrupt_signal("nt"), 12345)
        self.assertEqual(fixture_interrupt_signal("posix"), signal.SIGINT)

        class UnsupportedWindowsSignal:
            returncode = None
            terminated = False

            @staticmethod
            def poll():
                return None

            @staticmethod
            def send_signal(_value):
                raise ValueError("synthetic Windows signal rejection")

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def wait(self, timeout):
                return self.returncode

            @staticmethod
            def kill():
                raise AssertionError("terminate is sufficient for this cleanup regression")

        process = UnsupportedWindowsSignal()
        legacy = UnsupportedWindowsSignal()
        with self.assertRaises(ValueError):
            try:
                legacy.send_signal(signal.SIGINT)
            except (OSError, subprocess.TimeoutExpired):
                legacy.terminate()
        self.assertFalse(legacy.terminated,
                         "the previous SIGINT cleanup missed ValueError and left its owned child running")

        graceful, graceful_exit_code, forced = stop_fixture_child(process, platform_name="nt")
        self.assertFalse(graceful, "owned forced cleanup must not count as graceful interrupt success")
        self.assertIsNone(graceful_exit_code)
        self.assertTrue(forced)
        self.assertTrue(process.terminated)

    def test_port_zero_server_reports_and_serves_its_bound_port_then_stops_its_own_child(self):
        with tempfile.TemporaryDirectory(prefix="fixture-port-test-") as scratch_name:
            scratch = Path(scratch_name)
            home = scratch / "home"
            home.mkdir()
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(home),
                "TMPDIR": str(scratch),
                "TMP": str(scratch),
                "TEMP": str(scratch),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            }
            def start_and_check(requested_port: int, expected_port: int | None = None) -> int:
                process = subprocess.Popen(
                    [os.environ.get("INBOX_PYTHON", sys.executable), str(REPO / "testing" / "serve_inbox_fixture_stack.py"),
                     "--repo", str(REPO), "--port", str(requested_port)],
                    cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    **({"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt" else {}),
                )
                lines: queue.Queue[str] = queue.Queue()

                def read_output() -> None:
                    assert process.stdout is not None
                    for line in process.stdout:
                        lines.put(line)

                reader = threading.Thread(target=read_output, name="fixture-port-test-output", daemon=True)
                reader.start()
                received: list[str] = []
                port: int | None = None
                graceful_shutdown = False
                forced_cleanup = False
                graceful_exit_code: int | None = None
                try:
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline:
                        if process.poll() is not None and lines.empty():
                            break
                        try:
                            line = lines.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        received.append(line)
                        if line.startswith("FIXTURE_PORT="):
                            port = int(line.partition("=")[2].strip())
                            break

                    self.assertIsNotNone(port, "fixture child did not report its owned port: " + "".join(received))
                    assert port is not None
                    self.assertGreater(port, 0)
                    if expected_port is not None:
                        self.assertEqual(port, expected_port)

                    opener = build_opener(ProxyHandler({}))
                    health = None
                    health_deadline = time.monotonic() + 12
                    while time.monotonic() < health_deadline:
                        if process.poll() is not None:
                            break
                        try:
                            with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                                health = json.loads(response.read())
                            break
                        except (OSError, URLError, json.JSONDecodeError, TimeoutError):
                            time.sleep(0.05)
                    self.assertEqual(health, {"ok": True, "readOnly": True})
                    with opener.open(f"http://127.0.0.1:{port}/inbox/", timeout=1) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(b"Inbox", response.read())

                    diagnostics = None
                    ready_deadline = time.monotonic() + 12
                    while time.monotonic() < ready_deadline:
                        if process.poll() is not None:
                            break
                        try:
                            with opener.open(f"http://127.0.0.1:{port}/__fixture__/diagnostics", timeout=0.5) as response:
                                diagnostics = json.loads(response.read())
                            if diagnostics.get("fixtureDataReady") and diagnostics.get("syncComplete"):
                                break
                        except (OSError, URLError, json.JSONDecodeError, TimeoutError):
                            pass
                        time.sleep(0.05)
                    self.assertTrue(diagnostics and diagnostics.get("synthetic"))
                    self.assertTrue(diagnostics and diagnostics.get("readOnly"))
                    self.assertTrue(diagnostics and diagnostics.get("fixtureDataReady"))
                    self.assertTrue(diagnostics and diagnostics.get("syncComplete"))
                finally:
                    graceful_shutdown, graceful_exit_code, forced_cleanup = stop_fixture_child(process)
                    if process.stdout is not None:
                        process.stdout.close()
                    reader.join(timeout=1)

                self.assertTrue(graceful_shutdown, "fixture child must exit successfully after platform interrupt")
                self.assertFalse(forced_cleanup, "forced cleanup cannot count as a graceful fixture shutdown")
                self.assertEqual(graceful_exit_code, 0, "fixture child must exit successfully after interrupt")
                assert port is not None
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0,
                                        "fixture child must release its owned port after shutdown")
                return port

            port = start_and_check(0)
            restarted_port = start_and_check(port, expected_port=port)
            self.assertEqual(restarted_port, port)


if __name__ == "__main__":
    unittest.main()
