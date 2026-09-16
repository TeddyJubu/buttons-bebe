"""Run separately with the inbox runtime and httpx installed."""
import importlib.util
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory()
os.environ["HELPDESK_DB_FILE"] = str(Path(_TMP.name) / "inbox.sqlite3")
spec = importlib.util.spec_from_file_location("inbox_server", Path(__file__).resolve().parents[1] / "review_server.py")
server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = server
spec.loader.exec_module(server)
from fastapi.testclient import TestClient


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.context = TestClient(server.app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def post(self, data):
        return self.client.post("/console/api/helpdesk", content=__import__("json").dumps(data), headers={"content-type":"application/json"})

    def test_send_lock_and_disabled_bridge(self):
        response = self.post({"tool": "helpdesk.send_reply", "arguments": {"ticketId": "t-ada-track", "text": "Hi", "confirmed": True}})
        self.assertEqual(response.json(), {"ok":False,"error":"send_access_inactive","message":"Activate the send access."})
        self.assertEqual(self.client.post("/webhook/gorgias").status_code, 503)

    def test_invalid_payloads_are_structured_errors(self):
        for value in ([], 1, None, {"tool": []}, {"tool": "helpdesk.list_tickets", "arguments": []}, {"tool": "helpdesk.list_tickets", "arguments": {"limit": "2"}}):
            result = self.post(value)
            self.assertEqual(result.status_code, 400, value)
            self.assertFalse(result.json()["ok"])
        self.assertEqual(self.client.post("/console/api/helpdesk", content=b"{", headers={"content-type": "application/json"}).status_code, 400)

    def test_body_limit(self):
        response = self.client.post("/console/api/helpdesk", content=b" " * (server.MAX_BODY + 1), headers={"content-type": "application/json"})
        self.assertEqual(response.status_code, 413)

    def test_static_allowlist(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        for name in server.STATIC_FILES:
            self.assertEqual(self.client.get("/" + name).status_code, 200, name)
        for name in ("review_server.py", "static-manifest.json", "requirements.txt", "js/fixtures/demo-inbox.js", "data/intake_tickets.json", "js/shop/fixture-shop.js", "js/../review_server.py"):
            self.assertEqual(self.client.get("/" + name).status_code, 404, name)

    def test_capabilities_are_closed_and_backend_enforces_them(self):
        caps = self.post({"tool": "helpdesk.capabilities"}).json()["capabilities"]
        self.assertTrue(caps["listTickets"])
        self.assertFalse(caps["sendReply"])
        self.assertFalse(caps["escalateTicket"])
        for tool in ("helpdesk.pull_mailbox", "helpdesk.escalate_ticket", "helpdesk.draft_reply", "helpdesk.get_customer"):
            self.assertEqual(self.post({"tool": tool}).status_code, 403)
        self.assertEqual(self.post({"tool": "helpdesk.list_tickets"}).status_code, 503)
        self.assertEqual(self.client.get("/ready").status_code, 503)


    def test_symlink_and_path_traversal_cannot_serve_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "public").mkdir()
            (root / "private.txt").write_text("TEST PRIVATE VALUE")
            (root / "public" / "index.html").symlink_to(root / "private.txt")
            with patch.object(server, "INBOX", root / "public"):
                self.assertEqual(self.client.get("/").status_code, 404)
                for path in ("/%2e%2e/private.txt", "/../private.txt", "/%2fprivate.txt"):
                    self.assertEqual(self.client.get(path).status_code, 404)

    def test_storage_failure_is_not_reported_as_empty_inbox(self):
        with patch.object(server.tickets, "transaction", side_effect=server.StoreUnavailable("Unavailable")):
            self.assertEqual(self.post({"tool":"helpdesk.list_tickets"}).status_code, 503)
            self.assertEqual(self.client.get("/ready").status_code, 503)


if __name__ == "__main__":
    unittest.main()
