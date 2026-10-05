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
            process = subprocess.Popen(
                [sys.executable, str(REPO / "testing" / "serve_inbox_fixture_stack.py"),
                 "--repo", str(REPO), "--port", "0"],
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

                self.assertIsNotNone(port, "fixture child did not report its owned ephemeral port: " + "".join(received))
                assert port is not None
                self.assertGreater(port, 0)

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
                if process.poll() is None:
                    try:
                        process.send_signal(signal.SIGINT)
                        process.wait(timeout=8)
                    except (OSError, subprocess.TimeoutExpired):
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
                reader.join(timeout=1)

            self.assertIsNotNone(process.poll(), "owned fixture child was not stopped")


if __name__ == "__main__":
    unittest.main()
