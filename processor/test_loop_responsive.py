"""A stuck synchronous job blocks the loop. This file records why.

The Hermes subprocess now uses hermes_runner.process.run_bounded: its own
monotonic deadline, output caps, and process-group termination preserve the
fallback even though the outer asyncio deadline is not preemptive. No worker
thread was introduced. The historical subprocess.run examples below explain
why simply moving this work into asyncio.to_thread is unsafe. SIGTERM service
recovery additionally relies on systemd KillMode=control-group; these tests do
not certify the live unit. A future async conversion must preserve the fallback
and one-job invariant, with cleanup completed before claiming another job.

THE PROBLEM, WHICH IS REAL

`deterministic_classify` and `process_ticket_with_hermes` are synchronous and
CPU-bound, and they are called directly inside the job coroutine.
`asyncio.wait_for` cannot interrupt a blocked synchronous call. So while a
pathological regex spins:

  * the event loop is frozen,
  * `settings.job_timeout` never fires,
  * the idle heartbeat line at the bottom of `run_processor()` is never
    emitted - and `heartbeat.sh` reads exactly that line to decide the loop is
    wedged, so the watchdog stays quiet too,
  * the exclusive flock stays held.

That is why six catastrophic-backtracking bugs were BLOCKERS rather than slow
tickets: one email stopped the shop, with every alerting path disabled by the
same stall that caused the problem.

WHY IT IS NOT FIXED WITH asyncio.to_thread

It was, for one round, and the fix was WORSE than the problem. Review measured
two consequences, both on real code with only `subprocess.run` faked:

  1. `hermes_runner` passes `settings.job_timeout` to `subprocess.run`, and
     the orchestrator passes the SAME value to `asyncio.wait_for`. The outer
     deadline starts earlier - before classify, should_draft, marker
     neutralisation and prompt building - by 6ms on a small ticket and 603ms
     on a 2MB thread. Moving the work to a thread made the outer timeout
     actually fire, which made the inner `except subprocess.TimeoutExpired ->
     _FALLBACK_RESULT` branch UNREACHABLE. A chargeback ticket then went:

         with to_thread : 4 Hermes invocations, 0 dashboard rows, 0 owner
                          alerts, job failed after 3 retries
         without        : 1 invocation, 1 dashboard row, 1 owner alert, done

  2. `wait_for` cancellation does not stop the worker thread, so the loop
     claimed the next job immediately. Measured 5 concurrent
     `process_ticket_with_hermes` calls, 4 of them for the SAME ticket; two
     poisoned emails exhausted the default executor and drove 5 innocent
     tickets to `status=failed` without them ever reaching the classifier.
     SIGTERM also began blocking until orphaned threads drained (63s).

Trading a hypothetical freeze for measured, silent loss of owner alerts on
chargeback tickets is a bad trade, so it was reverted.

WHAT COVERS THE RISK INSTEAD

`ReDoSTests` in test_classifier_rules.py measures every pattern for
superlinear growth, and a structure-aware fuzz of all 179 patterns across
the classifier package, draft_cleaner.py and every hermes_runner.* module found zero remaining
instances while still catching all six historical ones. The class is closed by
making the patterns linear, not by trying to survive a non-linear one.

DOING IT PROPERLY, IF IT IS EVER NEEDED

Not a one-line change. It needs, at minimum:
  * a dedicated ThreadPoolExecutor(max_workers=1), not the default pool;
  * a refusal to claim a new job while a previous worker future is pending -
    log CRITICAL and alert rather than starting another;
  * an inner Hermes timeout strictly smaller than the outer one, so the
    designed fallback still fires;
  * `except asyncio.TimeoutError` persisting _FALLBACK_RESULT and notifying
    the owner instead of silently requeuing;
  * not counting a timeout whose work never started against `retry_count`.

The tests below pin the CURRENT behaviour so nobody re-derives this from
scratch, and so the day someone does the work above, they turn red and have
to be replaced deliberately.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time
import unittest
from pathlib import Path

PROCESSOR_DIR = Path(__file__).resolve().parent
WEBHOOK_SRC = PROCESSOR_DIR.parent / "webhook" / "src"
sys.path[:0] = [str(PROCESSOR_DIR), str(WEBHOOK_SRC)]


class BlockingIsAKnownLimitationTests(unittest.TestCase):
    """Pin the shape of the problem, so the reasoning above stays checkable."""

    SPIN = 2.0
    TIMEOUT = 0.3

    @staticmethod
    def _spin(seconds: float) -> str:
        started = time.perf_counter()
        while time.perf_counter() - started < seconds:
            pass
        return "finished"

    def test_a_direct_synchronous_call_defeats_the_timeout(self):
        """The mechanism, demonstrated rather than asserted about."""
        async def job():
            return self._spin(self.SPIN)

        async def main():
            return await asyncio.wait_for(job(), timeout=self.TIMEOUT)

        started = time.perf_counter()
        self.assertEqual(asyncio.run(main()), "finished",
                         "wait_for cannot interrupt blocked sync code")
        self.assertGreater(time.perf_counter() - started, self.SPIN * 0.8)

    def test_to_thread_would_let_it_fire(self):
        """...and the fix that is NOT applied, so the trade-off is legible."""
        async def main():
            started = time.perf_counter()
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._spin, self.SPIN),
                    timeout=self.TIMEOUT)
            except asyncio.TimeoutError:
                return time.perf_counter() - started
            return None

        elapsed = asyncio.run(main())
        self.assertIsNotNone(elapsed)
        self.assertLess(elapsed, self.SPIN * 0.8)


class TheTimersMustNotBeReorderedTests(unittest.TestCase):
    """The specific trap that made to_thread a regression.

    Both timeouts are `settings.job_timeout`, and the outer one starts first.
    That is harmless only while the outer timer cannot fire before the inner
    one - i.e. only while the work blocks the loop. Anything that makes the
    outer timeout effective MUST also give the inner one a smaller budget,
    or the Hermes-timeout fallback becomes dead code.
    """

    def test_generation_has_a_separate_smaller_budget(self):
        from hermes_runner import runner as hermes_runner_runner
        import orchestrator

        source = inspect.getsource(hermes_runner_runner.process_ticket_with_hermes)
        from config import ProcessorSettings
        self.assertEqual(ProcessorSettings.model_fields['hermes_timeout'].default,240)
        self.assertEqual(ProcessorSettings.model_fields['job_timeout'].default,270)
        configured_timeout = "'hermes_timeout'" in source
        subprocess_timeout = (
            "timeout=settings.job_timeout" in source or "timeout=timeout" in source
        )
        self.assertTrue(configured_timeout and subprocess_timeout,
                        "the Hermes subprocess must use the configured timeout")
        loop = inspect.getsource(orchestrator._run_with_timeout)
        self.assertIn("asyncio.wait_for", loop)

    def test_the_hermes_timeout_fallback_is_still_reachable(self):
        """It is only reachable because the outer timer cannot pre-empt it."""
        import orchestrator

        source = inspect.getsource(orchestrator.process_customer_message)
        self.assertNotIn("to_thread", source,
                         "moving these calls off the loop makes the outer "
                         "timeout fire first and turns hermes_runner's "
                         "subprocess.TimeoutExpired branch into dead code - "
                         "measured: 0 dashboard rows and 0 owner alerts on a "
                         "chargeback ticket. See this module's docstring.")

    def test_only_one_job_can_be_in_flight(self):
        """Cancelling a wait_for does not stop a worker thread.

        With the calls inline, a cancelled job cannot leave work running, so
        the loop's one-job-at-a-time contract holds. Restoring to_thread
        without a bounded single-worker executor broke it: 5 concurrent runs,
        4 for the same ticket.
        """
        import orchestrator

        source = inspect.getsource(orchestrator.process_customer_message)
        for blocking in ("deterministic_classify(", "process_ticket_with_hermes("):
            with self.subTest(call=blocking):
                self.assertIn(blocking, source)


if __name__ == "__main__":
    unittest.main()
