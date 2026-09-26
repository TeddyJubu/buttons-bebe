"""Synthetic database races and durable retries; no provider/model calls."""
import asyncio
import json
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path

from bb_webhook import database as db
from bb_webhook.db import Database
from bb_webhook import draft_generation as generation
from bb_webhook.send_intents import ActionConflict, IntentStore


class DraftGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'test.db'
        await db.init_db(self.path)
        await self.ingest('m1', '2026-09-26T01:00:00+00:00')
        self.job = await db.get_next_pending_job(self.path)

    async def ingest(self, mid, at):
        return await db.ingest_event(dict(tenant_id='test',ticket_id=123,message_id=mid,
            event_type='message',author_type='customer',is_customer_message=True,
            message_text='Can I have the return label?',ticket_subject='Return',
            customer_email='customer@example.invalid',channel='email',created_at=at), '{}', self.path)

    async def start(self):
        self.assertTrue(await db.claim_job(self.job['id'],self.path))
        return await generation.begin_attempt(self.job['id'],self.path)

    def result(self, attempt, state='failed', error='timeout', text=''):
        return dict(generation_attempt_id=attempt,job_id=self.job['id'],ticket_id=123,
            message_id='m1',priority='normal',action='drafted',reason='Synthetic result',
            draft_text=text,notify_owner=False,generation_state=state,generation_error=error,
            review_required=False,missing_facts=[],staff_next_step='')

    async def job_row(self):
        return dict((await Database(self.path).fetch('SELECT * FROM job_queue WHERE id=?',(self.job['id'],)))[0])

    async def make_due(self):
        await Database(self.path).execute("UPDATE job_queue SET next_attempt_at='2000-01-01' WHERE id=?",(self.job['id'],))

    async def test_two_durable_delayed_retries_then_exhaustion(self):
        for number, delay in ((1,30),(2,120),(3,None)):
            attempt = await self.start()
            state = await generation.finish_attempt(self.result(attempt),self.path)
            row = await self.job_row()
            saved = await db.get_job_result(self.job['id'],self.path)
            self.assertEqual(saved['draft_text'],'')
            self.assertEqual(saved['attempt_count'],number)
            if delay:
                self.assertEqual(state,'retry_wait')
                difference=(datetime.fromisoformat(saved['next_retry_at'])-datetime.fromisoformat(saved['processed_at'])).total_seconds()
                self.assertEqual(difference,delay)
                self.assertIsNone(await db.get_next_pending_job(self.path))
                self.assertFalse(await db.claim_job(self.job['id'],self.path))
                await db.init_db(self.path)  # Restart does not reset the schedule.
                self.assertEqual((await self.job_row())['next_attempt_at'],row['next_attempt_at'])
                await self.make_due()
            else:
                self.assertEqual(state,'failed')
                self.assertTrue(await db.complete_job(self.job['id'],db_path=self.path,require_result=True))
        attempts=await Database(self.path).fetch('SELECT * FROM draft_generation_attempts')
        self.assertEqual(len(attempts),3)
        self.assertTrue(all(r['finished_at'] and r['duration_ms'] >= 0 for r in attempts))

    async def test_nontransient_failure_is_not_retried(self):
        attempt=await self.start()
        self.assertEqual(await generation.finish_attempt(self.result(attempt,error='authentication'),self.path),'failed')
        self.assertIsNone((await self.job_row())['next_attempt_at'])

    async def test_restart_preserves_business_urgency_in_abandoned_attempt(self):
        await db.claim_job(self.job['id'], self.path)
        await generation.begin_attempt(self.job['id'], self.path, priority_context=dict(
            priority='critical', notify_owner=True, reason='Chargeback dispute'))
        await db.init_db(self.path)
        await generation.recover_attempt(self.job['id'], self.path)
        saved = await db.get_job_result(self.job['id'], self.path)
        self.assertEqual(saved['priority'], 'critical')
        self.assertTrue(saved['notify_owner'])
        self.assertEqual(saved['draft_text'], '')

    async def test_retry_success_is_immutable_and_http_replay_is_idempotent(self):
        first=await self.start()
        await generation.finish_attempt(self.result(first),self.path)
        await self.make_due()
        second=await self.start()
        payload=self.result(second,'ready',None,'Could you share the order number?')
        await generation.finish_attempt(payload,self.path)
        await generation.finish_attempt({**payload,'draft_text':'A replay cannot replace this'},self.path)
        self.assertEqual((await db.get_job_result(self.job['id'],self.path))['draft_text'],payload['draft_text'])
        self.assertIsNone(await generation.begin_attempt(self.job['id'],self.path))

    async def test_new_message_before_generation_cancels_work(self):
        await self.ingest('m2','2026-09-26T02:00:00+00:00')
        self.assertIsNone(await self.start())
        self.assertEqual((await self.job_row())['status'],'skipped')

    async def test_new_message_during_generation_prevents_publication(self):
        attempt=await self.start()
        await self.ingest('m2','2026-09-26T02:00:00+00:00')
        self.assertEqual(await generation.finish_attempt(self.result(attempt,'ready',None,'Old reply'),self.path),'superseded')
        self.assertIsNone(await db.get_job_result(self.job['id'],self.path))

    async def reserve_human(self):
        return await IntentStore(self.path).reserve(operation_id=str(uuid.uuid4()),actor_id='operator',kind='send',
            ticket_id=123,source_message_id='m1',text='A manually reviewed reply',draft_revision=generation.revision(''))

    async def test_human_reservation_during_generation_wins(self):
        attempt=await self.start()
        await self.reserve_human()
        self.assertEqual(await generation.finish_attempt(self.result(attempt,'ready',None,'AI reply'),self.path),'superseded')
        self.assertIsNone(await db.get_job_result(self.job['id'],self.path))

    async def test_publication_changes_revision_and_rejects_old_human_review(self):
        attempt=await self.start()
        await generation.finish_attempt(self.result(attempt,'ready',None,'New suggestion'),self.path)
        with self.assertRaises(ActionConflict):
            await self.reserve_human()

    async def test_manual_retry_deduplicates_and_checks_revision_and_human_actions(self):
        attempt=await self.start()
        await generation.finish_attempt(self.result(attempt,error='authentication'),self.path)
        await db.complete_job(self.job['id'],db_path=self.path,require_result=True)
        body=dict(operation_id=str(uuid.uuid4()),source_message_id='m1',draft_revision=generation.revision(''))
        with self.assertRaises(ActionConflict):
            await generation.retry_draft(123,{**body,'draft_revision':'0'*64},'operator',self.path)
        results=await asyncio.gather(*(generation.retry_draft(123,body,'operator',self.path) for _ in range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual((await self.job_row())['generation_cycle_attempts'],0)
        with self.assertRaises(ActionConflict):
            await generation.retry_draft(123,{**body,'operation_id':str(uuid.uuid4())},'operator',self.path)
        await self.reserve_human()
        self.assertIsNone(await self.start())

    async def test_legacy_failure_only_is_hidden_and_not_automatically_requeued(self):
        await db.record_ticket_result(123,'m1',self.job['id'],'high','sensitive_draft',
            'Hermes invocation failed — defaulting to high for safety',True,False,False,'Old fallback',self.path)
        await db.claim_job(self.job['id'],self.path)
        await db.complete_job(self.job['id'],db_path=self.path,require_result=True)
        rows=await db.get_dashboard_tickets(db_path=self.path)
        self.assertEqual(rows[0]['draft_text'],'')
        self.assertEqual(rows[0]['generation_state'],'failed')
        self.assertEqual(rows[0]['draft_revision'],generation.revision('Old fallback'))
        self.assertIsNone(await db.get_next_pending_job(self.path))
        self.assertEqual(generation.result_state({'draft_text':'Old fallback','reason':'Verified answer','action':'drafted'}),'ready')

    async def test_other_ticket_is_eligible_while_retry_waits(self):
        attempt=await self.start()
        await generation.finish_attempt(self.result(attempt),self.path)
        second=await db.ingest_event(dict(tenant_id='test',ticket_id=456,message_id='other',event_type='message',
            author_type='customer',is_customer_message=True,message_text='Another request'), '{}',self.path)
        self.assertEqual((await db.get_next_pending_job(self.path))['id'],second)

    async def test_restart_recovery_counts_interrupted_attempts_and_stops_at_three(self):
        for number in range(1,4):
            attempt=await self.start()
            self.assertIsNotNone(attempt)
            self.assertIsNone(await generation.begin_attempt(self.job['id'],self.path))
            await Database(self.path).execute("UPDATE job_queue SET started_at='2000-01-01' WHERE id=?",(self.job['id'],))
            await db.init_db(self.path)
            self.assertEqual(await db.requeue_stale_jobs(10,self.path),1)
            saved=await db.get_job_result(self.job['id'],self.path)
            self.assertEqual(saved['attempt_count'],number)
            self.assertEqual(saved['generation_error'],'process_exit')
            if number<3:
                self.assertEqual(saved['generation_state'],'retry_wait')
                self.assertIsNone(await db.get_next_pending_job(self.path))
                await self.make_due()
            else:
                self.assertEqual(saved['generation_state'],'failed')
                self.assertEqual((await self.job_row())['status'],'done')
        self.assertEqual(await db.requeue_stale_jobs(0,self.path),0)

    async def test_transport_retry_cannot_regenerate_terminal_failure(self):
        attempt=await self.start()
        await generation.finish_attempt(self.result(attempt,error='safety_rejected'),self.path)
        await db.fail_job(self.job['id'],'HTTP acknowledgement lost',self.path)
        await db.requeue_failed_job(self.job['id'],self.path)
        self.assertIsNone(await self.start())
        self.assertEqual((await self.job_row())['generation_cycle_attempts'],1)
        self.assertEqual((await self.job_row())['status'],'done')


if __name__=='__main__':
    unittest.main()
