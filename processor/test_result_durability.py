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
from bb_webhook.draft_generation import begin_attempt, finish_attempt, revision


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
                 created_at="2026-09-25T00:00:00+00:00",
                 message_text="Synthetic question"), "{}", self.path)
        self.job = await database.get_next_pending_job(self.path)
        orchestrator._classification_cache.clear()

    async def save(self, *, alert=True, draft="First draft", attempt=None):
        if attempt is None:
            attempt = getattr(self, 'attempt', None)
        if attempt is None:
            self.assertTrue(await database.claim_job(self.job_id, self.path))
            attempt = self.attempt = await begin_attempt(self.job_id, self.path)
        return await finish_attempt(dict(ticket_id=123, message_id="synthetic", job_id=self.job_id,
            generation_attempt_id=attempt, generation_state='ready', priority='high',
            action='sensitive_draft', reason='Synthetic reason', notify_owner=alert,
            draft_text=draft), self.path)

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

    async def unknown_source_time(self, text):
        import json
        payload = json.dumps(dict(message_text=text, ticket_id=123, message_id='synthetic'))
        await Database(self.path).execute(
            "UPDATE parsed_messages SET created_at=NULL,message_text=? WHERE ticket_id=? AND message_id=?",
            (text, 123, 'synthetic'))
        await Database(self.path).execute("UPDATE job_queue SET payload=? WHERE id=?", (payload, self.job_id))
        self.job['payload'] = payload

    async def test_unknown_sensitive_chronology_preserves_review_and_one_owed_alert_across_restart(self):
        await self.unknown_source_time('I am filing a chargeback for order #87654.')
        with patch.object(orchestrator, 'process_customer_message', AsyncMock()) as process, \
             patch.object(orchestrator, 'send_whatsapp', return_value=True) as notify:
            await orchestrator._process_one_job(self.job, True, self.settings)
            process.assert_not_called()
            notify.assert_not_called()  # Crash here before the singleton's alert drain.
            saved = await database.get_job_result(self.job_id, self.path)
            self.assertEqual(saved['priority'], 'critical')
            self.assertEqual(saved['generation_state'], 'needs_review')
            self.assertEqual(saved['generation_error'], 'message_chronology_unavailable')
            self.assertEqual(saved['draft_text'], '')
            self.assertTrue(saved['review_required'])
            self.assertTrue(saved['notify_owner'])
            self.assertFalse(saved['gorgias_priority_set'])
            self.assertFalse(saved['note_posted'])
            self.assertIsNone(saved['generation_attempt_id'])
            self.assertEqual(saved['attempt_count'], 0)
            self.assertIn('source message timestamp', saved['staff_next_step'])
            self.assertEqual((await self.status())['status'], 'skipped')
            self.assertEqual((await self.status())['error'], 'message_chronology_unavailable')
            self.assertEqual(await database.pending_recovery_alerts(5, self.path), [self.job_id])
            self.assertEqual((await Database(self.path).fetch('SELECT COUNT(*) AS n FROM draft_generation_attempts'))[0]['n'], 0)
            await database.init_db(self.path)  # Restart preserves the duty, without a model attempt.
            settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
            await orchestrator._recover_stale_jobs(settings)
            await database.init_db(self.path)
            await orchestrator._process_one_job(self.job, True, self.settings)
            await orchestrator._recover_stale_jobs(settings)
            process.assert_not_called()
            notify.assert_called_once()
            self.assertEqual(notify.call_args.kwargs['max_retries'], 0)
            self.assertEqual(await database.pending_recovery_alerts(5, self.path), [])
            self.assertEqual((await database.get_job_result(self.job_id, self.path))['draft_text'], '')

    async def test_unknown_normal_chronology_requires_review_without_owner_alert(self):
        await self.unknown_source_time('What are the measurements?')
        with patch.object(orchestrator, 'process_customer_message', AsyncMock()) as process, \
             patch.object(orchestrator, 'send_whatsapp') as notify:
            await orchestrator._process_one_job(self.job, True, self.settings)
            saved = await database.get_job_result(self.job_id, self.path)
            self.assertEqual(saved['priority'], 'normal')
            self.assertEqual(saved['generation_state'], 'needs_review')
            self.assertFalse(saved['notify_owner'])
            self.assertEqual(saved['draft_text'], '')
            self.assertEqual(await database.pending_recovery_alerts(5, self.path), [])
            settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
            await orchestrator._recover_stale_jobs(settings)
            process.assert_not_called()
            notify.assert_not_called()

    async def test_unknown_chronology_cannot_override_prior_human_reservation(self):
        from bb_webhook.send_intents import IntentStore
        import uuid
        await IntentStore(self.path).reserve(operation_id=str(uuid.uuid4()), actor_id='owner', kind='note',
            ticket_id=123, source_message_id='synthetic', text='Staff action',
            draft_revision=revision(''))
        await self.unknown_source_time('I am filing a chargeback for order #87654.')
        with patch.object(orchestrator, 'process_customer_message', AsyncMock()) as process:
            await orchestrator._process_one_job(self.job, True, self.settings)
            process.assert_not_called()
        self.assertEqual((await self.status())['error'], 'human_action_already_initiated')
        self.assertIsNone(await database.get_job_result(self.job_id, self.path))
        self.assertEqual(await database.pending_recovery_alerts(5, self.path), [])

    async def test_unknown_chronology_cannot_replace_successful_result(self):
        await self.save(alert=False, draft='Verified existing draft')
        before = await database.get_job_result(self.job_id, self.path)
        await self.unknown_source_time('I am filing a chargeback for order #87654.')
        self.assertIsNone(await begin_attempt(self.job_id, self.path,
            priority_context=dict(priority='critical', notify_owner=True, reason='Chargeback')))
        self.assertEqual(await database.get_job_result(self.job_id, self.path), before)
        self.assertEqual(await database.pending_recovery_alerts(5, self.path), [])

    async def seed_prior_failed_urgent(self, error):
        self.assertTrue(await database.claim_job(self.job_id, self.path))
        attempt = await begin_attempt(self.job_id, self.path,
            priority_context=dict(priority='high', notify_owner=True, reason='Verified refund request'))
        await finish_attempt(dict(ticket_id=123, message_id='synthetic', job_id=self.job_id,
            generation_attempt_id=attempt, generation_state='failed', generation_error=error,
            priority='high', notify_owner=True, action='sensitive_draft', reason='Verified refund request',
            draft_text='', recovery_alert=True), self.path)

    async def check_prior_urgent_duty_survives_chronology_refusal(self, error):
        await self.seed_prior_failed_urgent(error)
        await Database(self.path).execute("UPDATE job_queue SET status='pending',next_attempt_at=NULL WHERE id=?", (self.job_id,))
        await self.unknown_source_time('What are the measurements?')
        with patch.object(orchestrator, 'process_customer_message', AsyncMock()) as process, \
             patch.object(orchestrator, 'send_whatsapp', return_value=True) as notify:
            await orchestrator._process_one_job(self.job, True, self.settings)
            saved = await database.get_job_result(self.job_id, self.path)
            self.assertEqual(saved['priority'], 'high')
            self.assertTrue(saved['notify_owner'])
            self.assertIn('Verified refund request', saved['reason'])
            self.assertEqual(saved['generation_state'], 'needs_review')
            self.assertEqual(saved['draft_text'], '')
            self.assertEqual(saved['attempt_count'], 1)
            self.assertIsNone(saved['generation_attempt_id'])
            self.assertEqual((await Database(self.path).fetch('SELECT COUNT(*) AS n FROM draft_generation_attempts'))[0]['n'], 1)
            self.assertEqual(await database.pending_recovery_alerts(5, self.path), [self.job_id])
            settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
            await database.init_db(self.path)
            await orchestrator._recover_stale_jobs(settings)
            await orchestrator._recover_stale_jobs(settings)
            process.assert_not_called()
            notify.assert_called_once()

    async def test_failed_urgent_duty_survives_chronology_refusal(self):
        await self.check_prior_urgent_duty_survives_chronology_refusal('authentication')

    async def test_retry_wait_urgent_duty_survives_chronology_refusal(self):
        await self.check_prior_urgent_duty_survives_chronology_refusal('timeout')

    async def check_cross_job_chronology_refusal(self, prior_alert=None):
        await self.seed_prior_failed_urgent('authentication')
        old_job = self.job_id
        if prior_alert is not None:
            self.assertTrue(await database.claim_owner_alert(old_job, self.path))
            if prior_alert != 'attempting':
                await database.finish_owner_alert(old_job, prior_alert == 'accepted', self.path)
        before_ledger = [dict(row) for row in await Database(self.path).fetch('SELECT * FROM owner_alert_attempts')]
        await Database(self.path).execute("UPDATE job_queue SET status='done' WHERE id=?", (old_job,))
        new_job = await Database(self.path).execute("""INSERT INTO job_queue
            (tenant_id,ticket_id,message_id,event_type,author_type,is_customer_message,status,payload,created_at)
            SELECT tenant_id,ticket_id,message_id,event_type,author_type,is_customer_message,'pending',payload,created_at
            FROM job_queue WHERE id=?""", (old_job,))
        self.job_id = int(new_job)
        self.job = dict((await Database(self.path).fetch('SELECT * FROM job_queue WHERE id=?', (self.job_id,)))[0])
        await self.unknown_source_time('What are the measurements?' if prior_alert is None
                                       else 'I am filing a chargeback for order #87654.')
        with patch.object(orchestrator, 'process_customer_message', AsyncMock()) as process, \
             patch.object(orchestrator, 'send_whatsapp', return_value=True) as notify:
            await orchestrator._process_one_job(self.job, True, self.settings)
            saved = await database.get_job_result(self.job_id, self.path)
            self.assertEqual(saved['job_id'], self.job_id)
            self.assertEqual(saved['priority'], 'high' if prior_alert is None else 'critical')
            self.assertEqual(saved['generation_state'], 'needs_review')
            self.assertEqual(bool(saved['notify_owner']), prior_alert is None)
            self.assertEqual(saved['draft_text'], '')
            self.assertEqual(saved['attempt_count'], 0)
            self.assertIsNone(saved['generation_attempt_id'])
            self.assertEqual(await database.pending_recovery_alerts(5, self.path), [self.job_id] if prior_alert is None else [])
            self.assertEqual([dict(row) for row in await Database(self.path).fetch('SELECT * FROM owner_alert_attempts')], before_ledger)
            settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
            await database.init_db(self.path)
            await orchestrator._recover_stale_jobs(settings)
            await orchestrator._recover_stale_jobs(settings)
            process.assert_not_called()
            self.assertEqual(notify.call_count, int(prior_alert is None))

    async def test_cross_job_unattempted_urgent_duty_transfers_to_review(self):
        await self.check_cross_job_chronology_refusal()

    async def test_cross_job_accepted_owner_alert_is_not_resent(self):
        await self.check_cross_job_chronology_refusal('accepted')

    async def test_cross_job_uncertain_owner_alert_is_not_resent(self):
        await self.check_cross_job_chronology_refusal('uncertain')

    async def test_cross_job_claimed_owner_alert_is_not_resent(self):
        await self.check_cross_job_chronology_refusal('attempting')

    async def test_result_written_but_response_lost_retry_skips_model_and_alerts_once(self):
        async def response_lost(_job):
            await self.save(attempt=_job['generation_attempt_id'])
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
        await database.fail_job(self.job_id, 'Synthetic response lost', self.path)
        await database.requeue_failed_job(self.job_id, self.path)
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
        self.assertEqual(await database.requeue_stale_jobs(10, self.path, max_retries=3), [self.job_id])
        row = await self.status()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["retry_count"], 3)
        self.assertIn("operator review required", row["error"])
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(await database.requeue_stale_jobs(10, self.path, max_retries=3), [])


    async def test_stale_sweep_alerts_when_last_attempt_of_high_ticket_crashed(self):
        from bb_webhook.draft_generation import begin_attempt
        await database.claim_job(self.job_id, self.path)
        db = Database(self.path)
        await db.execute("UPDATE job_queue SET generation_cycle_attempts=2 WHERE id=?", (self.job_id,))
        self.assertIsNotNone(await begin_attempt(self.job_id, self.path, priority_context=dict(
            priority='high', notify_owner=True, reason='Synthetic refund request')))
        # The processor dies mid-generation on the third attempt.
        await db.execute("UPDATE job_queue SET started_at='2000-01-01' WHERE id=?", (self.job_id,))
        settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
        with patch.object(orchestrator, "send_whatsapp", return_value=True) as notify:
            self.assertEqual(await orchestrator._recover_stale_jobs(settings), [self.job_id])
            notify.assert_called_once()
        saved = await database.get_job_result(self.job_id, self.path)
        self.assertEqual(saved["generation_state"], "failed")
        self.assertTrue(saved["notify_owner"])
        self.assertEqual((await self.status())["status"], "done")
        tickets = await database.get_dashboard_tickets(db_path=self.path)
        self.assertEqual(tickets[0]["owner_alert_status"], "accepted")


    async def crash_final_attempt(self, job_id, reason='Synthetic refund request'):
        from bb_webhook.draft_generation import begin_attempt
        db = Database(self.path)
        await database.claim_job(job_id, self.path)
        await db.execute("UPDATE job_queue SET generation_cycle_attempts=2 WHERE id=?", (job_id,))
        self.assertIsNotNone(await begin_attempt(job_id, self.path, priority_context=dict(
            priority='high', notify_owner=True, reason=reason)))
        await db.execute("UPDATE job_queue SET started_at='2000-01-01' WHERE id=?", (job_id,))

    async def test_alert_survives_crash_between_recovery_and_notification(self):
        await self.crash_final_attempt(self.job_id)
        # Recovery commits, then the processor dies before attempting the alert.
        self.assertEqual(await database.requeue_stale_jobs(10, self.path, max_retries=3), [self.job_id])
        self.assertEqual((await self.status())["status"], "done")
        settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
        with patch.object(orchestrator, "send_whatsapp", return_value=True) as notify:
            await orchestrator._recover_stale_jobs(settings)
            await orchestrator._recover_stale_jobs(settings)
            notify.assert_called_once()
        self.assertIn("Synthetic refund request", notify.call_args.kwargs["reason"])
        tickets = await database.get_dashboard_tickets(db_path=self.path)
        self.assertEqual(tickets[0]["owner_alert_status"], "accepted")

    async def test_owed_alert_survives_supersession_and_pending_row_is_cleared(self):
        await self.crash_final_attempt(self.job_id)
        self.assertEqual(await database.requeue_stale_jobs(10, self.path, max_retries=3), [self.job_id])
        # A newer customer message supersedes the job before the deferred alert is sent.
        await Database(self.path).execute(
            "UPDATE job_queue SET status='skipped', error='new_customer_message_refresh_ticket' WHERE id=?",
            (self.job_id,))
        settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
        with patch.object(orchestrator, "send_whatsapp", return_value=True) as notify:
            await orchestrator._recover_stale_jobs(settings)
            await orchestrator._recover_stale_jobs(settings)
            notify.assert_called_once()
        rows = await Database(self.path).fetch("SELECT COUNT(*) AS n FROM recovery_alerts_pending")
        self.assertEqual(rows[0]["n"], 0)

    async def test_owed_alert_dropped_when_staff_already_acted(self):
        await self.crash_final_attempt(self.job_id)
        await database.requeue_stale_jobs(10, self.path, max_retries=3)
        await Database(self.path).execute(
            "UPDATE job_queue SET status='skipped', error='human_action_already_initiated' WHERE id=?",
            (self.job_id,))
        settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
        with patch.object(orchestrator, "send_whatsapp", return_value=True) as notify:
            await orchestrator._recover_stale_jobs(settings)
            notify.assert_not_called()
        rows = await Database(self.path).fetch("SELECT COUNT(*) AS n FROM recovery_alerts_pending")
        self.assertEqual(rows[0]["n"], 0)

    async def test_owed_urgent_alert_survives_unavailable_message_chronology(self):
        await self.crash_final_attempt(self.job_id)
        await database.requeue_stale_jobs(10, self.path, max_retries=3)
        await Database(self.path).execute(
            "UPDATE job_queue SET status='skipped', error='message_chronology_unavailable' WHERE id=?",
            (self.job_id,))
        settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
        with patch.object(orchestrator, "send_whatsapp", return_value=True) as notify:
            await orchestrator._recover_stale_jobs(settings)
            await orchestrator._recover_stale_jobs(settings)
            notify.assert_called_once()
        rows = await Database(self.path).fetch("SELECT COUNT(*) AS n FROM recovery_alerts_pending")
        self.assertEqual(rows[0]["n"], 0)

    async def test_pending_recovery_alerts_are_bounded_per_sweep(self):
        jobs = [self.job_id]
        for n in range(6):
            jobs.append(await database.ingest_event(
                dict(tenant_id="test", ticket_id=200 + n, message_id=f"synthetic-{n}", event_type="created",
                     author_type="customer", is_customer_message=True, message_text="Synthetic question",
                     created_at="2026-09-25T00:00:00+00:00"),
                "{}", self.path))
        for job_id in jobs:
            await self.crash_final_attempt(job_id)
        settings = SimpleNamespace(db_path_absolute=self.path, stale_job_minutes=10, max_retries=3)
        with patch.object(orchestrator, "send_whatsapp", return_value=True) as notify:
            await orchestrator._recover_stale_jobs(settings)
            self.assertEqual(notify.call_count, orchestrator._RECOVERY_ALERTS_PER_SWEEP)
            await orchestrator._recover_stale_jobs(settings)
            self.assertEqual(notify.call_count, len(jobs))


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
                orchestrator._save_result_to_webhook(123, "synthetic", 1, dict(generation_attempt_id=1, generation_state='ready'))


if __name__ == "__main__":
    unittest.main()
