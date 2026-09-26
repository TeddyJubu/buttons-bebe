"""Session/CSRF guarded retry queues work without send access or provider I/O."""
import unittest
import uuid
import httpx
from unittest.mock import patch
from bb_webhook import database, draft_generation, app as app_module
from bb_webhook.db import Database
from webhook.action_test_support import setup_action_case


class RetryApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await setup_action_case(self)
        await Database(self.path).execute("DELETE FROM ticket_results")
        self.job = await database.enqueue_job('test',1,'source-1','message','customer',True,{},self.path)
        await database.claim_job(self.job, self.path)
        attempt = await draft_generation.begin_attempt(self.job, self.path)
        await draft_generation.finish_attempt(dict(generation_attempt_id=attempt,
            job_id=self.job, ticket_id=1, message_id='source-1',priority='normal',
            action='drafted',reason='Synthetic failure',notify_owner=False,draft_text='',
            generation_state='failed',generation_error='authentication'),self.path)
        await database.complete_job(self.job,db_path=self.path,require_result=True)
        self.body=dict(operation_id=str(uuid.uuid4()),source_message_id='source-1',
                       draft_revision=draft_generation.revision(''))
        self.url='/dashboard/api/ticket/1/retry-draft'

    async def test_session_retry_without_send_grant_and_duplicate(self):
        with patch('bb_webhook.gorgias_client.GorgiasClient', side_effect=AssertionError('No provider calls')):
            first=await self.client.post(self.url,json=self.body)
            second=await self.client.post(self.url,json=self.body)
        self.assertEqual(first.status_code,202,first.text)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(first.json()['job_id'],self.job)

    async def test_rejects_stale_revision_and_new_customer_message(self):
        response=await self.client.post(self.url,json={**self.body,'draft_revision':'0'*64})
        self.assertEqual(response.status_code,409)
        await Database(self.path).execute("UPDATE parsed_messages SET message_id='new-source' WHERE ticket_id=1")
        response=await self.client.post(self.url,json=self.body)
        self.assertEqual(response.status_code,409)

    async def test_unauthenticated_and_cross_origin_cannot_enqueue(self):
        response=await self.client.post(self.url,json=self.body,headers={'Origin':'https://evil.example'})
        self.assertEqual(response.status_code,403)
        self.client.cookies.clear()
        response=await self.client.post(self.url,json=self.body)
        self.assertEqual(response.status_code,401)
        self.assertEqual((await database.get_job_stats(self.path))['done'],1)

    async def test_invalid_input_and_successful_candidate_are_rejected(self):
        response=await self.client.post(self.url,json={**self.body,'operation_id':'invalid'})
        self.assertEqual(response.status_code,400)
        await Database(self.path).execute("UPDATE ticket_results SET generation_state='ready',draft_text='Grounded answer'")
        response=await self.client.post(self.url,json=self.body)
        self.assertEqual(response.status_code,409)

    async def test_new_metadata_requires_processor_auth_and_is_projected(self):
        self.settings.processor_result_secret='synthetic-result-secret-0123456789'
        await self.client.post(self.url,json=self.body)
        await database.claim_job(self.job,self.path)
        attempt=await draft_generation.begin_attempt(self.job,self.path)
        payload=dict(generation_attempt_id=attempt,job_id=self.job,ticket_id=1,
            message_id='source-1',priority='normal',action='drafted',draft_text='The sleeve length is not confirmed.',
            generation_state='needs_review',review_required=True,missing_facts=['Sleeve length'],
            staff_next_step='Measure the named dress sleeve before completing this reply.')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app),base_url='http://127.0.0.1') as producer:
            response=await producer.post('/dashboard/api/results',json=payload)
            self.assertEqual(response.status_code,401)
            headers={'Authorization':'Bearer '+self.settings.processor_result_secret}
            malformed={**payload,'missing_facts':['x'*201]}
            self.assertEqual((await producer.post('/dashboard/api/results',headers=headers,json=malformed)).status_code,400)
            without_attempt={key:value for key,value in payload.items() if key!='generation_attempt_id'}
            self.assertEqual((await producer.post('/dashboard/api/results',headers=headers,json=without_attempt)).status_code,400)
            self.assertEqual((await producer.post('/dashboard/api/results',headers=headers,json=payload)).status_code,200)
        projected=(await database.get_dashboard_tickets(db_path=self.path))[0]
        self.assertEqual(projected['generation_state'],'needs_review')
        self.assertEqual(projected['missing_facts'],['Sleeve length'])
        self.assertEqual(projected['staff_next_step'],payload['staff_next_step'])
