"""Missed Gorgias messages enter Hermes once, without provider writes."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bb_webhook.database import init_db
from bb_webhook.db import Database
from gorgias_reconcile import reconcile_page


WHEN = "2026-09-25T14:39:03+00:00"


def ticket(ticket_id=284477559, received=WHEN):
    return {"id": ticket_id, "status": "open", "spam": False, "trashed_datetime": None,
            "subject": "Order question", "updated_datetime": received,
            "last_received_message_datetime": received, "customer": {"email": "customer@example.test"}}


def message(message_id=730082445, *, agent=False, at=WHEN, body="Please help with my order."):
    return {"id": message_id, "ticket_id": 284477559, "created_datetime": at, "from_agent": agent,
            "public": True, "channel": "email", "stripped_text": body,
            "preferred_content": body, "preferred_content_field": "stripped_text",
            "sender": {"email": "customer@example.test"}}


class FakeMCP:
    def __init__(self, tickets, messages):
        self.tickets = tickets
        self.messages = messages
        self.detail_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def call(self, tool, arguments):
        if tool == "list_inbox_tickets":
            return {"data": self.tickets, "meta": {"next_cursor": "older"}}
        assert tool == "get_ticket_messages"
        ticket_id = arguments["ticket_id"]
        self.detail_calls.append(ticket_id)
        return {"data": self.messages[ticket_id]}


class ReconcileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "webhook.db"
        await init_db(self.db_path)
        self.now = datetime(2026, 9, 25, 14, 40, tzinfo=timezone.utc).timestamp()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def scan(self, client, **kwargs):
        return await reconcile_page(self.db_path, "buttonsbebe", client_factory=lambda: client,
                                    now=self.now, **kwargs)

    def rows(self, sql):
        with sqlite3.connect(self.db_path) as db:
            return db.execute(sql).fetchall()

    async def test_unanswered_customer_message_is_queued_once_for_hermes(self):
        client = FakeMCP([ticket()], {284477559: [message()]})
        cursor, count = await self.scan(client)
        self.assertEqual((cursor, count), ("older", 1))
        self.assertEqual(self.rows("SELECT message_id,status,is_customer_message FROM job_queue"),
                         [("730082445", "pending", 1)])
        source = json.loads(self.rows("SELECT raw_payload FROM webhook_events")[0][0])
        self.assertEqual(source["source"], "gorgias_reconciliation")
        self.assertEqual(self.rows("SELECT message_text FROM parsed_messages"),
                         [("Please help with my order.",)])
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(client.detail_calls, [284477559])

    async def test_agent_replied_ticket_is_not_drafted_or_refetched(self):
        client = FakeMCP([ticket()], {284477559: [message(730082446, agent=True), message()]})
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(client.detail_calls, [284477559])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM job_queue"), [(0,)])

    async def test_existing_webhook_message_is_not_queued_again(self):
        await Database(self.db_path).execute(
            """INSERT INTO parsed_messages
               (message_id,ticket_id,event_type,author_type,is_customer_message,created_at,received_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("730082445", 284477559, "ticket-message-created", "customer", 1, WHEN, WHEN))
        client = FakeMCP([ticket()], {284477559: [message()]})
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(client.detail_calls, [])

    async def test_summary_ahead_of_messages_retries(self):
        later = "2026-09-25T14:40:00+00:00"
        client = FakeMCP([ticket(received=later)], {284477559: [message()]})
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(client.detail_calls, [284477559, 284477559])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM gorgias_reconcile_seen"), [(0,)])

    async def test_cross_ticket_message_is_rejected(self):
        wrong = {**message(), "ticket_id": 999}
        client = FakeMCP([ticket()], {284477559: [wrong]})
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM job_queue"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM gorgias_reconcile_seen"), [(0,)])

    async def test_active_job_cap_defers_other_tickets(self):
        second = ticket(284938147)
        client = FakeMCP([ticket(), second],
                         {284477559: [message()], 284938147: [{**message(730071617), "ticket_id": 284938147}]})
        self.assertEqual(await self.scan(client, max_active_jobs=1), (None, 1))
        self.assertEqual(client.detail_calls, [284477559])
        self.assertEqual(await self.scan(client, max_active_jobs=1), (None, 0))
        await Database(self.db_path).execute("UPDATE job_queue SET status='done'")
        self.assertEqual(await self.scan(client, max_active_jobs=1), ("older", 1))
        self.assertEqual(client.detail_calls, [284477559, 284938147])


if __name__ == "__main__":
    unittest.main()
