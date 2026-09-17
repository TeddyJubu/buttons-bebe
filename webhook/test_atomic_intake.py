"""Synthetic interruption tests: no real credentials or customer data."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import httpx

from bb_webhook import app as app_module, database
from bb_webhook.db import Database


class AtomicIntakeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        await database.init_db(self.path)
        self.event = dict(tenant_id="test", ticket_id=123, message_id="synthetic-1",
                          event_type="ticket.message.created", author_type="customer",
                          is_customer_message=True, channel="email", intents=[],
                          message_text="Synthetic question")

    async def counts(self):
        return [(await Database(self.path).fetch(f"SELECT COUNT(*) AS n FROM {table}"))[0]["n"]
                for table in ("webhook_events", "parsed_messages", "job_queue")]

    async def test_interruption_after_each_write_rolls_back_then_retry_succeeds(self):
        for table in ("webhook_events", "parsed_messages", "job_queue"):
            with self.subTest(table=table):
                await Database(self.path).execute(
                    f"CREATE TRIGGER injected_failure AFTER INSERT ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END")
                with self.assertRaises(aiosqlite.IntegrityError):
                    await database.ingest_event(self.event, "{}", self.path)
                self.assertEqual(await self.counts(), [0, 0, 0])
                self.assertFalse(await database.is_duplicate("synthetic-1", self.path))
                await Database(self.path).execute("DROP TRIGGER injected_failure")
        job = await database.ingest_event(self.event, "{}", self.path)
        self.assertIsInstance(job, int)
        self.assertEqual(await self.counts(), [1, 1, 1])

    async def test_concurrent_deliveries_and_post_commit_retry_enqueue_once(self):
        results = await asyncio.gather(*[
            database.ingest_event(self.event, "{}", self.path) for _ in range(16)])
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(await self.counts(), [1, 1, 1])
        self.assertIsNone(await database.ingest_event(self.event, "{}", self.path))

    async def test_cancellation_rolls_back_uncommitted_transaction(self):
        started = asyncio.Event()
        async def interrupted(conn):
            await conn.execute("INSERT INTO app_settings VALUES ('test', '1', 'now')")
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(Database(self.path).transaction(interrupted))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await Database(self.path).fetch("SELECT * FROM app_settings"), [])

    async def test_contention_retry_rolls_back_whole_callback(self):
        attempts = 0
        async def write(conn):
            nonlocal attempts
            attempts += 1
            await conn.execute("INSERT INTO app_settings VALUES ('test', '1', 'now')")
            if attempts == 1:
                raise aiosqlite.OperationalError("database is locked")
            return "committed"
        result = await Database(self.path).transaction(write)
        self.assertEqual((result, attempts), ("committed", 2))
        self.assertEqual(len(await Database(self.path).fetch("SELECT * FROM app_settings")), 1)

    async def test_legacy_partial_intake_is_reported_without_replaying_it(self):
        # Seed the pre-job-queue partial state directly: an event row with no
        # parsed message and no job (the state record_event used to leave).
        await Database(self.path).execute(
            "INSERT INTO webhook_events (message_id, tenant_id, ticket_id, "
            "event_type, author_type, raw_payload, received_at) "
            "VALUES (?, 'test', ?, 'created', 'customer', ?, ?)",
            ("synthetic-1", 123, "{}", "2026-01-01T00:00:00+00:00"),
            operation="test_seed_partial_event")
        self.assertIsNone(await database.ingest_event(self.event, "{}", self.path))
        self.assertEqual(await database.get_intake_integrity_stats(self.path), [dict(
            event_type="created", author_type="customer", events=1,
            missing_parsed=1, missing_jobs=1)])
        self.assertEqual(await self.counts(), [1, 0, 0])

    async def test_agent_intake_retains_feedback_payload(self):
        self.event.update(author_type="agent", is_customer_message=False)
        await database.ingest_event(self.event, "{}", self.path)
        job = await database.get_next_pending_job(self.path)
        self.assertEqual(job["is_customer_message"], 0)
        self.assertEqual(job["author_type"], "agent")

    async def test_http_failure_never_acknowledges_and_redelivery_is_accepted(self):
        await Database(self.path).execute(
            "CREATE TRIGGER injected_failure AFTER INSERT ON job_queue "
            "BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END")
        real_db = Database
        with (
            patch.object(database, "Database", side_effect=lambda path=None: real_db(path or self.path)),
            patch.object(app_module, "verify_signature", return_value=True),
            patch.object(app_module, "parse_event", return_value=self.event),
            patch.object(app_module, "is_event_too_old", return_value=False),
            patch.object(app_module, "is_event_in_future", return_value=False),
            patch.object(app_module, "_check_rate_limit", return_value=True),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_module.app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                first = await client.post("/webhook/gorgias/test", content=b"{}")
                self.assertEqual(first.status_code, 500)
                self.assertEqual(await self.counts(), [0, 0, 0])
                await real_db(self.path).execute("DROP TRIGGER injected_failure")
                second = await client.post("/webhook/gorgias/test", content=b"{}")
                self.assertEqual(second.status_code, 202)
                third = await client.post("/webhook/gorgias/test", content=b"{}")
                self.assertEqual(third.status_code, 200)
                self.assertEqual(third.json()["status"], "duplicate")
        self.assertEqual(await self.counts(), [1, 1, 1])


if __name__ == "__main__":
    unittest.main()
