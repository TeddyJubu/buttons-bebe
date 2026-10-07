"""The job deadline works while Hermes runs in its retained worker thread.

The Hermes subprocess has its own shorter, bounded deadline (240 seconds by
default). The orchestrator gives the whole job 270 seconds and runs synchronous
Hermes work on a dedicated one-worker executor. If the outer job expires, it
stops waiting and never publishes that abandoned result; the retained future
prevents a second Hermes invocation until the first thread and child process
have ended.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROCESSOR_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROCESSOR_DIR.parent
WEBHOOK_SRC = REPO_ROOT / "webhook" / "src"
sys.path[:0] = [str(REPO_ROOT), str(PROCESSOR_DIR), str(WEBHOOK_SRC)]

# Processor config normally reads the root .env at import. Tests must not read
# that file, so import through the repository's offline guard and leave the
# settings model with env_file=None afterward.
from demo.adversarial.offline_imports import without_root_dotenv  # noqa: E402

with without_root_dotenv():
    import orchestrator  # noqa: E402
    from config import ProcessorSettings  # noqa: E402
    from hermes_runner import runner as hermes_runner_runner  # noqa: E402


class JobTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def _wait_for_thread_event(self, event: threading.Event, timeout: float = 1.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not event.is_set():
            if asyncio.get_running_loop().time() >= deadline:
                self.fail("fake Hermes runner did not reach the expected point")
            await asyncio.sleep(0.005)

    async def test_timeout_is_prompt_responsive_and_does_not_overlap_followup(self):
        settings = SimpleNamespace(job_timeout=0.08)
        budget = settings.job_timeout
        first_started = threading.Event()
        finish_first = threading.Event()
        second_started = threading.Event()
        call_lock = threading.Lock()
        calls = 0

        def slow_runner(**_kwargs):
            nonlocal calls
            with call_lock:
                calls += 1
                call_number = calls
            if call_number == 1:
                first_started.set()
                finish_first.wait(timeout=3)
            else:
                second_started.set()
            return {
                "action": "drafted",
                "priority": "normal",
                "notify_owner": False,
                "draft_text": "A synthetic reply.",
            }

        async def tick_the_loop():
            await asyncio.sleep(0.01)
            return "loop remained responsive"

        def job(job_id: int, ticket_id: int) -> dict:
            return {
                "id": job_id,
                "message_id": f"synthetic-{job_id}",
                "payload": json.dumps({
                    "ticket_id": ticket_id,
                    "message_id": f"synthetic-{job_id}",
                    "message_text": "A synthetic product question.",
                }),
            }

        classifier_result = {
            "priority": "NORMAL",
            "sensitive": False,
            "reason": "synthetic test",
            "should_notify_owner": False,
        }
        first: asyncio.Task | None = None
        waiting_job: asyncio.Task | None = None
        shutdown: asyncio.Task | None = None
        claim_check: asyncio.Task | None = None
        followup: asyncio.Task | None = None
        try:
            with (
                patch.object(orchestrator, "deterministic_classify", return_value=classifier_result),
                patch.object(orchestrator, "process_ticket_with_hermes", side_effect=slow_runner),
                patch.object(orchestrator, "_save_result_to_webhook") as save,
            ):
                timeout_started = time.perf_counter()
                first = asyncio.create_task(orchestrator._run_with_timeout(
                    orchestrator.process_customer_message(job(1, 101)),
                    timeout=settings.job_timeout,
                    job_id=1,
                ))
                await self._wait_for_thread_event(first_started)

                self.assertEqual(
                    await asyncio.wait_for(tick_the_loop(), timeout=0.2),
                    "loop remained responsive",
                )
                with self.assertRaises(asyncio.TimeoutError):
                    await first
                elapsed = time.perf_counter() - timeout_started
                self.assertGreaterEqual(elapsed, budget * 0.7)
                self.assertLess(elapsed, 0.5)

                # A second job can time out while waiting for the first worker.
                # It must then disappear without starting later in the background.
                waiting_job = asyncio.create_task(orchestrator._run_with_timeout(
                    orchestrator.process_customer_message(job(2, 202)),
                    timeout=0.04,
                    job_id=2,
                ))
                with self.assertRaises(asyncio.TimeoutError):
                    await waiting_job

                # Shutdown must drain the retained worker, and the next
                # customer job must wait before it is even claimed.
                settings_for_claim = SimpleNamespace(db_path_absolute=Path("unused-test-db"))
                with (
                    patch.object(
                        orchestrator, "claim_job", new_callable=AsyncMock,
                        return_value=False,
                    ) as claim,
                    patch.object(orchestrator, "_release_lock") as release_lock,
                ):
                    shutdown = asyncio.create_task(orchestrator._cleanup_processor(None))
                    claim_check = asyncio.create_task(orchestrator._process_one_job(
                        job(3, 303), is_customer=True, settings=settings_for_claim,
                    ))
                    try:
                        await asyncio.sleep(0.03)
                        self.assertFalse(shutdown.done())
                        release_lock.assert_not_called()
                        claim.assert_not_awaited()
                        with call_lock:
                            self.assertEqual(calls, 1)
                        self.assertFalse(second_started.is_set())
                        save.assert_not_called()

                        finish_first.set()
                        await asyncio.wait_for(shutdown, timeout=1.0)
                        await asyncio.wait_for(claim_check, timeout=1.0)
                        release_lock.assert_called_once()
                        claim.assert_awaited_once()
                    finally:
                        finish_first.set()
                        for task in (shutdown, claim_check):
                            if not task.done():
                                try:
                                    await asyncio.wait_for(task, timeout=1.0)
                                except Exception:
                                    pass

                # Once the old worker has ended, a fresh call can run and
                # publish normally. The timed-out waiting job never runs later.
                followup = asyncio.create_task(
                    orchestrator.process_customer_message(job(4, 404))
                )
                result = await asyncio.wait_for(followup, timeout=1.0)
                self.assertEqual(result["ticket_id"], 404)
                self.assertTrue(second_started.is_set())
                with call_lock:
                    self.assertEqual(calls, 2)
                save.assert_called_once()
                self.assertEqual(save.call_args.kwargs["ticket_id"], 404)
        finally:
            # Do not leave the dedicated worker waiting if an assertion fails.
            for task in (first, waiting_job):
                if task is not None and not task.done():
                    task.cancel()
            finish_first.set()
            for task in (first, waiting_job):
                if task is not None and not task.done():
                    try:
                        await asyncio.wait_for(task, timeout=1.0)
                    except Exception:
                        pass
            if shutdown is not None and not shutdown.done():
                try:
                    await asyncio.wait_for(shutdown, timeout=1.0)
                except Exception:
                    pass
            else:
                try:
                    await asyncio.wait_for(orchestrator._wait_for_hermes_worker(), timeout=1.0)
                except Exception:
                    pass
            if followup is not None and not followup.done():
                try:
                    await asyncio.wait_for(followup, timeout=1.0)
                except Exception:
                    pass

    def test_generation_and_job_budgets_remain_ordered(self):
        source = inspect.getsource(hermes_runner_runner.process_ticket_with_hermes)
        self.assertEqual(ProcessorSettings.model_fields["hermes_timeout"].default, 240)
        self.assertEqual(ProcessorSettings.model_fields["job_timeout"].default, 270)
        self.assertIn("'hermes_timeout'", source)
        self.assertIn("timeout=timeout", source)
        self.assertEqual(orchestrator._hermes_executor._max_workers, 1)

    def test_result_post_socket_timeout_is_not_a_job_deadline(self):
        result_secret = "synthetic_result_secret_0123456789abcdefgh"
        with (
            patch.object(
                orchestrator,
                "get_settings",
                return_value=SimpleNamespace(processor_result_secret=result_secret),
            ),
            patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError("socket timeout")),
        ):
            with self.assertRaisesRegex(RuntimeError, "Result API POST timed out"):
                orchestrator._save_result_to_webhook(
                    123,
                    "synthetic-message",
                    1,
                    {"generation_attempt_id": 1, "generation_state": "ready"},
                )


if __name__ == "__main__":
    unittest.main()
