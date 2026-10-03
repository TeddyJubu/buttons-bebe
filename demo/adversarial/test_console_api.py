"""Adversarial tests for the real FastAPI console API.

The tests use the production ``bb_webhook.app`` routes through an ASGI
transport, but every test gets a throwaway SQLite database.  Gorgias and
Hermes are replaced only at the app's external transport boundaries:
``_GClient`` and ``create_subprocess_exec``.  No real network or model call is
permitted.

Some tests deliberately document currently observable weaknesses.  They
assert the behavior so a future hardening change turns into a visible test
failure rather than silently changing the threat model.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiosqlite
import httpx


ROOT = Path(__file__).resolve().parents[2]
WEBHOOK_SRC = ROOT / "webhook" / "src"
if str(WEBHOOK_SRC) not in sys.path:
    sys.path.insert(0, str(WEBHOOK_SRC))

# Set demo-only process-local configuration before importing the settings
# singleton.  These values never leave this test process.
os.environ["WEBHOOK_SECRET"] = "demo-console-adversarial-secret"
os.environ["GORGIAS_SUBDOMAIN"] = "demo-local-only"
os.environ["GORGIAS_API_EMAIL"] = ""
os.environ["GORGIAS_API_KEY"] = ""
os.environ["CONSOLE_USERNAME"] = "demo-owner"
os.environ["CONSOLE_SESSION_SECRET"] = "demo-console-session-secret"
os.environ["PROCESSOR_RESULT_SECRET"] = "demo-processor-result-secret-0123456789"
ORIGIN = "https://support.buttonsbebe.com"
sys.path.append(str(ROOT))

from demo.adversarial.offline_imports import without_root_dotenv  # noqa: E402

with without_root_dotenv():
    from bb_webhook import app as app_module, session_store  # noqa: E402
    from bb_webhook.config import get_settings  # noqa: E402
    from bb_webhook.console_auth import build_session_token, session_claims  # noqa: E402
    from bb_webhook.database import (  # noqa: E402
        claim_job,
        complete_job,
        fail_job,
        ingest_event,
        init_db,
        record_ticket_result,
    )


class RecordingGorgias:
    """Boundary fake for the two Gorgias write methods."""

    calls: list[tuple[str, int, str]] = []
    result: dict[str, Any] = {"ok": True}

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def send_public_reply(self, ticket_id: int, body_text: str, *, expected_recipient: str,
                                expected_source_message_id: str, on_created: Any) -> dict[str, Any]:
        self.calls.append(("send", ticket_id, body_text))
        if not self.result["ok"]:
            return dict(self.result)
        await on_created(7000 + len(self.calls))
        return {**self.result, "delivery_status": "sent"}

    async def post_internal_note(self, ticket_id: int, body_text: str, *, on_created: Any) -> dict[str, Any]:
        self.calls.append(("note", ticket_id, body_text))
        if not self.result["ok"]:
            return dict(self.result)
        await on_created(7000 + len(self.calls))
        return {**self.result, "message": {"id": 7000 + len(self.calls)}}


REAL_EXEC = app_module._asyncio.create_subprocess_exec
# A local stand-in for Hermes: answers only inside the run-token tags it was given.
FAKE_MODEL = ("import re,sys;t=re.search(r'<DRAFT:([a-f0-9]+)>',sys.argv[-1]).group(1);"
              "print(f'<DRAFT:{t}>{sys.argv[1]}</DRAFT:{t}>')")


def fake_model(calls: list, reply: str):
    async def fake_exec(*args: Any, **kwargs: Any):
        calls.append(args)
        return await REAL_EXEC(sys.executable, "-c", FAKE_MODEL, reply, args[-1], **kwargs)
    return fake_exec


class ConsoleApiAdversarialTests(unittest.IsolatedAsyncioTestCase):
    """Exercise console routes over HTTP against a temporary demo DB."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="cute-things-console-")
        self.db_path = Path(self._tmp.name) / "demo-console.db"
        os.environ["WEBHOOK_DB_PATH"] = str(self.db_path)
        get_settings.cache_clear()
        await init_db(self.db_path)
        await session_store.initialize(self.db_path)
        secret = os.environ["CONSOLE_SESSION_SECRET"]
        token = build_session_token(os.environ["CONSOLE_USERNAME"], secret)
        await session_store.register(session_claims(token, secret), self.db_path)

        transport = httpx.ASGITransport(app=app_module.app, raise_app_exceptions=False)
        # The owner reaches the console through the signed session cookie and
        # the public origin; the processor posts results over direct loopback.
        self.client = httpx.AsyncClient(
            transport=transport, base_url="http://demo.test",
            headers={"Origin": ORIGIN}, cookies={"bb_console_session": token},
        )
        self.processor = httpx.AsyncClient(
            transport=transport, base_url="http://demo.test",
            headers={"Authorization": "Bearer " + os.environ["PROCESSOR_RESULT_SECRET"]},
        )
        RecordingGorgias.calls = []
        RecordingGorgias.result = {"ok": True}
        self._lesson_patch = patch.object(app_module, "_record_lesson", lambda *a, **k: True)
        self._lesson_patch.start()

        await self._seed_demo_rows()

    async def asyncTearDown(self) -> None:
        self._lesson_patch.stop()
        await self.client.aclose()
        await self.processor.aclose()
        get_settings.cache_clear()
        self._tmp.cleanup()

    async def _seed_message(
        self,
        message_id: str,
        ticket_id: int,
        *,
        subject: str,
        text: str,
        priority: str = "normal",
        action: str = "drafted",
        status: str = "done",
        result: bool = True,
    ) -> None:
        event = {
            "tenant_id": "cute-things-demo",
            "ticket_id": ticket_id,
            "message_id": message_id,
            "event_type": "ticket.message.created",
            "author_type": "customer",
            "author_email": f"{message_id}@example.com",
            "channel": "email",
            "customer_email": f"{message_id}@example.com",
            "ticket_subject": subject,
            "message_text": text,
            "intents": [{"name": "order_status"}],
            "is_customer_message": True,
            "created_at": "2026-08-23T00:00:00+00:00",
        }
        job_id = await ingest_event(event, json.dumps(event), db_path=self.db_path)
        await claim_job(job_id, db_path=self.db_path)
        if status == "done":
            await complete_job(job_id, db_path=self.db_path)
        elif status == "failed":
            await fail_job(job_id, "synthetic processor failure", db_path=self.db_path)
        if not result:
            return
        await record_ticket_result(
            ticket_id=ticket_id,
            message_id=message_id,
            job_id=job_id,
            priority=priority,
            action=action,
            reason=f"reason for {message_id}",
            notify_owner=priority in {"high", "critical"},
            gorgias_priority_set=False,
            note_posted=False,
            draft_text=f"Draft for {message_id}",
            db_path=self.db_path,
        )

    async def _seed_demo_rows(self) -> None:
        await self._seed_message(
            "m-high",
            1001,
            subject="Where is my order?",
            text="Please find order #1001.",
            priority="high",
        )
        await self._seed_message(
            "m-failed",
            1002,
            subject="Refund request",
            text="I need a refund.",
            priority="critical",
            action="escalated",
            status="failed",
        )
        await self._seed_message(
            "m-normal",
            1003,
            subject="Sizing question",
            text="What size should I choose?",
        )
        await self._seed_message(
            "m-xss",
            1004,
            subject='<script>alert("subject")</script>',
            text='<img src=x onerror="alert(1)"> </script>',
            priority="high",
        )

    async def _get(self, path: str) -> httpx.Response:
        return await self.client.get(path)

    async def _post(self, path: str, payload: Any = None, *, content: str | None = None,
                    client: httpx.AsyncClient | None = None) -> httpx.Response:
        client = client or self.client
        if content is not None:
            return await client.post(path, content=content, headers={"content-type": "application/json"})
        return await client.post(path, json=payload)

    def _action(self, message_id: str, text: str, **extra: Any) -> dict[str, Any]:
        """A reviewed human action bound to the seeded source message and draft."""
        revision = hashlib.sha256(f"Draft for {message_id}".encode()).hexdigest()
        return {"operation_id": str(uuid.uuid4()), "source_message_id": message_id, "text": text,
                "draft_revision": revision, "confirmed": True, **extra}

    async def _post_result(self, payload: Any = None, *, content: str | None = None) -> httpx.Response:
        return await self._post("/dashboard/api/results", payload, content=content, client=self.processor)

    async def test_real_list_stats_tickets_and_notifications_endpoints(self) -> None:
        messages = await self._get("/dashboard/api/messages?limit=20&customer_only=true")
        stats = await self._get("/dashboard/api/stats")
        tickets = await self._get("/dashboard/api/tickets?limit=20")
        notifications = await self._get("/dashboard/api/notifications")

        self.assertEqual(messages.status_code, 200)
        self.assertEqual(len(messages.json()), 4)
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["total"], 4)
        self.assertEqual(tickets.status_code, 200)
        ticket_rows = tickets.json()
        self.assertEqual({row["ticket_id"] for row in ticket_rows}, {1001, 1002, 1003, 1004})
        self.assertEqual(notifications.status_code, 200)
        notification_body = notifications.json()
        self.assertEqual(notification_body["unread_count"], 3)
        self.assertEqual(
            {item["kind"] for item in notification_body["notifications"]},
            {"review", "failed"},
        )

    async def test_notification_read_state_is_persistent_and_ticket_scoped(self) -> None:
        initial = (await self._get("/dashboard/api/notifications")).json()
        ids = {item["ticket_id"]: item["id"] for item in initial["notifications"]}
        read_one = await self._post("/dashboard/api/notifications/read", {"ids": [ids[1001]]})
        self.assertEqual(read_one.status_code, 200)
        self.assertEqual(read_one.json()["unread_count"], 2)

        after = (await self._get("/dashboard/api/notifications")).json()
        by_ticket = {item["ticket_id"]: item for item in after["notifications"]}
        self.assertTrue(by_ticket[1001]["read"])
        self.assertFalse(by_ticket[1002]["read"])
        self.assertFalse(by_ticket[1004]["read"])

        invalid = await self._post("/dashboard/api/notifications/read", {})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"], "ids_or_all_required")

        all_read = await self._post("/dashboard/api/notifications/read", {"all": True})
        self.assertEqual(all_read.status_code, 200)
        self.assertEqual(all_read.json()["unread_count"], 0)

    async def test_invalid_result_writes_fail_closed_for_missing_fields_and_json(self) -> None:
        missing = await self._post_result({"ticket_id": 999, "message_id": "bad"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"], "missing_fields")

        malformed = await self._post_result(content="{not-json")
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(malformed.json()["error"], "invalid_json")

    async def test_result_endpoint_rejects_wrong_identity_types(self) -> None:
        response = await self._post_result(
            {
                "ticket_id": "not-an-integer",
                "message_id": {"not": "a string"},
                "priority": "normal",
                "action": "drafted",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_ticket_id")

    async def test_result_endpoint_rejects_list_values_without_server_error(self) -> None:
        response = await self._post_result(
            {
                "ticket_id": 2001,
                "message_id": "m-invalid-list",
                "priority": ["high"],
                "action": "drafted",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_priority")

    async def test_result_endpoint_persists_processor_no_draft_outcome(self) -> None:
        await self._seed_message("m-thanks", 1005, subject="Thanks", text="Thank you!", status="processing",
                                 result=False)
        response = await self._post_result(
            {
                "ticket_id": 1005,
                "message_id": "m-thanks",
                "priority": "low",
                "action": "no_draft_needed",
                "reason": "acknowledgement-only message",
                "draft_text": None,
            },
        )
        self.assertEqual(response.status_code, 200)

        tickets = (await self._get("/dashboard/api/tickets?limit=20")).json()
        persisted = next(row for row in tickets if row["ticket_id"] == 1005)
        self.assertEqual(persisted["action"], "no_draft_needed")
        self.assertIsNone(persisted["draft_text"])

    async def test_send_and_note_require_server_confirmation_and_note_remains_internal(self) -> None:
        with patch.object(app_module, "_GClient", RecordingGorgias):
            unconfirmed_send = await self._post(
                "/dashboard/api/ticket/1001/send",
                self._action("m-high", "Send without confirmation token", confirmed=False),
            )
            unconfirmed_note = await self._post(
                "/dashboard/api/ticket/1002/note",
                self._action("m-failed", "Note without confirmation token", confirmed=False),
            )
            note = await self._post(
                "/dashboard/api/ticket/1002/note", self._action("m-failed", "Internal note")
            )
            confirmed_send = await self._post(
                "/dashboard/api/ticket/1001/send", self._action("m-high", "Send after confirmation")
            )

        for refused in (unconfirmed_send, unconfirmed_note):
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.json()["error"], "confirmation_required")
        self.assertEqual((note.status_code, note.json()["delivery_status"]), (200, "recorded"))
        self.assertEqual((confirmed_send.status_code, confirmed_send.json()["delivery_status"]), (200, "sent"))
        self.assertEqual(
            RecordingGorgias.calls,
            [
                ("note", 1002, "Internal note"),
                ("send", 1001, "Send after confirmation"),
            ],
        )

        empty_send = await self._post("/dashboard/api/ticket/1001/send", self._action("m-high", "  "))
        empty_note = await self._post("/dashboard/api/ticket/1002/note", self._action("m-failed", ""))
        self.assertEqual(empty_send.status_code, 400)
        self.assertEqual(empty_note.status_code, 400)

    async def test_send_and_note_transport_failures_are_not_reported_as_success(self) -> None:
        RecordingGorgias.result = {"ok": False, "delivery_status": "not_attempted", "error": "ticket not found"}
        with patch.object(app_module, "_GClient", RecordingGorgias):
            send = await self._post("/dashboard/api/ticket/1001/send", self._action("m-high", "hello"))
            note = await self._post("/dashboard/api/ticket/1002/note", self._action("m-failed", "hello"))

        self.assertEqual(send.status_code, 409)
        self.assertEqual(
            {key: send.json()[key] for key in ("ok", "delivery_status", "error")},
            {"ok": False, "delivery_status": "not_attempted", "error": "ticket not found"},
        )
        self.assertEqual(note.status_code, 202)
        self.assertEqual(
            {key: note.json()[key] for key in ("ok", "delivery_status", "error")},
            {"ok": False, "delivery_status": "unknown", "error": "delivery_unconfirmed"},
        )

    async def test_send_and_note_list_bodies_fail_closed_without_500(self) -> None:
        with patch.object(app_module, "_GClient", RecordingGorgias):
            send = await self._post("/dashboard/api/ticket/1001/send", ["hello"])
            note = await self._post("/dashboard/api/ticket/1002/note", ["hello"])
        self.assertEqual(send.status_code, 400)
        self.assertEqual(note.status_code, 400)

    async def test_rewrite_uses_patched_model_boundary_and_validates_instruction(self) -> None:
        calls: list[tuple[Any, ...]] = []
        missing_instruction = await self._post(
            "/dashboard/api/ticket/1001/rewrite",
            {"draft": "draft", "message_text": "customer"},
        )
        self.assertEqual(missing_instruction.status_code, 400)
        self.assertEqual(missing_instruction.json()["error"], "no instruction")

        with (
            patch.object(app_module._asyncio, "create_subprocess_exec", fake_model(calls, "A safe rewritten reply.")),
            patch.object(app_module, "_HERMES_IGNORE_RULES", True),
        ):
            rewritten = await self._post(
                "/dashboard/api/ticket/1001/rewrite",
                {
                    "draft": "Draft",
                    "instruction": "Make it warmer",
                    "message_text": "Where is order #1001?",
                    "source_message_id": "m-high",
                },
            )
        self.assertEqual(rewritten.status_code, 200)
        self.assertEqual(rewritten.json(), {"ok": True, "draft": "A safe rewritten reply."})
        self.assertEqual(len(calls), 1)
        self.assertIn("OWNER REWRITE INSTRUCTION:\nMake it warmer", calls[0][-1])
        self.assertEqual(
            calls[0][calls[0].index("-t") + 1],
            "buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias",
        )
        self.assertIn("--ignore-rules", calls[0])

    async def test_rewrite_rejects_a_ticket_missing_from_the_console(self) -> None:
        calls: list[tuple[Any, ...]] = []
        with patch.object(app_module._asyncio, "create_subprocess_exec", fake_model(calls, "missing-ticket draft")):
            response = await self._post(
                "/dashboard/api/ticket/999999/rewrite",
                {"draft": "old", "instruction": "rewrite it", "source_message_id": "m-high"},
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "ticket_not_in_console")
        self.assertEqual(calls, [])

    async def test_html_and_script_payloads_remain_json_data_at_api_boundary(self) -> None:
        response = await self._get("/dashboard/api/tickets?limit=20")
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        xss_row = next(row for row in response.json() if row["ticket_id"] == 1004)
        self.assertEqual(xss_row["ticket_subject"], '<script>alert("subject")</script>')
        self.assertIn("onerror", xss_row["message_text"])
        self.assertNotIn("<script>", response.headers.get("content-type", ""))

        RecordingGorgias.calls = []
        with patch.object(app_module, "_GClient", RecordingGorgias):
            sent = await self._post(
                "/dashboard/api/ticket/1004/send", self._action("m-xss", '<script>alert("send")</script>')
            )
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(RecordingGorgias.calls[0][2], '<script>alert("send")</script>')

    async def test_oversized_limits_are_capped_and_negative_limits_are_clamped(self) -> None:
        # Add 505 parsed rows in one transaction so the endpoint's 500-row cap
        # can be tested without making any external request.
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executemany(
                """INSERT INTO parsed_messages
                (message_id, ticket_id, event_type, author_type, author_email,
                 channel, customer_email, ticket_subject, message_text, intents,
                 is_customer_message, created_at, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                [
                    (
                        f"page-{i}",
                        3000 + i,
                        "ticket.message.created",
                        "customer",
                        f"page-{i}@example.com",
                        "email",
                        f"page-{i}@example.com",
                        f"Page {i}",
                        "page",
                        "[]",
                        "2026-08-23T00:00:00+00:00",
                        f"2026-08-23T00:00:{i % 60:02d}+00:00",
                    )
                    for i in range(505)
                ],
            )
            await conn.commit()

        capped = await self._get("/dashboard/api/tickets?limit=999999")
        self.assertEqual(capped.status_code, 200)
        self.assertEqual(len(capped.json()), 500)

        negative = await self._get("/dashboard/api/tickets?limit=-1")
        self.assertEqual(negative.status_code, 200)
        self.assertEqual(len(negative.json()), 1)

    async def test_missing_ticket_path_is_422_before_any_external_transport(self) -> None:
        RecordingGorgias.calls = []
        with patch.object(app_module, "_GClient", RecordingGorgias):
            response = await self._post("/dashboard/api/ticket/not-an-int/send", self._action("m-high", "hello"))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(RecordingGorgias.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
