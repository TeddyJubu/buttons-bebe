"""Preview ownership and cleanup for the Inbox browser gate; no browser needed."""
import io
import socket
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import inbox_browser_gate as gate


def real_preview(port):
    return [sys.executable, str(gate.PREVIEW), "--repo", str(gate.ROOT), "--port", str(port)]


def port_is_free(port):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


class InboxBrowserGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def script(self, name, body):
        path = self.tmp / name
        path.write_text(textwrap.dedent(body))
        return path

    def test_missing_conversation_state_suite_fails_before_any_preview_starts(self):
        for name in ("layout.mjs", "customer-loading.mjs", "rewrite-draft.mjs", "draft-recovery.mjs", "send-access.mjs"):
            (self.tmp / name).write_text("")
        with (patch.object(gate, "SUITE_DIR", self.tmp), patch.object(gate.subprocess, "Popen") as popen,
              patch("sys.stderr", new_callable=io.StringIO) as stderr):
            self.assertEqual(gate.main(), 1)
        self.assertEqual(stderr.getvalue(),
                         "inbox browser gate failed: missing required browser suites: conversation-state.mjs\n")
        popen.assert_not_called()

    def test_occupied_port_never_yields_a_preview(self):
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            port = holder.getsockname()[1]
            with self.assertRaisesRegex(gate.GateError, "exited"):
                with gate.preview(real_preview(port), port, timeout=10):
                    self.fail("an occupied port was accepted")

    def test_ready_line_without_a_live_healthy_process_is_rejected(self):
        exits = self.script("exits.py", """
            import sys
            print(f"Synthetic Inbox preview: http://127.0.0.1:{sys.argv[1]}/inbox/", flush=True)
        """)
        foreign = self.script("foreign.py", """
            import json, sys
            from http.server import BaseHTTPRequestHandler, HTTPServer
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    body = json.dumps({"ok": True}).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
                    self.wfile.write(body)
            server = HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
            print(f"Synthetic Inbox preview: http://127.0.0.1:{sys.argv[1]}/inbox/", flush=True)
            server.serve_forever()
        """)
        for script, error in ((exits, "exited"), (foreign, "unexpected preview health")):
            port = gate.free_port()
            with self.subTest(script=script.name), self.assertRaisesRegex(gate.GateError, error):
                with gate.preview([sys.executable, str(script), str(port)], port, timeout=10):
                    self.fail("an unhealthy preview was accepted")
            self.assertTrue(port_is_free(port))

    def test_suites_get_the_owned_url_and_failure_still_stops_the_preview(self):
        body = """
            import os
            from pathlib import Path
            Path(__file__ + ".url").write_text(os.environ["INBOX_TEST_URL"])
            raise SystemExit(1 if __file__.endswith("fails.py") else 0)
        """
        passes, fails = self.script("passes.py", body), self.script("fails.py", body)
        port = gate.free_port()
        with self.assertRaisesRegex(gate.GateError, "browser suites failed: fails.py"):
            with gate.preview(real_preview(port), port) as (proc, base):
                gate.run_suites(proc, base, [passes, fails], runner=(sys.executable,))
        self.assertIsNotNone(proc.returncode)
        self.assertTrue(port_is_free(port))
        for path in (passes, fails):
            self.assertEqual(Path(str(path) + ".url").read_text(), f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    unittest.main()
