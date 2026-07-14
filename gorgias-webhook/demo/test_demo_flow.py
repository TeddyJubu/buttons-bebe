#!/usr/bin/env python3
"""
test_demo_flow.py — Unit tests for demo_store and integration test with mock LLM.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, "/root")

# Force mock LLM and isolated feedback db before any project imports
os.environ["LLM_PROVIDER"] = "mock"
_TMP = tempfile.mkdtemp(prefix="demo_test_")
os.environ["FEEDBACK_DB_PATH"] = os.path.join(_TMP, "feedback_test.db")

import demo_patches
demo_patches.apply()

import gorgias_api
from demo_gorgias_handler import handle
from demo_store import DemoStore, get_store


class TestDemoStore(unittest.TestCase):
    def setUp(self):
        self.store = get_store()
        self.store.reset()

    def test_create_ticket_and_messages(self):
        ticket = self.store.create_ticket(
            "shipped@example.com",
            "Where is my order?",
            "Please send tracking.",
        )
        self.assertEqual(ticket["id"], 1)
        self.assertEqual(ticket["customer"]["email"], "shipped@example.com")

        msgs = self.store.list_messages(ticket["id"])
        self.assertEqual(len(msgs), 1)
        self.assertFalse(msgs[0]["from_agent"])

        self.store.add_internal_note(ticket["id"], "Draft reply here", 777419526)
        msgs = self.store.list_messages(ticket["id"])
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["channel"], "internal-note")
        self.assertFalse(msgs[1]["public"])

    def test_customer_shopify_integration(self):
        self.store.create_ticket("cancelme@example.com", "Cancel", "Please cancel")
        customer = self.store.get_customer(1)
        ctx = gorgias_api.extract_order_context(customer)
        self.assertTrue(ctx["shopify_found"])
        self.assertGreater(len(ctx["orders"]), 0)
        self.assertEqual(ctx["orders"][0]["financial_status"], "paid")

    def test_no_order_customer(self):
        self.store.create_ticket("customer@example.com", "Sizing", "Do you run small?")
        customer = self.store.get_customer(1)
        ctx = gorgias_api.extract_order_context(customer)
        self.assertFalse(ctx["shopify_found"])
        self.assertEqual(ctx["orders"], [])

    def test_tags_and_priority(self):
        ticket = self.store.create_ticket("a@example.com", "Hi", "Hello")
        self.store.set_priority(ticket["id"], "urgent")
        self.store.add_tags(ticket["id"], ["ai-drafted", "escalate"])
        updated = self.store.get_ticket(ticket["id"])
        self.assertEqual(updated["priority"], "urgent")
        self.assertIn("ai-drafted", updated["tags"])
        self.assertIn("escalate", updated["tags"])

    def test_telegram_and_owner_reply_queue(self):
        self.store.append_telegram("notify", "Draft for ticket #42", ticket_id=42)
        self.store.append_telegram("priority", "URGENT ticket #42", ticket_id=42)
        self.assertEqual(len(self.store.get_telegram("notify")), 1)
        self.assertEqual(len(self.store.get_telegram("priority")), 1)

        self.store.enqueue_owner_reply("Our return window is 30 days.")
        updates = self.store.poll_replies(offset=0)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["message"]["text"], "Our return window is 30 days.")

    def test_gorgias_handler_shapes(self):
        ticket = self.store.create_ticket("shipped@example.com", "Track", "Where?")
        tid = ticket["id"]

        status, data = handle("GET", f"/api/tickets/{tid}")
        self.assertEqual(status, 200)
        self.assertEqual(data["id"], tid)

        status, data = handle("GET", f"/api/messages?ticket_id={tid}&limit=100")
        self.assertEqual(status, 200)
        self.assertIn("data", data)
        self.assertEqual(len(data["data"]), 1)

        cid = ticket["customer_id"]
        status, data = handle("GET", f"/api/customers/{cid}")
        self.assertEqual(status, 200)
        self.assertEqual(data["email"], "shipped@example.com")

        status, data = handle("POST", f"/api/tickets/{tid}/messages", {
            "channel": "internal-note",
            "from_agent": True,
            "public": False,
            "body_text": "AI draft",
            "sender": {"id": 777419526},
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["channel"], "internal-note")

        status, data = handle("PUT", f"/api/tickets/{tid}", {"priority": "high"})
        self.assertEqual(status, 200)
        self.assertEqual(data["priority"], "high")

        status, data = handle("POST", f"/api/tickets/{tid}/tags", {"names": ["ai-drafted"]})
        self.assertEqual(status, 200)
        self.assertIn("ai-drafted", data["tags"])


class TestDemoIntegration(unittest.TestCase):
    def setUp(self):
        get_store().reset()
        import feedback_db
        feedback_db.init_db(os.environ["FEEDBACK_DB_PATH"])

    def test_create_ticket_runs_workflow_a(self):
        import demo_runner

        result = demo_runner.create_and_run(
            "shipped@example.com",
            "Has my order shipped?",
            "Can you send tracking?",
        )
        self.assertEqual(result["workflow"], "A")
        self.assertIsNone(result.get("pipeline_error"))

        store = get_store()
        detail = store.get_ticket_detail(result["ticket_id"])
        internal = [m for m in detail["messages"] if m.get("channel") == "internal-note"]
        self.assertGreaterEqual(len(internal), 1, "Expected internal note from Workflow A")

        # Telegram inboxes should have messages (testing bot at minimum)
        self.assertGreater(len(store.get_telegram("notify")), 0)

    def test_agent_reply_runs_workflow_b(self):
        import demo_runner

        create_result = demo_runner.create_and_run(
            "shipped@example.com",
            "Tracking",
            "Where is my package?",
        )
        tid = create_result["ticket_id"]

        # Simulate human editing the draft
        agent_result = demo_runner.add_agent_reply_and_run(
            tid,
            "Hi! Your order shipped via UPS. Tracking: 1Z999AA10123456784",
        )
        self.assertEqual(agent_result["workflow"], "B")

        import feedback_db
        conn = feedback_db.get_conn(os.environ["FEEDBACK_DB_PATH"])
        try:
            replies = conn.execute("SELECT COUNT(*) AS c FROM replies").fetchone()
            self.assertGreater(replies["c"], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
