"""Missed Gorgias messages enter Hermes once, without provider writes."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from bb_webhook.database import init_db
from bb_webhook.db import Database
from gorgias_reconcile import reconcile_page, reconcile_loop, SweepPosition
import gorgias_reconcile


WHEN = "2026-09-25T14:39:03+00:00"


def ticket(ticket_id=284477559, received=WHEN):
    return {"id": ticket_id, "status": "open", "spam": False, "trashed_datetime": None,
            "subject": "Order question", "updated_datetime": received,
            "last_received_message_datetime": received, "customer": {"email": "customer@example.test"}}


def message(message_id=730082445, *, agent=False, at=WHEN, body="Please help with my order."):
    return {"id": message_id, "ticket_id": 284477559, "created_datetime": at, "from_agent": agent,
            "public": True, "channel": "email", "stripped_text": body,
            "preferred_content": body, "preferred_content_field": "stripped_text",
            "sender": {"email": "customer@example.test"}}


class FakeMCP:
    def __init__(self, tickets, messages, *, pages=None):
        self.pages = pages if pages is not None else {None:(tickets, 'older'), 'older':([], None)}
        self.messages = messages
        self.detail_calls = []
        self.list_calls = []
        self.ticket_calls = []
        self.tickets = {row['id']:row for page in self.pages.values() if isinstance(page, tuple) for row in page[0]}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def call(self, tool, arguments):
        if tool == "list_inbox_tickets":
            cursor=arguments.get('cursor')
            self.list_calls.append(cursor)
            page=self.pages[cursor]
            if isinstance(page, BaseException):
                raise page
            data, next_cursor=page
            self.tickets.update({row['id']:row for row in data})
            return {"data":data, "meta":{"next_cursor":next_cursor}}
        if tool == "get_ticket":
            ticket_id = arguments["ticket_id"]
            self.ticket_calls.append(ticket_id)
            return dict(self.tickets.get(ticket_id, {}))
        assert tool == "get_ticket_messages"
        ticket_id = arguments["ticket_id"]
        self.detail_calls.append(ticket_id)
        data=self.messages.get(ticket_id, [])
        if isinstance(data, BaseException):
            raise data
        return {"data":data}


class ReconcileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "webhook.db"
        await init_db(self.db_path)
        self.position=SweepPosition()
        self.now = datetime(2026, 9, 25, 14, 40, tzinfo=timezone.utc).timestamp()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def scan(self, client, **kwargs):
        return await reconcile_page(self.db_path, "buttonsbebe", self.position, client_factory=lambda: client,
                                    now=self.now, **kwargs)

    def rows(self, sql, params=()):
        with sqlite3.connect(self.db_path) as db:
            return db.execute(sql, params).fetchall()

    async def test_unanswered_customer_message_is_queued_once_for_hermes(self):
        client = FakeMCP([ticket()], {284477559: [message()]})
        cursor, count = await self.scan(client)
        self.assertEqual((cursor, count), ("older", 1))
        self.assertEqual(self.rows("SELECT message_id,status,is_customer_message FROM job_queue"),
                         [("730082445", "pending", 1)])
        source = json.loads(self.rows("SELECT raw_payload FROM webhook_events")[0][0])
        self.assertEqual(source["source"], "gorgias_reconciliation")
        self.assertEqual(self.rows("SELECT message_text FROM parsed_messages"),
                         [("Please help with my order.",)])
        self.assertEqual(await self.scan(client), (None, 0))
        self.assertEqual(client.detail_calls, [284477559])

    async def test_agent_replied_ticket_is_not_drafted_or_refetched(self):
        client = FakeMCP([ticket()], {284477559: [message(730082446, agent=True), message()]})
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(await self.scan(client), (None, 0))
        self.assertEqual(client.detail_calls, [284477559])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM job_queue"), [(0,)])

    async def test_existing_webhook_message_is_not_queued_again(self):
        await Database(self.db_path).execute(
            """INSERT INTO parsed_messages
               (message_id,ticket_id,event_type,author_type,is_customer_message,created_at,received_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("730082445", 284477559, "ticket-message-created", "customer", 1, WHEN, WHEN))
        client = FakeMCP([ticket()], {284477559: [message()]})
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(client.detail_calls, [])

    async def test_legacy_offset_and_microseconds_prevent_duplicate_recovery(self):
        for local, summary in (
            ('2026-09-25T16:39:03+02:00', WHEN),
            ('2026-09-25T14:39:03.000200Z', '2026-09-25T14:39:03.000100Z'),
        ):
            await Database(self.db_path).execute('DELETE FROM parsed_messages')
            await Database(self.db_path).execute("""INSERT INTO parsed_messages
                (message_id,ticket_id,event_type,author_type,is_customer_message,created_at,received_at)
                VALUES ('legacy',284477559,'message','customer',1,?,?)""", (local, WHEN))
            self.position=SweepPosition()
            client=FakeMCP([ticket(received=summary)], {284477559:[message()]})
            self.assertEqual(await self.scan(client), ('older', 0))
            self.assertEqual(client.detail_calls, [])

    async def test_earlier_offset_and_invalid_local_time_do_not_hide_recoverable_message(self):
        for local in ('2026-09-25T16:00:00+02:00', 'invalid'):
            await Database(self.db_path).execute('DELETE FROM parsed_messages')
            await Database(self.db_path).execute('DELETE FROM webhook_events')
            await Database(self.db_path).execute('DELETE FROM job_queue')
            await Database(self.db_path).execute('DELETE FROM gorgias_reconcile_seen')
            await Database(self.db_path).execute("""INSERT INTO parsed_messages
                (message_id,ticket_id,event_type,author_type,is_customer_message,created_at,received_at)
                VALUES ('legacy',284477559,'message','customer',1,?,?)""", (local, WHEN))
            self.position=SweepPosition()
            client=FakeMCP([ticket()], {284477559:[message()]})
            self.assertEqual(await self.scan(client), ('older', 1))
            self.assertEqual(client.detail_calls, [284477559])
            self.assertEqual(self.rows("SELECT created_at FROM parsed_messages WHERE message_id='legacy'"), [(local,)])
            self.assertEqual(self.rows('SELECT COUNT(*) FROM job_queue'), [(1,)])

    async def test_recovery_normalizes_utc_and_rejects_timezone_free_provider_time(self):
        client=FakeMCP([ticket(received='2026-09-25T16:39:03+02:00')],
            {284477559:[message(at='2026-09-25T16:39:03+02:00')]})
        self.assertEqual(await self.scan(client), ('older', 1))
        self.assertEqual(self.rows('SELECT created_at FROM parsed_messages'), [(WHEN,)])
        self.position=SweepPosition()
        invalid=FakeMCP([ticket(999, received='2026-09-25T14:39:03')], {})
        self.assertEqual(await self.scan(invalid), ('older', 0))
        self.assertEqual(invalid.detail_calls, [])

    async def test_summary_ahead_of_messages_retries(self):
        later = "2026-09-25T14:40:00+00:00"
        client = FakeMCP([ticket(received=later)], {284477559: [message()]})
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(await self.scan(client), (None, 0))
        self.assertEqual(client.detail_calls, [284477559])
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(client.detail_calls, [284477559, 284477559])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM gorgias_reconcile_seen"), [(0,)])

    async def test_cross_ticket_message_is_rejected(self):
        wrong = {**message(), "ticket_id": 999}
        client = FakeMCP([ticket()], {284477559: [wrong]})
        self.assertEqual(await self.scan(client), ("older", 0))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM job_queue"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM gorgias_reconcile_seen"), [(0,)])

    async def test_active_job_cap_defers_other_tickets(self):
        second = ticket(284938147)
        client = FakeMCP([ticket(), second],
                         {284477559: [message()], 284938147: [{**message(730071617), "ticket_id": 284938147}]})
        self.assertEqual(await self.scan(client, max_active_jobs=1), (None, 1))
        self.assertEqual(client.detail_calls, [284477559])
        self.assertEqual(await self.scan(client, max_active_jobs=1), (None, 0))
        await Database(self.db_path).execute("UPDATE job_queue SET status='done'")
        self.assertEqual(await self.scan(client, max_active_jobs=1), ("older", 1))
        self.assertEqual(client.detail_calls, [284477559, 284938147])

    async def test_sixth_candidate_gets_turn_after_five_failed_details(self):
        clients=FakeMCP([ticket(n) for n in range(1, 7)],
            {n:RuntimeError('Synthetic read failure') for n in range(1, 6)} | {6:[{**message(600), 'ticket_id':6}]})
        self.assertEqual(await self.scan(clients), (None, 0))
        self.assertEqual(clients.detail_calls, [1,2,3,4,5])
        self.assertEqual([t['id'] for t in self.position.batch.remaining], [6])
        self.assertEqual(await self.scan(clients), ('older', 1))
        self.assertEqual(clients.list_calls, [None])
        self.assertEqual(clients.detail_calls, [1,2,3,4,5,6])
        self.assertEqual(self.rows('SELECT ticket_id,message_id FROM job_queue'), [(6,'600')])
        self.assertEqual(await self.scan(clients), (None, 0))
        self.assertEqual(await self.scan(clients), (None, 0))
        self.assertEqual(await self.scan(clients), ('older', 0))
        self.assertEqual(clients.detail_calls, [1,2,3,4,5,6,1,2,3,4,5])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM job_queue'), [(1,)])

    async def test_capacity_preserves_unattempted_captured_candidate(self):
        client=FakeMCP([ticket(1),ticket(2)],
            {n:[{**message(n*100), 'ticket_id':n}] for n in (1,2)})
        self.assertEqual(await self.scan(client, max_active_jobs=1), (None, 1))
        self.assertEqual([t['id'] for t in self.position.batch.remaining], [2])
        self.assertEqual(await self.scan(client, max_active_jobs=1), (None, 0))
        self.assertEqual(client.list_calls, [None])
        self.assertEqual(client.detail_calls, [1])
        self.assertEqual([t['id'] for t in self.position.batch.remaining], [2])
        await Database(self.db_path).execute("UPDATE job_queue SET status='done'")
        self.assertEqual(await self.scan(client, max_active_jobs=1), ('older', 1))
        self.assertEqual(client.list_calls, [None])
        self.assertEqual(client.detail_calls, [1,2])

    async def test_deferred_ineligible_ticket_is_skipped_while_next_eligible_progresses(self):
        for index, change in enumerate(({'status':'closed'}, {'spam':True}, {'trashed_datetime':WHEN})):
            with self.subTest(change=change):
                await Database(self.db_path).execute("UPDATE job_queue SET status='done'")
                self.position = SweepPosition()
                ids = [index * 10 + n for n in (1,2,3)]
                live = [ticket(n) for n in ids]
                client = FakeMCP(live, {n:[{**message(n*100), 'ticket_id':n}] for n in ids})
                self.assertEqual((await self.scan(client, max_active_jobs=1))[1], 1)
                live[1].update(change)
                await Database(self.db_path).execute("UPDATE job_queue SET status='done'")
                self.assertEqual((await self.scan(client, max_active_jobs=1))[1], 1)
                self.assertEqual(client.detail_calls, [ids[0],ids[2]])
                self.assertEqual(client.ticket_calls, [ids[1],ids[2]])
                self.assertEqual(self.rows('SELECT COUNT(*) FROM job_queue WHERE ticket_id=?', (ids[1],)), [(0,)])

    async def test_deferred_state_reads_remain_bounded_and_batch_keeps_progressing(self):
        live = [ticket(n) for n in range(1,9)]
        client = FakeMCP(live, {n:[{**message(n*100), 'ticket_id':n}] for n in range(1,9)})
        self.assertEqual((await self.scan(client, max_details=1, max_active_jobs=1))[1], 1)
        for row in live[1:6]:
            row['status'] = 'closed'
        await Database(self.db_path).execute("UPDATE job_queue SET status='done'")
        self.assertEqual((await self.scan(client, max_active_jobs=1))[1], 0)
        self.assertEqual(client.ticket_calls, [2,3,4,5,6])
        self.assertEqual([row['id'] for row in self.position.batch.remaining], [7,8])
        self.assertEqual((await self.scan(client, max_active_jobs=1))[1], 1)
        self.assertEqual(client.detail_calls, [1,7])

    async def test_deferred_ticket_tool_is_read_only_and_other_tools_remain_refused(self):
        client = gorgias_reconcile.ReadOnlyMCP.__new__(gorgias_reconcile.ReadOnlyMCP)
        client.rpc = AsyncMock(return_value={'structuredContent':ticket(2)})
        self.assertEqual((await client.call('get_ticket', {'ticket_id':2}))['id'], 2)
        client.rpc.assert_awaited_once_with('tools/call', {'name':'get_ticket', 'arguments':{'ticket_id':2}})
        with self.assertRaisesRegex(ValueError, 'Non-read'):
            await client.call('send_reply', {'ticket_id':2})
        self.assertEqual(client.rpc.await_count, 1)

    async def test_changing_page_does_not_expand_or_replace_captured_batch(self):
        client=FakeMCP([ticket(n) for n in range(1, 103)], {})
        self.assertEqual(await self.scan(client, max_details=1000), (None, 0))
        self.assertEqual(len(self.position.batch.remaining), 95)
        original=self.position.batch.remaining[0]
        client.pages[None]=([ticket(n, received='2026-09-25T14:40:00Z') for n in range(201, 301)], 'older')
        for _ in range(19):
            await self.scan(client)
        self.assertIsNone(self.position.batch)
        self.assertEqual(client.list_calls, [None])
        self.assertEqual(client.detail_calls, list(range(1,101)))
        self.assertEqual(original['updated_datetime'], WHEN)
        await self.scan(client)
        await self.scan(client)
        self.assertEqual(client.list_calls, [None, 'older', None])
        self.assertEqual(client.detail_calls[-5:], [201,202,203,204,205])
        self.assertEqual(len(self.position.batch.remaining), 95)

    async def run_loop_turns(self, client, turns, on_sleep=None):
        sleeps=0
        async def stop(_seconds):
            nonlocal sleeps
            sleeps+=1
            if on_sleep:
                on_sleep(sleeps)
            if sleeps >= turns:
                raise asyncio.CancelledError()
        with patch.object(gorgias_reconcile.asyncio, 'sleep', side_effect=stop), patch.object(gorgias_reconcile.time, 'time', return_value=self.now):
            with self.assertRaises(asyncio.CancelledError):
                await reconcile_loop(self.db_path, 'buttonsbebe', client_factory=lambda:client)

    async def test_loop_reaches_page_six_terminal_and_next_sweep_after_head_refresh(self):
        client=FakeMCP([], {}, pages={None:([ticket(1)],'p2')} | {
            f'p{n}':([ticket(n)], f'p{n+1}' if n<6 else None) for n in range(2,7)})
        await self.run_loop_turns(client, 9)
        self.assertEqual(client.list_calls, [None,'p2','p3','p4','p5',None,'p6',None,'p2'])
        self.assertEqual(client.detail_calls, [1,2,3,4,5,1,6,1,2])

    async def test_head_refresh_has_five_turns_and_preserves_captured_older_batch(self):
        client=FakeMCP([], {}, pages={None:([ticket(n) for n in range(1,31)], 'older'), 'older':([], None)})
        def replace_head(sleeps):
            if sleeps==5:
                client.pages[None]=([ticket(n) for n in range(999,1009)], 'older')
        await self.run_loop_turns(client, 8, replace_head)
        self.assertEqual(client.list_calls, [None,None,'older'])
        self.assertEqual(client.detail_calls, list(range(1,26))+list(range(999,1004))+list(range(26,31)))

    async def test_failed_head_refresh_preserves_older_batch(self):
        client=FakeMCP([], {}, pages={None:([ticket(n) for n in range(1,31)], 'older'), 'older':([], None)})
        def fail_head(sleeps):
            if sleeps==5:
                client.pages[None]=ValueError('Synthetic head read failure')
        await self.run_loop_turns(client, 8, fail_head)
        self.assertEqual(client.list_calls, [None,None,'older'])
        self.assertEqual(client.detail_calls, list(range(1,31)))

    async def test_invalid_traversal_cursor_resets_to_head(self):
        client=FakeMCP([], {}, pages={None:([], 'expired'), 'expired':ValueError('Invalid synthetic cursor')})
        await self.run_loop_turns(client, 3)
        self.assertEqual(client.list_calls, [None,'expired',None])
        self.assertEqual(client.detail_calls, [])

    async def test_cancelled_detail_propagates_without_seen_or_queue_write(self):
        client=FakeMCP([ticket()], {284477559:asyncio.CancelledError()})
        with self.assertRaises(asyncio.CancelledError):
            await self.scan(client)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM job_queue'), [(0,)])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM gorgias_reconcile_seen'), [(0,)])

    async def test_restart_after_intake_before_seen_keeps_exactly_one_job(self):
        client=FakeMCP([ticket()], {284477559:[message()]})
        with patch.object(gorgias_reconcile, '_mark_seen', side_effect=RuntimeError('Synthetic process exit')):
            with self.assertRaises(RuntimeError):
                await self.scan(client)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM job_queue'), [(1,)])
        self.position=SweepPosition()
        self.assertEqual(await self.scan(client), ('older', 0))
        self.assertEqual(client.detail_calls, [284477559])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM job_queue'), [(1,)])

    async def test_new_ticket_version_is_eligible_in_later_batch(self):
        client=FakeMCP([ticket()], {284477559:[message(730082446, agent=True)]})
        self.assertEqual(await self.scan(client), ('older', 0))
        client.pages[None]=([ticket(received='2026-09-25T14:40:00Z')], 'older')
        client.messages[284477559]=[message(730082447, at='2026-09-25T14:40:00Z')]
        self.assertEqual(await self.scan(client), (None, 0))
        self.assertEqual(await self.scan(client), ('older', 1))
        self.assertEqual(client.detail_calls, [284477559,284477559])
        self.assertEqual(self.rows('SELECT message_id FROM job_queue'), [('730082447',)])

    async def test_zero_capacity_does_not_fetch_or_capture_page(self):
        client=FakeMCP([ticket()], {284477559:[message()]})
        self.assertEqual(await self.scan(client, max_active_jobs=0), (None, 0))
        self.assertIsNone(self.position.batch)
        self.assertEqual(client.list_calls, [])
        self.assertEqual(client.detail_calls, [])

    async def test_current_text_is_queued_and_raw_evidence_stays_intact(self):
        full = ("Please help with my order.\nSent from my iPhone\n"
                "-------- Original message --------\nFrom: Buttons Bebe <hello@bb.com>")
        stripped = "Please help with my order.\nSent from my iPhone"
        raw_message = message(body=stripped)
        raw_message["body_text"] = full
        raw_message["stripped_text"] = stripped
        raw_message["preferred_content"] = stripped
        raw_message["preferred_content_field"] = "stripped_text"
        client = FakeMCP([ticket()], {284477559: [raw_message]})
        cursor, count = await self.scan(client)
        self.assertEqual((cursor, count), ("older", 1))
        self.assertEqual(self.rows("SELECT message_id,message_text FROM parsed_messages"),
                         [("730082445", "Please help with my order.")])
        source = json.loads(self.rows("SELECT raw_payload FROM webhook_events")[0][0])
        stored = source["message"]
        self.assertEqual(stored["id"], 730082445)
        self.assertEqual(stored["preferred_content"], stripped)
        self.assertEqual(stored["preferred_content_field"], "stripped_text")
        self.assertEqual(stored["stripped_text"], stripped)
        self.assertEqual(stored["body_text"], full)
        self.assertEqual(stored["current_text"], "Please help with my order.")
        self.assertIn("Original message", stored["display_text"])
        self.assertEqual(stored["original_content"], full)
        self.assertEqual(stored["original_field"], "body_text")
        self.assertTrue(stored["history_available"])
        self.assertFalse(stored["source_truncated"])
        self.assertEqual(stored["cleanup_version"], "intake-1")
        self.assertEqual(stored["display_source"], "body_text")
        self.assertEqual(stored["current_source"], "stripped_text")

    async def test_curated_contract_is_stored_without_inventing_a_body(self):
        raw_message = message(body="Please help with my order.")
        raw_message["preferred_content"] = "NOT A RAW BODY"
        raw_message["preferred_content_field"] = "body_text"
        raw_message.update({
            "display_text": "SERVICE DISPLAY\nEarlier quoted history",
            "current_text": "SERVICE CURRENT",
            "display_source": "body_text",
            "current_source": "stripped_text",
            "original_content": "RAW CHOSEN BODY",
            "original_field": "body_text",
            "history_available": True,
            "source_truncated": False,
            "cleanup_version": "intake-1",
        })
        client = FakeMCP([ticket()], {284477559: [raw_message]})
        self.assertEqual((await self.scan(client))[1], 1)
        stored = json.loads(self.rows("SELECT raw_payload FROM webhook_events")[0][0])["message"]
        self.assertEqual(self.rows("SELECT message_text FROM parsed_messages"), [("SERVICE CURRENT",)])
        self.assertEqual(stored["display_text"], "SERVICE DISPLAY\nEarlier quoted history")
        self.assertEqual(stored["current_text"], "SERVICE CURRENT")
        self.assertEqual(stored["original_content"], "RAW CHOSEN BODY")
        self.assertTrue(stored["history_available"])
        self.assertNotIn("body_text", stored)
        self.assertEqual(stored["preferred_content"], "NOT A RAW BODY")
        self.assertEqual(raw_message["preferred_content"], "NOT A RAW BODY")

    async def test_invalid_contract_uses_retained_sources_only(self):
        raw_message = message(body="Please help with my order.")
        raw_message["preferred_content"] = "NOT A RAW BODY"
        raw_message["preferred_content_field"] = "body_text"
        raw_message["history_available"] = True
        raw_message["cleanup_version"] = "intake-1"
        raw_message["display_text"] = "FAKE HISTORY"
        client = FakeMCP([ticket()], {284477559: [raw_message]})
        self.assertEqual((await self.scan(client))[1], 1)
        stored = json.loads(self.rows("SELECT raw_payload FROM webhook_events")[0][0])["message"]
        self.assertEqual(stored["current_text"], "Please help with my order.")
        self.assertEqual(stored["display_text"], "Please help with my order.")
        self.assertFalse(stored["history_available"])
        self.assertNotIn("body_text", stored)
        self.assertNotIn("FAKE", stored["display_text"])
        self.assertNotIn("NOT A RAW BODY", stored["original_content"])

    async def test_large_metadata_cannot_displace_current_message(self):
        item = ticket()
        item['customer'] = {'name': '你' * 40_000, 'email': 'x' * 40_000,
                            'id': 'y' * 40_000}
        raw_message = message(body='Please help with my order.')
        client = FakeMCP([item], {284477559: [raw_message]})
        self.assertEqual((await self.scan(client))[1], 1)
        raw = self.rows('SELECT raw_payload FROM webhook_events')[0][0]
        self.assertLessEqual(len(raw.encode('utf-8')), 65_536)
        value = json.loads(raw)
        self.assertEqual(value['message']['current_text'], 'Please help with my order.')
        self.assertEqual(value['ticket']['id'], 284477559)
        self.assertTrue(value['message']['source_truncated'])
        self.assertEqual(len(value['ticket']['customer']['name']), 200)
        self.assertEqual(item['customer']['name'], '你' * 40_000)

    async def test_unicode_sources_stay_inside_the_raw_byte_cap(self):
        marker = "Please help with my order.\nEarlier note about the hat.\n"
        filler = "你" * ((100_000 // len("你".encode("utf-8"))) + 8)
        self.assertGreaterEqual(len(filler.encode("utf-8")), 100_000)
        body = marker + filler
        originals = {
            "body_text": body,
            "body_html": "<p>" + body + "</p>",
            "stripped_html": "<div>" + filler + "</div>",
            "preferred_content": filler + "preferred",
        }
        raw_message = message(body="Please help with my order.\nSent from my iPhone")
        raw_message.update(originals)
        raw_message["preferred_content_field"] = "stripped_text"
        client = FakeMCP([ticket()], {284477559: [raw_message]})
        self.assertEqual((await self.scan(client))[1], 1)
        self.assertEqual(raw_message["preferred_content"], originals["preferred_content"])
        self.assertEqual(raw_message["body_text"], originals["body_text"])
        raw_payload = self.rows("SELECT raw_payload FROM webhook_events")[0][0]
        self.assertLessEqual(len(raw_payload.encode("utf-8")), 65_536)
        self.assertTrue(raw_payload.isascii())
        source = json.loads(raw_payload)
        stored = source["message"]
        self.assertEqual(stored["id"], 730082445)
        self.assertEqual(stored["created_datetime"], WHEN)
        self.assertEqual(source["ticket"]["id"], 284477559)
        self.assertEqual(self.rows("SELECT message_id, created_at FROM parsed_messages"),
                         [("730082445", WHEN)])
        self.assertIn("Please help with my order.", stored["current_text"])
        self.assertIn("Earlier note about the hat.", stored["display_text"])
        self.assertTrue(stored["history_available"])
        self.assertTrue(stored["source_truncated"])
        self.assertTrue(stored["original_content"].startswith("Please help with my order.\nEarlier note"))
        for key, value in originals.items():
            self.assertTrue(stored[key])
            self.assertTrue(value.startswith(stored[key]))
            self.assertLess(len(stored[key]), len(value))
        from intake.message_content import intake_from_message
        read = intake_from_message(stored)
        self.assertIn("Please help with my order.", read["current_text"])
        self.assertIn("Earlier note about the hat.", read["display_text"])
        self.assertTrue(read["history_available"])


if __name__ == "__main__":
    unittest.main()
