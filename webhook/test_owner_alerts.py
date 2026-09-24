"""Read-only owner-alert inspection against synthetic local SQLite data."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from bb_webhook import app as app_module, database, db as db_module, session_store
from bb_webhook.console_auth import build_session_token, session_claims
from bb_webhook.db import Database


ATTEMPT_FIELDS = {
    "job_id", "ticket_id", "message_id", "status", "attempted_at",
    "finished_at", "ticket_subject", "reason",
}
ORIGIN = "https://support.buttonsbebe.com"
PATH = "/dashboard/api/owner-alerts"


class OwnerAlertFixture:
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "alerts.sqlite3"
        await database.init_db(self.path)

    async def seed_attempt(self, message_id, *, ticket_id=123, status="uncertain",
                           attempted_at="2020-01-01T00:00:00+00:00"):
        job_id = await database.ingest_event(dict(
            tenant_id="test", ticket_id=ticket_id, message_id=message_id,
            event_type="ticket.message.created", author_type="customer",
            is_customer_message=True, ticket_subject="Synthetic alert subject",
            customer_email="private-customer@example.invalid",
            message_text="private-message-content", intents=[],
        ), '{"credential":"private-raw-payload"}', self.path)
        await database.record_ticket_result(
            ticket_id, message_id, job_id, "high", "sensitive_draft",
            "Synthetic escalation reason", True, False, False,
            draft_text="private-draft-content", db_path=self.path,
        )
        await Database(self.path).execute(
            "INSERT INTO owner_alert_attempts VALUES (?, ?, ?, ?)",
            (job_id, status, attempted_at,
             None if status == "attempting" else "2020-01-01T00:00:01+00:00"),
        )
        return job_id


class OwnerAlertDatabaseTests(OwnerAlertFixture, unittest.IsolatedAsyncioTestCase):
    async def test_all_time_nonaccepted_attempts_match_stats_not_distinct_tickets(self):
        first = await self.seed_attempt("first", status="attempting")
        second = await self.seed_attempt("second")
        await self.seed_attempt("accepted", status="accepted")

        result = await database.get_owner_alerts(db_path=self.path)
        stats = await database.get_result_stats(self.path)

        self.assertEqual(set(result), {"total", "limit", "offset", "attempts"})
        self.assertEqual((result["total"], result["limit"], result["offset"]), (2, 50, 0))
        self.assertEqual(result["total"], stats["owner_alerts_need_attention"])
        self.assertEqual([row["job_id"] for row in result["attempts"]], [second, first])
        self.assertEqual([row["ticket_id"] for row in result["attempts"]], [123, 123])
        self.assertEqual([row["status"] for row in result["attempts"]], ["uncertain", "attempting"])
        for row in result["attempts"]:
            self.assertEqual(set(row), ATTEMPT_FIELDS)
            self.assertEqual(row["ticket_subject"], "Synthetic alert subject")
            self.assertEqual(row["reason"], "Synthetic escalation reason")
        self.assertIsNone(result["attempts"][1]["finished_at"])

    async def test_pagination_orders_by_newest_attempt_then_job_id(self):
        newest = await self.seed_attempt("newest", attempted_at="2026-09-24T00:00:00+00:00")
        older = await self.seed_attempt("older")
        tied = await self.seed_attempt("tied")
        pages = [await database.get_owner_alerts(limit=1, offset=i, db_path=self.path)
                 for i in range(4)]
        self.assertEqual([page["total"] for page in pages], [3, 3, 3, 3])
        self.assertEqual([page["offset"] for page in pages], [0, 1, 2, 3])
        self.assertEqual([page["attempts"][0]["job_id"] for page in pages[:3]],
                         [newest, tied, older])
        self.assertEqual(pages[3]["attempts"], [])

    async def test_missing_context_does_not_remove_ledger_attempts(self):
        missing_subject = await self.seed_attempt("missing-subject")
        missing_result = await self.seed_attempt("missing-result")
        orphan = await self.seed_attempt("missing-job")
        await Database(self.path).execute("DELETE FROM parsed_messages WHERE message_id = ?",
                                          ("missing-subject",))
        await Database(self.path).execute("DELETE FROM ticket_results WHERE job_id = ?",
                                          (missing_result,))
        await Database(self.path).execute("DELETE FROM job_queue WHERE id = ?", (orphan,))
        result = await database.get_owner_alerts(db_path=self.path)
        rows = {row["job_id"]: row for row in result["attempts"]}
        self.assertEqual(result["total"], 3)
        self.assertEqual(set(rows), {missing_subject, missing_result, orphan})
        self.assertIsNone(rows[missing_subject]["ticket_subject"])
        self.assertEqual(rows[missing_subject]["reason"], "Synthetic escalation reason")
        self.assertIsNone(rows[missing_result]["reason"])
        self.assertEqual(rows[missing_result]["ticket_subject"], "Synthetic alert subject")
        for field in ("ticket_id", "message_id", "ticket_subject", "reason"):
            self.assertIsNone(rows[orphan][field])
        self.assertEqual(rows[orphan]["status"], "uncertain")

    async def test_joins_require_full_identity_and_never_duplicate_attempts(self):
        job = await self.seed_attempt("one")
        # job_id alone is not unique in results; message_id alone can belong to
        # another ticket. Neither unrelated result may multiply or replace a row.
        for ticket, message in ((999, "one"), (123, "unrelated")):
            await database.record_ticket_result(ticket, message, job, "high", "sensitive_draft",
                                                "Wrong result", True, False, False,
                                                db_path=self.path)
        await Database(self.path).execute(
            "UPDATE parsed_messages SET ticket_id = 999 WHERE message_id = 'one'")
        result = await database.get_owner_alerts(db_path=self.path)
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["attempts"][0]["reason"], "Synthetic escalation reason")
        self.assertIsNone(result["attempts"][0]["ticket_subject"])

    async def test_empty_ledger_returns_empty_envelope(self):
        self.assertEqual(await database.get_owner_alerts(db_path=self.path),
                         {"total": 0, "limit": 50, "offset": 0, "attempts": []})


class OwnerAlertApiTests(OwnerAlertFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await session_store.initialize(self.path)
        self.settings = SimpleNamespace(
            console_username="test-owner", console_session_secret="synthetic-session-secret",
            db_path_absolute=self.path,
        )
        for target in (app_module, db_module):
            patched = patch.object(target, "get_settings", return_value=self.settings)
            patched.start()
            self.addCleanup(patched.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.create_app()), base_url=ORIGIN)
        self.addAsyncCleanup(self.client.aclose)
        self.token = build_session_token(self.settings.console_username,
                                         self.settings.console_session_secret)
        self.claims = session_claims(self.token, self.settings.console_session_secret)
        await session_store.register(self.claims, self.path)
        self.client.cookies.set("bb_console_session", self.token)

    async def test_authenticated_get_returns_minimal_contract_without_mutation(self):
        await self.seed_attempt("inspect", status="attempting")
        with sqlite3.connect(self.path) as conn:
            before = list(conn.iterdump())
        for _ in range(2):
            response = await self.client.get(PATH)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["vary"], "Cookie")
            payload = response.json()
            self.assertEqual(set(payload), {"total", "limit", "offset", "attempts"})
            self.assertEqual(set(payload["attempts"][0]), ATTEMPT_FIELDS)
            self.assertEqual(payload["attempts"][0]["status"], "attempting")
            self.assertNotIn("private-", response.text)
            self.assertNotIn(self.settings.console_session_secret, response.text)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(list(conn.iterdump()), before)

    async def test_pagination_defaults_bounds_and_offset(self):
        with sqlite3.connect(self.path) as conn:
            conn.executemany("INSERT INTO owner_alert_attempts VALUES (?, 'uncertain', ?, NULL)",
                             [(i, "2020-01-01T00:00:00+00:00") for i in range(1, 106)])
        for query, limit, offset, length in (
            ("", 50, 0, 50),
            ("?limit=1000&offset=-2", 100, 0, 100),
            ("?limit=0&offset=1", 1, 1, 1),
            ("?limit=-5", 1, 0, 1),
            ("?limit=2&offset=103", 2, 103, 2),
            ("?offset=105", 50, 105, 0),
        ):
            with self.subTest(query=query):
                response = await self.client.get(PATH + query)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual((result["total"], result["limit"], result["offset"]),
                                 (105, limit, offset))
                self.assertEqual(len(result["attempts"]), length)
                if length:
                    self.assertEqual(result["attempts"][0]["job_id"], 105 - offset)
        for query in ("?limit=invalid", "?offset=1.5"):
            self.assertEqual((await self.client.get(PATH + query)).status_code, 422)

    async def test_missing_invalid_unregistered_and_revoked_sessions_are_denied(self):
        unregistered = build_session_token(self.settings.console_username,
                                           self.settings.console_session_secret)
        for token in ("", "invalid-token", unregistered):
            with self.subTest(token_kind="missing" if not token else "invalid"):
                response = await self.client.get(PATH, headers={
                    "cookie": f"bb_console_session={token}",
                    "x-authenticated-actor": "owner:test-owner",
                    "x-forwarded-for": "127.0.0.1",
                })
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "not_authenticated"})
        await session_store.revoke(self.claims, self.path)
        self.assertEqual((await self.client.get(PATH)).status_code, 401)

    async def test_inspection_route_has_no_mutating_methods(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                response = await self.client.request(method, PATH, headers={"origin": ORIGIN})
                self.assertEqual(response.status_code, 405)


if __name__ == "__main__":
    unittest.main()
