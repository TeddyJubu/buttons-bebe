"""Queue completion requires a durable draft; retries cannot regenerate it."""
from __future__ import annotations

import tempfile
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import orchestrator
from bb_webhook import database
from bb_webhook.db import Database


class ResultDurabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "queue.db"
        await database.init_db(self.path)
        self.settings = SimpleNamespace(db_path_absolute=self.path, job_timeout=10, max_retries=3)
        self.job_id = await database.ingest_event(
            dict(tenant_id="test", ticket_id=123, message_id="synthetic", event_type="created",
                 author_type="customer", is_customer_message=True,
                 message_text="Synthetic question"), "{}", self.path)
        self.job = await database.get_next_pending_job(self.path)
        orchestrator._classification_cache.clear()

    async def save(self, *, alert=True, draft="First draft"):
        await database.record_ticket_result(123, "synthetic", self.job_id, "high", "sensitive_draft",
                                            "Synthetic reason", alert, False, False, draft, self.path)

    async def status(self):
        return dict((await Database(self.path).fetch("SELECT * FROM job_queue WHERE id=?", (self.job_id,)))[0])

    async def test_failure_before_result_write_does_not_complete_or_alert(self):
        with (
            patch.object(orchestrator, "process_customer_message", new_callable=AsyncMock) as process,
            patch.object(orchestrator, "send_whatsapp") as notify,
        ):
            process.side_effect = OSError("Synthetic unavailable result endpoint")
            await orchestrator._process_one_job(self.job, True, self.settings)
            self.assertEqual((await self.status())["status"], "pending")
            saved=await database.get_job_result(self.job_id,self.path)
            self.assertEqual(saved['generation_state'],'retry_wait')
            self.assertEqual(saved['draft_text'],'')
            self.assertIsNone(await database.get_next_pending_job(self.path))
            notify.assert_not_called()

    async def test_successful_coroutine_without_saved_result_cannot_complete(self):
        with patch.object(orchestrator, "process_customer_message", AsyncMock(return_value={"action": "drafted"})):
            await orchestrator._process_one_job(self.job, True, self.settings)
        self.assertEqual((await self.status())["status"], "pending")
        self.assertEqual((await self.status())["generation_cycle_attempts"], 1)
        self.assertIsNotNone((await self.status())['next_attempt_at'])

    async def test_generation_crash_preserves_current_dispute_urgency_and_alerts_once(self):
        import json
        self.job['payload'] = json.dumps(dict(message_text='I am filing a chargeback for order #87654.'))
        with patch.object(orchestrator, 'process_customer_message', AsyncMock(side_effect=TimeoutError())), \
             patch.object(orchestrator, 'send_whatsapp', return_value=True) as notify:
            await orchestrator._process_one_job(self.job, True, self.settings)
            saved = await database.get_job_result(self.job_id, self.path)
            self.assertEqual(saved['priority'], 'critical')
            self.assertEqual(saved['generation_state'], 'retry_wait')
            self.assertTrue(saved['notify_owner'])
            notify.assert_called_once()
            await orchestrator._notify_recovered_result(self.job, self.path)
            notify.assert_called_once()

    async def test_result_written_but_response_lost_retry_skips_model_and_alerts_once(self):
        async def response_lost(_job):
            await self.save()
            raise TimeoutError("Synthetic acknowledgement lost")
        with patch.object(orchestrator, "process_customer_message", side_effect=response_lost):
            await orchestrator._process_one_job(self.job, True, self.settings)
        self.assertEqual((await self.status())["status"], "pending")
        retry = await database.get_next_pending_job(self.path)
        with (
            patch.object(orchestrator, "process_customer_message", new_callable=AsyncMock) as process,
            patch.object(orchestrator, "send_whatsapp", return_value=True) as notify,
        ):
            await orchestrator._process_one_job(retry, True, self.settings)
            process.assert_not_called()
            notify.assert_called_once()
            self.assertEqual(notify.call_args.kwargs["max_retries"], 0)
            # Simulate a crash after notification but before queue completion.
            await Database(self.path).execute("UPDATE job_queue SET status='pending' WHERE id=?", (self.job_id,))
            await orchestrator._process_one_job(await database.get_next_pending_job(self.path), True, self.settings)
            notify.assert_called_once()
        self.assertEqual((await self.status())["status"], "done")
        tickets = await database.get_dashboard_tickets(db_path=self.path)
        self.assertEqual(tickets[0]["owner_alert_status"], "accepted")
        self.assertEqual((await database.get_result_stats(self.path))["owner_alerts_need_attention"], 0)
        self.assertEqual((await database.get_job_result(self.job_id, self.path))["draft_text"], "First draft")

    async def test_crash_after_alert_claim_is_visible_uncertain_and_never_resent(self):
        await self.save()
        self.assertTrue(await database.claim_owner_alert(self.job_id, self.path))
        with patch.object(orchestrator, "send_whatsapp") as notify:
            await orchestrator._process_one_job(self.job, True, self.settings)
            notify.assert_not_called()
        stats = await database.get_result_stats(self.path)
        self.assertEqual(stats["owner_alerts_need_attention"], 1)
        tickets = await database.get_dashboard_tickets(db_path=self.path)
        self.assertEqual(tickets[0]["owner_alert_status"], "uncertain")
        self.assertEqual((await self.status())["status"], "done")

    async def test_first_result_is_immutable_and_mismatched_identity_cannot_complete(self):
        await self.save(draft="Approved draft")
        await self.save(draft="Retry generated something else")
        self.assertEqual((await database.get_job_result(self.job_id, self.path))["draft_text"], "Approved draft")
        await database.claim_job(self.job_id, self.path)
        await Database(self.path).execute("UPDATE ticket_results SET message_id='wrong'")
        self.assertIsNone(await database.get_job_result(self.job_id, self.path))
        self.assertFalse(await database.complete_job(self.job_id, db_path=self.path, require_result=True))
        self.assertEqual((await self.status())["status"], "processing")

    async def test_stale_retry_exhaustion_is_failed_with_operator_visible_reason(self):
        await database.claim_job(self.job_id, self.path)
        await Database(self.path).execute("UPDATE job_queue SET started_at='2000-01-01', retry_count=3")
        self.assertEqual(await database.requeue_stale_jobs(10, self.path, max_retries=3), 1)
        row = await self.status()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["retry_count"], 3)
        self.assertIn("operator review required", row["error"])
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(await database.requeue_stale_jobs(10, self.path, max_retries=3), 0)


class ResultAcknowledgementTests(unittest.TestCase):
    def test_durable_alert_disables_ambiguous_transport_retries(self):
        import whatsapp_notifier
        with (
            patch.dict(os.environ, {"WHATSAPP_SEND_URL": "http://127.0.0.1:8085/test/send",
                                    "WA_SEND_SECRET": "synthetic-test-secret"}, clear=True),
            patch("urllib.request.urlopen", side_effect=TimeoutError("synthetic timeout")) as transport,
            patch.object(whatsapp_notifier.time, "sleep") as sleep,
        ):
            self.assertFalse(whatsapp_notifier.send_whatsapp(123, "test", "", "test", "test", max_retries=0))
            transport.assert_called_once()
            sleep.assert_not_called()

    def test_non_acknowledgement_2xx_does_not_silently_pass(self):
        response = Mock(status=200)
        response.read.return_value = b'{"status":"not_saved"}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("urllib.request.OpenerDirector.open", return_value=response), patch.object(orchestrator,"get_settings",return_value=SimpleNamespace(processor_result_secret="synthetic-result-secret-0123456789")):
            with self.assertRaisesRegex(RuntimeError, "acknowledgement"):
                orchestrator._save_result_to_webhook(123, "synthetic", 1, {})


if __name__ == "__main__":
    unittest.main()
