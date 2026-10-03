from __future__ import annotations

import importlib.util
import json
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path


DEMO_DIR = Path(__file__).resolve().parent


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, DEMO_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fake_gorgias = load_module("fake_gorgias_mcp", "fake_gorgias_mcp.py")
QA_GORGIAS_TOOLS = load_module("qa_safety", "../testing/qa_safety.py").TOOLS["buttonsbebe_gorgias"]
# fake_gorgias_rest imports fake_gorgias_mcp by module name, matching normal
# execution while remaining dependency-light for this direct-file test.
import sys

sys.modules["fake_gorgias_mcp"] = fake_gorgias
fake_rest = load_module("fake_gorgias_rest", "fake_gorgias_rest.py")


class FakeGorgiasMCPTests(unittest.TestCase):
    def test_mcp_registers_exactly_the_six_production_read_only_tools(self):
        registered = tuple(fake_gorgias.mcp._tool_manager._tools.keys())
        self.assertEqual(set(registered), QA_GORGIAS_TOOLS)
        self.assertEqual(len(registered), 6)

    def test_inbox_paging_walks_every_ticket_once_newest_first(self):
        first = fake_gorgias.list_inbox_tickets(4)
        self.assertEqual(set(first), {"data", "meta"})
        self.assertEqual(set(first["data"][0]), set(fake_gorgias.INBOX_FIELDS))
        self.assertNotIn("messages", first["data"][0])
        self.assertNotIn("synthetic", first["data"][0])
        second = fake_gorgias.list_inbox_tickets(4, first["meta"]["next_cursor"])
        self.assertIsNone(second["meta"]["next_cursor"])
        rows = first["data"] + second["data"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({row["id"] for row in rows}), 6)
        stamps = [row["updated_datetime"] for row in rows]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        self.assertEqual(len(fake_gorgias.list_inbox_tickets(500)["data"]), 6)

    def test_inbox_paging_rejects_invalid_limits_and_cursors(self):
        for limit in (0, -1, True, "4"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                fake_gorgias.list_inbox_tickets(limit)
        for cursor in ("", "4", "demo-cursor:", "demo-cursor:-1", "demo-cursor:x", 4):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                fake_gorgias.list_inbox_tickets(10, cursor)

    def test_fixture_covers_all_demo_orders_and_is_synthetic(self):
        data = fake_gorgias.load_fixtures()
        self.assertEqual(len(data["customers"]), 3)
        self.assertEqual(len(data["tickets"]), 6)
        orders = {
            order["name"]
            for customer in data["customers"]
            for order in customer["orders"]
        }
        self.assertEqual(orders, {"#1001", "#1002", "#1003", "#1004"})
        self.assertTrue(all(item["synthetic"] for item in data["customers"] + data["tickets"]))

    def test_list_recent_tickets_mirrors_trimmed_live_shape_and_limit(self):
        result = fake_gorgias.list_recent_tickets(2)
        self.assertEqual(result["count"], 2)
        self.assertEqual(set(result["tickets"][0]), {
            "id", "subject", "status", "channel", "created_datetime", "updated_datetime"
        })
        self.assertNotIn("messages", result["tickets"][0])
        self.assertEqual(fake_gorgias.list_recent_tickets(100)["count"], 6)

    def test_ticket_and_messages_keep_customer_and_agent_context(self):
        ticket = fake_gorgias.get_ticket(61002)
        self.assertEqual(ticket["customer"]["email"], "ai-demo-fulfilled@example.com")
        self.assertNotIn("messages", ticket)
        messages = fake_gorgias.get_ticket_messages(61002)
        self.assertEqual(messages["count"], 2)
        self.assertFalse(messages["data"][0]["from_agent"])
        self.assertTrue(messages["data"][1]["from_agent"])

    def test_customer_search_is_case_insensitive_and_has_two_orders(self):
        customer = fake_gorgias.get_customer(51003)
        self.assertEqual(len(customer["orders"]), 2)
        result = fake_gorgias.search_customer("AI-DEMO-MULTI@EXAMPLE.COM")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["data"][0]["id"], 51003)
        self.assertEqual(fake_gorgias.search_customer("missing@example.com")["data"], [])

    def test_missing_records_fail_closed(self):
        self.assertEqual(fake_gorgias.get_ticket(99999)["error"], "ticket not found")
        self.assertEqual(fake_gorgias.get_ticket_messages(99999)["error"], "ticket not found")
        self.assertEqual(fake_gorgias.get_customer(99999)["error"], "customer not found")


class FakeGorgiasRESTTests(unittest.TestCase):
    def setUp(self):
        self.state = fake_rest.DemoGorgiasState()
        self.server = fake_rest.create_server(host="127.0.0.1", port=0, state=self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method: str, path: str, body=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def test_health_and_empty_action_log_are_simulated(self):
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["simulated"])
        self.assertEqual(payload["delivery"], "local_capture_only")
        self.assertEqual(payload["count"], 0)

    def test_send_and_note_use_console_routes_and_remain_local(self):
        status, sent = self.request(
            "POST", "/dashboard/api/ticket/61001/send", {"text": "Demo reply", "customer_name": "Demo Customer One"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(sent["public"])
        self.assertFalse(sent["delivered"])
        status, note = self.request(
            "POST", "/console/api/ticket/61004/note", {"text": "Review refund fixture"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(note["public"])
        self.assertEqual([item["kind"] for item in self.state.actions], ["send", "note"])

    def test_real_gorgias_rest_shape_reads_messages_and_captures_posts(self):
        status, messages = self.request("GET", "/api/tickets/61002/messages?limit=30")
        self.assertEqual(status, 200)
        self.assertEqual(len(messages["data"]), 2)

        status, sent = self.request(
            "POST",
            "/api/tickets/61002/messages",
            {
                "channel": "email",
                "public": True,
                "from_agent": True,
                "body_text": "Captured through the real Gorgias client route",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(sent["kind"], "send")
        self.assertFalse(sent["delivered"])

        status, note = self.request(
            "POST",
            "/api/tickets/61004/messages",
            {
                "channel": "internal-note",
                "public": False,
                "body_text": "Captured internal note",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(note["kind"], "note")
        self.assertEqual([item["kind"] for item in self.state.actions], ["send", "note"])

    def test_empty_unknown_and_malformed_actions_are_not_captured(self):
        status, _ = self.request("POST", "/dashboard/api/ticket/61001/send", {"text": "  "})
        self.assertEqual(status, 400)
        status, _ = self.request("POST", "/dashboard/api/ticket/99999/note", {"text": "nope"})
        self.assertEqual(status, 404)
        status, _ = self.request("POST", "/dashboard/api/ticket/61001/note", {"wrong": "field"})
        self.assertEqual(status, 400)
        self.assertEqual(self.state.actions, [])

    def test_non_local_bind_is_rejected(self):
        with self.assertRaises(ValueError):
            fake_rest.create_server(host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
