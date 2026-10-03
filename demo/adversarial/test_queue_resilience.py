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
    from bb_webhook.draft_generation import begin_attempt, finish_attempt  # noqa: E402
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
        event = {**_payload(message_id, ticket_id), "tenant_id": "cute-things-demo",
                 "event_type": "ticket.message.created", "author_type": "customer" if customer else "agent",
                 "is_customer_message": customer}
        return await database.ingest_event(event, json.dumps(event), self.db_path)

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
            return await self._enqueue(message_id, 7200)

        received = await asyncio.gather(delivery(), delivery())
        self.assertEqual(sum(job_id is not None for job_id in received), 1)

        self.assertEqual(await _count_rows(self.db_path, "webhook_events"), 1)
        self.assertEqual(await _count_rows(self.db_path, "parsed_messages"), 1)
        # A duplicate webhook must map to one queue job, even when both
        # deliveries pass the initial idempotency read concurrently.
        self.assertEqual(await _count_rows(
            self.db_path, "job_queue", "message_id = 'duplicate-race'"
        ), 1)

    async def test_stale_recovery_requeues_old_and_invalid_claims_but_preserves_fresh_jobs(self) -> None:
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
        self.assertEqual(reclaimed, [old_id, malformed_id])
        self.assertEqual((await _row(
            self.db_path, "SELECT status, retry_count FROM job_queue WHERE id = ?", (old_id,)
        )), {"status": "pending", "retry_count": 1})
        self.assertEqual((await _row(
            self.db_path, "SELECT status, retry_count FROM job_queue WHERE id = ?", (fresh_id,)
        )), {"status": "processing", "retry_count": 0})
        self.assertEqual((await _row(
            self.db_path, "SELECT status, retry_count FROM job_queue WHERE id = ?", (malformed_id,)
        )), {"status": "pending", "retry_count": 1})

    async def test_generation_retry_exhaustion_stops_after_two_durable_delays(self) -> None:
        job_id = await self._enqueue("retry-exhaustion", 7400)
        settings = SimpleNamespace(
            db_path_absolute=self.db_path,
            job_timeout=1,
            max_retries=3,
        )

        async def always_fails(_job: dict) -> dict:
            raise RuntimeError("synthetic Hermes outage")

        with patch.object(orchestrator, "process_customer_message", new=always_fails):
            for index in range(3):
                job = await database.get_next_pending_job(self.db_path)
                self.assertIsNotNone(job)
                await orchestrator._process_one_job(job, True, settings)
                saved = await database.get_job_result(job_id, self.db_path)
                self.assertEqual(saved["generation_error"], "runtime_error")
                self.assertEqual(saved["draft_text"], "")
                self.assertEqual(saved["attempt_count"], index + 1)
                if index < 2:
                    self.assertEqual(saved["generation_state"], "retry_wait")
                    delay = (datetime.fromisoformat(saved["next_retry_at"]) -
                             datetime.fromisoformat(saved["processed_at"])).total_seconds()
                    self.assertEqual(delay, (30, 120)[index])
                    self.assertIsNone(await database.get_next_pending_job(self.db_path))
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute("UPDATE job_queue SET next_attempt_at=? WHERE id=?",
                                     ("2000-01-01T00:00:00+00:00", job_id))
                else:
                    self.assertEqual(saved["generation_state"], "failed")
                    self.assertIsNone(saved["next_retry_at"])

        row = await _row(
            self.db_path,
            "SELECT status, retry_count, error FROM job_queue WHERE id = ?",
            (job_id,),
        )
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["retry_count"], 0)
        self.assertIsNone(await database.get_next_pending_job(self.db_path))
        self.assertEqual(await _count_rows(self.db_path, "draft_generation_attempts"), 3)

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
        job = await database.get_next_pending_job(self.db_path)
        await orchestrator._process_one_job(job, True, settings)

        row = await _row(
            self.db_path,
            "SELECT status, error FROM job_queue WHERE id = ?",
            (job_id,),
        )
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["error"], "runtime_error")
        saved = await database.get_job_result(job_id, self.db_path)
        self.assertEqual(saved["generation_state"], "retry_wait")
        self.assertEqual(saved["draft_text"], "")
        self.assertTrue(saved["review_required"])
        self.assertIsNone(await database.get_next_pending_job(self.db_path))

    async def test_sensitive_generation_failures_keep_priority_and_alert_after_publication(self) -> None:
        requests = (
            ("refund", "I need a full refund for this order."),
            ("dispute", "I am opening a dispute for this payment."),
            ("defect", "The product has a defect and arrived broken."),
            ("cancel", "Please cancel my order before it ships."),
            ("urgent", "This is urgent; please change my shipping address."),
            ("followup", "Following up on my urgent request; I still have no response."),
        )
        settings = SimpleNamespace(db_path_absolute=self.db_path, job_timeout=1, max_retries=3)

        async def unavailable_model(_job):
            raise RuntimeError("synthetic generation failure")

        for index, (label, text) in enumerate(requests):
            with self.subTest(request=label):
                event = {**_payload("sensitive-" + label, 7900 + index),
                         "message_text": text, "ticket_subject": "Support request",
                         "tenant_id": "cute-things-demo", "event_type": "ticket.message.created",
                         "author_type": "customer", "is_customer_message": True}
                job_id = await database.ingest_event(event, json.dumps(event), self.db_path)
                job = next(row for row in await database.get_pending_job_window(20, self.db_path)
                           if row["id"] == job_id)
                observed = []

                def alert(**_fields):
                    with sqlite3.connect(self.db_path) as conn:
                        observed.append(conn.execute(
                            "SELECT generation_state,draft_text FROM ticket_results WHERE job_id=?",
                            (job_id,)).fetchone())
                    return False

                with patch.object(orchestrator, "process_customer_message", new=unavailable_model), \
                        patch.object(orchestrator, "send_whatsapp", side_effect=alert) as notify:
                    await orchestrator._process_one_job(job, True, settings)
                    saved = await database.get_job_result(job_id, self.db_path)
                    self.assertIn(saved["priority"], {"high", "critical"})
                    self.assertTrue(saved["notify_owner"])
                    self.assertEqual(saved["generation_state"], "retry_wait")
                    self.assertEqual(saved["draft_text"], "")
                    self.assertEqual(observed, [("retry_wait", "")])
                    notify.assert_called_once()
                    self.assertEqual(notify.call_args.kwargs["max_retries"], 0)
                    await orchestrator._notify_owner_once(job, saved, self.db_path)
                    notify.assert_called_once()
                audit = await _row(self.db_path,
                    "SELECT status FROM owner_alert_attempts WHERE job_id=?", (job_id,))
                self.assertEqual(audit["status"], "uncertain")

    async def test_out_of_order_messages_keep_independent_results(self) -> None:
        old_event = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        new_event = datetime.now(timezone.utc).isoformat()
        async def intake(message_id, created_at):
            event = {**_payload(message_id, 7700, event_created_at=created_at),
                     "tenant_id": "cute-things-demo", "event_type": "ticket.message.created",
                     "author_type": "customer", "is_customer_message": True}
            return await database.ingest_event(event, json.dumps(event), self.db_path)

        async def publish(job_id, message_id, draft):
            self.assertTrue(await database.claim_job(job_id, self.db_path))
            attempt_id = await begin_attempt(job_id, self.db_path)
            self.assertIsNotNone(attempt_id)
            state = await finish_attempt({"ticket_id": 7700, "message_id": message_id,
                "job_id": job_id, "generation_attempt_id": attempt_id, "generation_state": "ready",
                "priority": "normal", "action": "drafted", "reason": message_id,
                "notify_owner": False, "draft_text": draft}, self.db_path)
            self.assertEqual(state, "ready")
            self.assertTrue(await database.complete_job(job_id, db_path=self.db_path, require_result=True))

        old_id = await intake("message-old", old_event)
        await publish(old_id, "message-old", "old draft")
        new_id = await intake("message-new", new_event)
        await publish(new_id, "message-new", "new draft")
        self.assertEqual((await database.get_job_result(old_id, self.db_path))["draft_text"], "old draft")
        self.assertEqual((await database.get_job_result(new_id, self.db_path))["draft_text"], "new draft")

        late_id = await intake("message-late", old_event)
        self.assertTrue(await database.claim_job(late_id, self.db_path))
        self.assertIsNone(await begin_attempt(late_id, self.db_path))
        late = await _row(self.db_path, "SELECT status,error FROM job_queue WHERE id=?", (late_id,))
        self.assertEqual(late, {"status": "skipped", "error": "new_customer_message_refresh_ticket"})
        self.assertIsNone(await database.get_job_result(late_id, self.db_path))
        self.assertEqual((await database.get_job_result(new_id, self.db_path))["draft_text"], "new draft")

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

    def test_result_persistence_failure_propagates_without_false_acknowledgement(self) -> None:
        started = time.perf_counter()
        with patch.dict(os.environ, {"DEMO_MODE": "0", "DASHBOARD_RESULT_URL":
                                    "http://127.0.0.1:8000/dashboard/api/results"}), \
                patch.object(orchestrator, "get_settings", return_value=SimpleNamespace(
                    processor_result_secret="synthetic-result-secret-0123456789")), \
                patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = OSError("demo dashboard unavailable")
            with self.assertRaisesRegex(OSError, "demo dashboard unavailable"):
                orchestrator._save_result_to_webhook(
                    ticket_id=7800,
                    message_id="persistence-failure",
                    job_id=1,
                    hermes_result={"priority": "high", "action": "sensitive_draft",
                                   "generation_attempt_id": 1, "generation_state": "ready"},
                    draft_text="[SENSITIVE] Demo draft",
                )
            self.assertEqual(opener.return_value.open.call_count, 1)

        self.assertLess(time.perf_counter() - started, 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
