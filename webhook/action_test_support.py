"""Synthetic authenticated action fixture; no external network or live DB."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from bb_webhook import app as app_module, database, session_store
from bb_webhook.console_auth import build_session_token, session_claims
from bb_webhook.db import Database
from bb_webhook.draft_generation import begin_attempt, finish_attempt


async def setup_action_case(case):
    case.tmp = tempfile.TemporaryDirectory()
    case.addCleanup(case.tmp.cleanup)
    case.path = Path(case.tmp.name) / 'actions.sqlite3'
    await database.init_db(case.path)
    await session_store.initialize(case.path)
    case.settings = SimpleNamespace(db_path_absolute=case.path, console_session_secret='test-action-secret',
                                    console_username='owner', demo_mode=False)
    for target in ('bb_webhook.deps.get_settings', 'bb_webhook.db.get_settings',
                   'bb_webhook.middleware.console_session.get_settings'):
        patched = patch(target, return_value=case.settings)
        patched.start()
        case.addCleanup(patched.stop)
    case.job_id = await database.ingest_event(dict(tenant_id='test', ticket_id=1,
        message_id='source-1', event_type='ticket.message.created', author_type='customer',
        customer_email='customer@example.com', message_text='Where is my parcel?',
        is_customer_message=True, created_at='2026-09-26T01:00:00+00:00'), '{}', case.path)
    await Database(case.path).execute("UPDATE parsed_messages SET received_at='2026-09-26T01:00:01+00:00' WHERE message_id='source-1'")
    await database.claim_job(case.job_id, case.path)
    attempt = await begin_attempt(case.job_id, case.path)
    await finish_attempt(dict(ticket_id=1, message_id='source-1', job_id=case.job_id,
        generation_attempt_id=attempt, generation_state='ready', priority='normal',
        action='drafted', draft_text='I can help check that.'), case.path)
    await database.complete_job(case.job_id, db_path=case.path, require_result=True)
    token = build_session_token('owner', 'test-action-secret')
    await session_store.register(session_claims(token, 'test-action-secret'), case.path)
    case.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app),
        base_url='https://support.buttonsbebe.com', headers={'Origin': 'https://support.buttonsbebe.com'},
        cookies={'bb_console_session': token})
    case.addAsyncCleanup(case.client.aclose)


async def enable_action_send_access(case):
    """Install a real synthetic Inbox grant for tests of Gorgias write routes."""
    response = await case.client.post('/dashboard/api/inbox/send-access', json={'enabled': True})
    if response.status_code != 200:
        raise AssertionError(f'could not enable synthetic Inbox send access: {response.text}')
    case.client.headers['X-Inbox-Send-Access'] = response.json()['token']
    return response.json()['token']
