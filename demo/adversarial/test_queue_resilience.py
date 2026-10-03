"""Adversarial queue and processor tests for the isolated demo environment.

This module deliberately owns no production changes.  Every database is a
temporary SQLite file, and processor collaborators are patched in-process.
Some tests are expected to expose currently-verified safety gaps; those
assertions are intentionally not weakened to make the suite green.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
PROCESSOR_DIR = ROOT / "processor"
WEBHOOK_SRC = ROOT / "webhook" / "src"
sys.path[:0] = [str(PROCESSOR_DIR), str(WEBHOOK_SRC)]
sys.path.append(str(ROOT))

from demo.adversarial.offline_imports import without_root_dotenv  # noqa: E402

with without_root_dotenv():
    from bb_webhook import database  # noqa: E402
    import orchestrator  # noqa: E402


def _payload(message_id: str, ticket_id: int, *, event_created_at: str | None = None) -> dict:
    return {
        "message_id": message_id,
        "ticket_id": ticket_id,
        "message_text": f"Synthetic demo message {message_id}",
        "ticket_subject": "Demo queue resilience fixture",
        "customer_email": "queue-resilience@example.com",
        "created_at": event_created_at or datetime.now(timezone.utc).isoformat(),
    }


async def _count_rows(db_path: Path, table: str, where: str = "1=1") -> int:
    async with database.aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")
        row = await cursor.fetchone()
        await cursor.close()
    return int(row[0])


async def _row(db_path: Path, sql: str, params: tuple = ()) -> dict:
    async with database.aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = database.aiosqlite.Row
        cursor = await conn.execute(sql, params)
        result = dict(await cursor.fetchone())
        await cursor.close()
    return result


class QueueResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="queue-resilience-")
        self.db_path = Path(self._tmp.name) / "demo.sqlite3"
        await database.init_db(self.db_path)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def _enqueue(self, message_id: str, ticket_id: int, *, customer: bool = True) -> int:
        return await database.enqueue_job(
            tenant_id="cute-things-demo",
            ticket_id=ticket_id,
            message_id=message_id,
            event_type="ticket.message.created",
            author_type="customer" if customer else "agent",
            is_customer_message=customer,
            payload=_payload(message_id, ticket_id),
            db_path=self.db_path,
        )

    async def test_concurrent_enqueue_preserves_every_unique_job(self) -> None:
        started = time.perf_counter()
        job_ids = await asyncio.gather(*(
            self._enqueue(f"concurrent-{index}", 7000 + index)
            for index in range(32)
        ))

        self.assertEqual(len(job_ids), 32)
        self.assertEqual(len(set(job_ids)), 32)
        self.assertEqual(await _count_rows(self.db_path, "job_queue"), 32)
        self.assertLess(time.perf_counter() - started, 5.0)

    async def test_concurrent_claim_has_exactly_one_winner(self) -> None:
        job_id = await self._enqueue("claim-race", 7100)

        started = time.perf_counter()
        claims = await asyncio.gather(*(
            database.claim_job(job_id, self.db_path) for _ in range(16)
        ))

        # This is the safety invariant: observing processing is not the same
        # as having won the conditional UPDATE.
        self.assertEqual(sum(claims), 1)
        self.assertEqual((await _row(
            self.db_path, "SELECT status FROM job_queue WHERE id = ?", (job_id,)
        ))["status"], "processing")
        self.assertLess(time.perf_counter() - started, 5.0)

    async def test_concurrent_duplicate_delivery_does_not_enqueue_twice(self) -> None:
        """Reproduce the webhook's check-then-record race deterministically."""
        message_id = "duplicate-race"
        checked = 0
        all_checked = asyncio.Event()

        async def delivery() -> None:
            nonlocal checked
            duplicate = await database.is_duplicate(message_id, self.db_path)
            self.assertFalse(duplicate)
            checked += 1
            if checked == 2:
                all_checked.set()
            await all_checked.wait()
            await database.record_event(
                message_id=message_id,
                tenant_id="cute-things-demo",
                ticket_id=7200,
                event_type="ticket.message.created",
                author_type="customer",
                raw_payload=json.dumps(_payload(message_id, 7200)),
                db_path=self.db_path,
            )
            await self._enqueue(message_id, 7200)

        await asyncio.gather(delivery(), delivery())

        self.assertEqual(await _count_rows(self.db_path, "webhook_events"), 1)
        # A duplicate webhook must map to one queue job, even when both
        # deliveries pass the initial idempotency read concurrently.
        self.assertEqual(await _count_rows(
            self.db_path, "job_queue", "message_id = 'duplicate-race'"
        ), 1)

    async def test_stale_recovery_requeues_only_old_processing_jobs(self) -> None:
        old_id = await self._enqueue("stale-old", 7300)
        fresh_id = await self._enqueue("stale-fresh", 7301)
        malformed_id = await self._enqueue("stale-malformed", 7302)
        now = datetime.now(timezone.utc)

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """UPDATE job_queue SET status='processing', started_at=?, retry_count=0
                   WHERE id=?""",
                [
                    ((now - timedelta(minutes=31)).isoformat(), old_id),
                    ((now - timedelta(minutes=1)).isoformat(), fresh_id),
                    ("not-an-iso-date", malformed_id),
                ],
            )
            conn.commit()

        try:
            reclaimed = await database.requeue_stale_jobs(10, self.db_path)
        except Exception as exc:
            self.fail(
                f"stale recovery raised {type(exc).__name__}: {exc}; "
                "it must tolerate its own selected rows"
            )
        self.assertEqual(reclaimed, 1)
        self.assertEqual((await _row(
            self.db_path, "SELECT status, retry_count FROM job_queue WHERE id = ?", (old_id,)
        )), {"status": "pending", "retry_count": 1})
        self.assertEqual((await _row(
            self.db_path, "SELECT status, retry_count FROM job_queue WHERE id = ?", (fresh_id,)
        )), {"status": "processing", "retry_count": 0})
        self.assertEqual((await _row(
            self.db_path, "SELECT status, retry_count FROM job_queue WHERE id = ?", (malformed_id,)
        )), {"status": "processing", "retry_count": 0})

    async def test_retry_exhaustion_leaves_job_failed(self) -> None:
        job_id = await self._enqueue("retry-exhaustion", 7400)
        settings = SimpleNamespace(
            db_path_absolute=self.db_path,
            job_timeout=1,
            max_retries=3,
        )

        async def always_fails(_job: dict) -> dict:
            raise RuntimeError("synthetic Hermes outage")

        with patch.object(orchestrator, "process_customer_message", new=always_fails):
            for _ in range(4):
                pending = await database.get_pending_jobs(1, self.db_path)
                if pending:
                    await orchestrator._process_one_job(pending[0], True, settings)
                else:
                    break

        row = await _row(
            self.db_path,
            "SELECT status, retry_count, error FROM job_queue WHERE id = ?",
            (job_id,),
        )
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["retry_count"], 3)
        self.assertIn("synthetic Hermes outage", row["error"])

    async def test_db_lock_contention_eventually_commits_without_loss(self) -> None:
        with sqlite3.connect(self.db_path, timeout=0) as holder:
            holder.execute("BEGIN IMMEDIATE")
            started = time.perf_counter()
            enqueue_task = asyncio.create_task(self._enqueue("locked-write", 7500))
            await asyncio.sleep(0.15)
            holder.commit()
            job_id = await enqueue_task

        self.assertGreater(job_id, 0)
        self.assertEqual(await _count_rows(
            self.db_path, "job_queue", "message_id = 'locked-write'"
        ), 1)
        self.assertLess(time.perf_counter() - started, 4.0)

    async def test_malformed_payload_is_failed_and_not_able_to_crash_worker(self) -> None:
        job_id = await self._enqueue("malformed-json", 7600)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE job_queue SET payload = ? WHERE id = ?", ("{broken", job_id))
            conn.commit()

        settings = SimpleNamespace(
            db_path_absolute=self.db_path,
            job_timeout=1,
            max_retries=0,
        )
        job = (await database.get_pending_jobs(1, self.db_path))[0]
        await orchestrator._process_one_job(job, True, settings)

        row = await _row(
            self.db_path,
            "SELECT status, error FROM job_queue WHERE id = ?",
            (job_id,),
        )
        self.assertEqual(row["status"], "failed")
        self.assertIn("JSONDecodeError", row["error"])

    async def test_out_of_order_messages_keep_independent_results(self) -> None:
        old_event = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        new_event = datetime.now(timezone.utc).isoformat()
        new_id = await database.enqueue_job(
            "cute-things-demo", 7700, "message-new", "ticket.message.created", "customer", True,
            _payload("message-new", 7700, event_created_at=new_event), self.db_path,
        )
        old_id = await database.enqueue_job(
            "cute-things-demo", 7700, "message-old", "ticket.message.created", "customer", True,
            _payload("message-old", 7700, event_created_at=old_event), self.db_path,
        )

        # Complete in the opposite order from arrival and event timestamps.
        self.assertTrue(await database.claim_job(old_id, self.db_path))
        await database.record_ticket_result(
            7700, "message-old", old_id, "normal", "drafted", "old message", False, False, False,
            "old draft", self.db_path,
        )
        await database.complete_job(old_id, db_path=self.db_path)
        self.assertTrue(await database.claim_job(new_id, self.db_path))
        await database.record_ticket_result(
            7700, "message-new", new_id, "high", "sensitive_draft", "new message", True, False, False,
            "new draft", self.db_path,
        )
        await database.complete_job(new_id, db_path=self.db_path)

        results = await database.get_ticket_results(db_path=self.db_path)
        by_message = {result["message_id"]: result for result in results}
        self.assertEqual(set(by_message), {"message-old", "message-new"})
        self.assertEqual(by_message["message-old"]["draft_text"], "old draft")
        self.assertEqual(by_message["message-new"]["draft_text"], "new draft")

    def test_singleton_lock_rejects_second_holder_without_touching_production_lock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="processor-lock-") as temp_dir:
            temp_dir_path = Path(temp_dir)
            fake_source = temp_dir_path / "orchestrator.py"
            lock_path = temp_dir_path / ".processor.lock"
            holder_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                orchestrator._lock_fd = None
                with patch.object(orchestrator, "Path", lambda _path: fake_source):
                    self.assertFalse(orchestrator._acquire_singleton_lock())
                self.assertIsNone(orchestrator._lock_fd)
            finally:
                fcntl.flock(holder_fd, fcntl.LOCK_UN)
                os.close(holder_fd)

            orchestrator._lock_fd = None
            with patch.object(orchestrator, "Path", lambda _path: fake_source):
                self.assertTrue(orchestrator._acquire_singleton_lock())
                orchestrator._release_lock()

    def test_result_persistence_failure_is_fail_soft(self) -> None:
        started = time.perf_counter()
        with patch("urllib.request.urlopen", side_effect=OSError("demo dashboard unavailable")):
            result = orchestrator._save_result_to_webhook(
                ticket_id=7800,
                message_id="persistence-failure",
                job_id=1,
                hermes_result={"priority": "high", "action": "sensitive_draft"},
                draft_text="[SENSITIVE] Demo draft",
            )

        self.assertIsNone(result)
        self.assertLess(time.perf_counter() - started, 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
