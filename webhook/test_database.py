from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from bb_webhook import database
from bb_webhook.db import Database


class DatabaseQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "queue.db"
        await database.init_db(self.db_path)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def _enqueue(self, message_id: str, *, customer: bool) -> int:
        return await database.enqueue_job(
            tenant_id="test",
            ticket_id=100,
            message_id=message_id,
            event_type="ticket.message.created",
            author_type="customer" if customer else "agent",
            is_customer_message=customer,
            payload={"message_id": message_id},
            db_path=self.db_path,
        )

    async def test_wrapper_executes_and_fetches_without_changing_free_function_api(self) -> None:
        db = Database(self.db_path)
        await db.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("mode", "test", "now"),
            operation="test_insert",
        )
        rows = await db.fetch(
            "SELECT value FROM app_settings WHERE key = ?",
            ("mode",),
            operation="test_select",
        )
        self.assertEqual(rows[0]["value"], "test")
        self.assertEqual(await database.get_setting("mode", db_path=self.db_path), "test")

    async def test_next_pending_job_is_customer_first(self) -> None:
        await self._enqueue("agent-first", customer=False)
        await self._enqueue("customer-second", customer=True)
        db = Database(self.db_path)
        await db.execute(
            "UPDATE job_queue SET created_at = ? WHERE message_id = ?",
            ("2026-01-01T00:00:00+00:00", "agent-first"),
            operation="test_age_agent_job",
        )
        await db.execute(
            "UPDATE job_queue SET created_at = ? WHERE message_id = ?",
            ("2026-01-02T00:00:00+00:00", "customer-second"),
            operation="test_age_customer_job",
        )

        job = await database.get_next_pending_job(self.db_path)

        self.assertIsNotNone(job)
        self.assertEqual(job["message_id"], "customer-second")
        self.assertEqual(job["is_customer_message"], 1)

    async def test_enqueue_and_claim_are_atomic_under_concurrency(self) -> None:
        ids = await asyncio.gather(*(self._enqueue("same-job", customer=True) for _ in range(16)))

        self.assertEqual(len(set(ids)), 1)
        rows = await Database(self.db_path).fetch(
            "SELECT COUNT(*) AS count FROM job_queue WHERE message_id = ?",
            ("same-job",),
            operation="test_count_jobs",
        )
        self.assertEqual(rows[0]["count"], 1)

        claims = await asyncio.gather(*(database.claim_job(ids[0], self.db_path) for _ in range(16)))

        self.assertEqual(sum(claims), 1)


if __name__ == "__main__":
    unittest.main()
