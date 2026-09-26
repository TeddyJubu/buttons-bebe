"""Abandoned singleton claims must recover after a rapid restart."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import orchestrator
from bb_webhook import database
from bb_webhook.db import Database


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "queue.db"
        await database.init_db(self.path)
        self.job_id = await database.enqueue_job("test", 1, "synthetic", "created", "customer",
                                                 True, {}, self.path)
        await database.claim_job(self.job_id, self.path)

    async def test_recent_claim_recovers_on_later_sweep_without_restart(self):
        self.assertEqual(await database.requeue_stale_jobs(10, self.path), 0)
        self.assertIsNone(await database.get_next_pending_job(self.path))
        await Database(self.path).execute("UPDATE job_queue SET started_at = ?", (
            (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(),))
        self.assertEqual(await database.requeue_stale_jobs(10, self.path), 1)
        job = await database.get_next_pending_job(self.path)
        self.assertEqual(job["id"], self.job_id)
        self.assertEqual(job["retry_count"], 1)
        self.assertEqual(await database.requeue_stale_jobs(10, self.path), 0)

    async def test_finished_job_is_not_replayed_and_invalid_claim_does_not_strand(self):
        await database.complete_job(self.job_id, db_path=self.path)
        self.assertEqual(await database.requeue_stale_jobs(0, self.path), 0)
        await Database(self.path).execute("UPDATE job_queue SET status='processing', started_at=NULL")
        self.assertEqual(await database.requeue_stale_jobs(10, self.path), 1)

    async def test_recovery_is_bounded_and_leaves_recent_claims_alone(self):
        db = Database(self.path)
        # A large abandoned backlog cannot monopolize one loop iteration.
        for n in range(101):
            await db.execute(
                """INSERT INTO job_queue (tenant_id, ticket_id, message_id, event_type,
                   author_type, status, payload, created_at, started_at)
                   VALUES ('test', 1, ?, 'created', 'customer', 'processing', '{}',
                           '2000-01-01', '2000-01-01')""", (f"old-{n}",))
        self.assertEqual(await database.requeue_stale_jobs(10, self.path), 100)
        self.assertEqual(await database.requeue_stale_jobs(10, self.path), 1)
        row = (await db.fetch("SELECT status FROM job_queue WHERE id=?", (self.job_id,)))[0]
        self.assertEqual(row["status"], "processing")

    async def test_loop_sweeps_periodically_between_jobs_and_releases_lock(self):
        settings = SimpleNamespace(db_path_absolute=self.path, log_format="text", log_level="INFO",
                                   poll_interval=0, job_timeout=150, max_retries=3,
                                   stale_job_minutes=10, heartbeat_seconds=0)
        calls = 0
        clock = 0
        def monotonic():
            return clock
        def _window_result(job):
            return [job] if job is not None else []
        async def next_job(**kwargs):
            nonlocal calls, clock
            calls += 1
            clock += 61
            if calls >= 3:
                orchestrator._shutdown = True
            return None
        async def window_job(**kwargs):
            return _window_result(await next_job())
        try:
            orchestrator._shutdown = False
            with (
                patch.object(orchestrator, "get_settings", return_value=settings),
                patch.object(orchestrator, "setup_logging"),
                patch.object(orchestrator, "_acquire_singleton_lock", return_value=True),
                patch.object(orchestrator, "_release_lock") as release,
                patch.object(orchestrator, "time", SimpleNamespace(monotonic=monotonic)),
                patch.object(orchestrator, "get_pending_job_window", side_effect=window_job),
                patch.object(orchestrator, "requeue_stale_jobs", new_callable=AsyncMock) as recover,
                patch.object(orchestrator, 'check_alert_route',return_value={'status':'ok'}),
            ):
                recover.return_value = 0
                self.assertEqual(await orchestrator.run_processor(), 0)
                self.assertEqual(recover.await_count, 4)  # startup, first probe, two later sweeps
                release.assert_called_once()
        finally:
            orchestrator._shutdown = False


if __name__ == "__main__":
    unittest.main()
