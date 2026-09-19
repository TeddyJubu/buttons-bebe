"""Slice A–C: bridge status, Gorgias inbound, human-only send_reply."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge import config as bridge_config
from bridge.gorgias_inbound import accept, parse_event, verify_secret
from bridge.router import reply_route, route_label
from helpdesk.dispatch import HUMAN_ONLY_TOOLS, dispatch, invoke
from helpdesk.http import handle_http
from helpdesk.names import TOOL_BRIDGE_STATUS, TOOL_NAMES, TOOL_SEND_REPLY
from helpdesk.tickets import reset as reset_tickets


class BridgeStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tickets()
        for key in (
            "GORGIAS_BRIDGE_ENABLED",
            "HELPDESK_OUTBOUND_ENABLED",
            "GORGIAS_SUBDOMAIN",
            "GORGIAS_API_EMAIL",
            "GORGIAS_API_KEY",
            "GORGIAS_BRIDGE_SECRET",
            "AGENTMAIL_API_KEY",
            "HELPDESK_SEND_ALLOWLIST",
        ):
            os.environ.pop(key, None)

    def test_bridge_status_defaults_off(self) -> None:
        payload = dispatch(TOOL_BRIDGE_STATUS, {})
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["gorgiasEnabled"])
        self.assertFalse(payload["outboundEnabled"])
        self.assertFalse(payload["gorgiasConfigured"])
        self.assertNotIn("GORGIAS_API_KEY", json.dumps(payload))

    def test_ingest_gorgias_source_round_trips(self) -> None:
        payload = dispatch(
            "helpdesk.ingest_email",
            {
                "from": "Ada <ada.bridge@example.com>",
                "subject": "Where is #1001?",
                "body": "Tracking on order #1001 please",
                "receivedAt": "2026-09-06T12:00:00Z",
                "messageId": "gorgias-msg-1",
                "source": "gorgias",
                "_fromBridge": True,
                "external": {
                    "system": "gorgias",
                    "ticketId": "4242",
                    "messageId": "gorgias-msg-1",
                    "customerEmail": "ada.bridge@example.com",
                },
            },
        )
        self.assertTrue(payload["ok"])
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": payload["ticketId"]})["ticket"]
        self.assertEqual(ticket["source"], "gorgias")
        self.assertEqual(ticket["external"]["ticketId"], "4242")
        self.assertEqual(ticket["fromEmail"], "ada.bridge@example.com")

    def test_source_gorgias_rejected_without_bridge_flag(self) -> None:
        payload = invoke(
            "helpdesk.ingest_email",
            {
                "from": "Ada <ada.bridge@example.com>",
                "subject": "Forge",
                "body": "Tracking on order #1001 please",
                "receivedAt": "2026-09-06T12:00:00Z",
                "messageId": "forged-1",
                "source": "gorgias",
                "external": {"system": "gorgias", "ticketId": "999", "messageId": "forged-1"},
            },
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "bad_request")
        self.assertIn("bridge-only", payload["message"])

    def test_intake_store_survives_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "intake_tickets.json"
            os.environ["HELPDESK_STORE_FILE"] = str(store)
            try:
                reset_tickets()
                created = dispatch(
                    "helpdesk.ingest_email",
                    {
                        "from": "Sam <sam@example.com>",
                        "subject": "Broken rattle",
                        "body": "The rattle is broken",
                        "receivedAt": "2026-09-06T12:00:00Z",
                        "messageId": "persist-1",
                        "source": "agentmail",
                    },
                )
                self.assertTrue(created["ok"])
                ticket_id = created["ticketId"]
                reset_tickets()
                ticket = dispatch("helpdesk.get_ticket", {"ticketId": ticket_id})["ticket"]
                self.assertEqual(ticket["id"], ticket_id)
                self.assertEqual(ticket["source"], "agentmail")
            finally:
                os.environ.pop("HELPDESK_STORE_FILE", None)
                reset_tickets()


class GorgiasInboundTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tickets()
        os.environ["GORGIAS_BRIDGE_SECRET"] = "bridge-test-secret"
        os.environ["GORGIAS_BRIDGE_ENABLED"] = "1"

    def tearDown(self) -> None:
        for key in ("GORGIAS_BRIDGE_SECRET", "GORGIAS_BRIDGE_ENABLED"):
            os.environ.pop(key, None)
        reset_tickets()

    def test_verify_secret_bearer_and_header(self) -> None:
        self.assertTrue(verify_secret({"Authorization": "Bearer bridge-test-secret"}))
        self.assertTrue(verify_secret({"X-Bridge-Secret": "bridge-test-secret"}))
        self.assertFalse(verify_secret({"Authorization": "Bearer wrong"}))
        self.assertFalse(verify_secret({}))
        # Query secrets are rejected (log-leak risk).
        self.assertFalse(verify_secret({}, "bridge-test-secret"))

    def test_accept_customer_message(self) -> None:
        result = accept(
            {
                "trigger": "ticket-message-created",
                "ticket": {
                    "id": 77,
                    "subject": "Where is #1001?",
                    "channel": "email",
                    "customer": {"email": "ada.bridge@example.com", "name": "Ada"},
                },
                "message": {
                    "id": 8801,
                    "from_agent": "False",
                    "body_text": "Tracking on order #1001 please",
                    "created_datetime": "2026-09-06T12:00:00Z",
                },
            },
            invoke=invoke,
        )
        self.assertEqual(result["status"], "accepted")
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": result["ticketId"]})["ticket"]
        self.assertEqual(ticket["source"], "gorgias")
        self.assertEqual(ticket["external"]["ticketId"], "77")

    def test_accept_ignores_agent_and_duplicates(self) -> None:
        payload = {
            "ticket": {"id": 78, "subject": "Hi", "customer": {"email": "a@example.com"}},
            "message": {
                "id": 8802,
                "from_agent": True,
                "body_text": "Agent reply",
                "created_datetime": "2026-09-06T12:00:00Z",
            },
        }
        ignored = accept(payload, invoke=invoke)
        self.assertEqual(ignored["status"], "ignored_agent_message")
        customer = {
            "ticket": {"id": 79, "subject": "Hi", "customer": {"email": "b@example.com", "name": "Bee"}},
            "message": {
                "id": 8803,
                "from_agent": False,
                "body_text": "Hello",
                "created_datetime": "2026-09-06T12:00:00Z",
            },
        }
        first = accept(customer, invoke=invoke)
        second = accept(customer, invoke=invoke)
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "duplicate")

    def test_accept_spam_returns_the_filed_ticket_id(self) -> None:
        """#33: spam files a reviewable ticket; the bridge must surface it."""
        result = accept(
            {
                "trigger": "ticket-message-created",
                "ticket": {
                    "id": 80,
                    "subject": "You won a $10,000 prize!",
                    "customer": {"email": "prize-farm@example.com", "name": "Prize Desk"},
                },
                "message": {
                    "id": 8804,
                    "from_agent": "False",
                    "body_text": "Claim your lottery winnings today.",
                    "created_datetime": "2026-09-06T12:00:00Z",
                },
            },
            invoke=invoke,
        )
        self.assertEqual(result["status"], "spam")
        self.assertIsNotNone(result["ticketId"], "bridge callers must be able to find the Spam ticket")
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": result["ticketId"]})["ticket"]
        self.assertTrue(ticket["spam"])
        spam_rows = dispatch("helpdesk.list_tickets", {"view": "spam", "limit": 100})["tickets"]
        self.assertTrue(any(row["id"] == result["ticketId"] for row in spam_rows))

    def test_bridge_disabled_returns_503(self) -> None:
        os.environ["GORGIAS_BRIDGE_ENABLED"] = "0"
        result = accept({"ticket": {"id": 1}, "message": {"id": 2}}, invoke=invoke)
        self.assertEqual(result["http"], 503)
        self.assertEqual(result["status"], "bridge_disabled")

    def test_parse_true_false_strings(self) -> None:
        event = parse_event(
            {
                "ticket": {"id": 1, "subject": "S"},
                "message": {"id": 2, "from_agent": "True", "body_text": "x"},
            }
        )
        self.assertTrue(event["from_agent"])


class SendReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tickets()
        os.environ["HELPDESK_OUTBOUND_ENABLED"] = "1"
        os.environ["HELPDESK_SEND_ALLOWLIST"] = "ada.bridge@example.com"
        os.environ.pop("GORGIAS_BRIDGE_ENABLED", None)

    def tearDown(self) -> None:
        for key in (
            "HELPDESK_OUTBOUND_ENABLED",
            "HELPDESK_SEND_ALLOWLIST",
            "GORGIAS_BRIDGE_ENABLED",
            "GORGIAS_BASE_URL",
            "GORGIAS_SUBDOMAIN",
            "GORGIAS_API_EMAIL",
            "GORGIAS_API_KEY",
        ):
            os.environ.pop(key, None)
        reset_tickets()

    def _intake(self, *, source: str = "agentmail", external=None) -> str:
        args = {
            "from": "Ada <ada.bridge@example.com>",
            "subject": "Where is #1001?",
            "body": "Tracking on order #1001 please",
            "receivedAt": "2026-09-06T12:00:00Z",
            "messageId": f"{source}-msg-send",
            "source": source,
            "external": external
            or {
                "system": source if source in {"gorgias", "agentmail"} else "agentmail",
                "ticketId": "4242" if source == "gorgias" else None,
                "messageId": f"{source}-msg-send",
                "customerEmail": "ada.bridge@example.com",
            },
        }
        if source == "gorgias":
            args["_fromBridge"] = True
        payload = dispatch("helpdesk.ingest_email", args)
        self.assertTrue(payload["ok"])
        return payload["ticketId"]

    def test_mcp_cli_human_only(self) -> None:
        self.assertIn(TOOL_SEND_REPLY, HUMAN_ONLY_TOOLS)
        blocked = invoke(TOOL_SEND_REPLY, {"ticketId": "t-in-1", "text": "Hi", "confirmed": True})
        self.assertEqual(blocked["error"], "human_only")

    def test_confirmation_and_seed_refused(self) -> None:
        unconfirmed = handle_http(
            TOOL_SEND_REPLY,
            {"ticketId": "t-ada-track", "text": "Hi", "confirmed": False},
            actor="human",
        )
        self.assertEqual(unconfirmed["error"], "send_access_inactive")
        self.assertEqual(unconfirmed["message"], "Activate the send access.")
        seed = handle_http(
            TOOL_SEND_REPLY,
            {"ticketId": "t-ada-track", "text": "Hi", "confirmed": True},
            actor="human",
        )
        self.assertEqual(seed["error"], "send_access_inactive")

    def test_allowlist_blocks(self) -> None:
        ticket_id = self._intake()
        os.environ["HELPDESK_SEND_ALLOWLIST"] = "other@example.com"
        blocked = handle_http(
            TOOL_SEND_REPLY,
            {"ticketId": ticket_id, "text": "Hi Ada", "confirmed": True},
            actor="human",
        )
        self.assertEqual(blocked["error"], "send_access_inactive")

    def test_email_route_stays_locked_when_outbound_env_on(self) -> None:
        os.environ["HELPDESK_OUTBOUND_ENABLED"] = "1"
        ticket_id = self._intake(source="agentmail")
        with patch("bridge.email_out.send_email_reply") as send:
            payload = handle_http(
                TOOL_SEND_REPLY,
                {"ticketId": ticket_id, "text": "On the way", "confirmed": True},
                actor="human",
            )
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"], "send_access_inactive")
            send.assert_not_called()

    def test_gorgias_route_stays_locked(self) -> None:
        os.environ["GORGIAS_BRIDGE_ENABLED"] = "1"
        os.environ["HELPDESK_OUTBOUND_ENABLED"] = "1"
        os.environ["GORGIAS_SUBDOMAIN"] = "demo"
        os.environ["GORGIAS_API_EMAIL"] = "agent@example.com"
        os.environ["GORGIAS_API_KEY"] = "key"
        sys.modules.pop("bridge.gorgias_api", None)
        ticket_id = self._intake(
            source="gorgias",
            external={
                "system": "gorgias",
                "ticketId": "4242",
                "messageId": "gorgias-msg-send",
                "customerEmail": "ada.bridge@example.com",
            },
        )
        with patch("bridge.gorgias_api.send_public_reply") as send:
            payload = handle_http(
                TOOL_SEND_REPLY,
                {"ticketId": ticket_id, "text": "Shipped", "confirmed": True},
                actor="human",
            )
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"], "send_access_inactive")
            send.assert_not_called()

    def test_gorgias_module_not_loaded_when_send_locked(self) -> None:
        before = {k for k in sys.modules if "gorgias_api" in k}
        ticket_id = self._intake(source="agentmail")
        handle_http(
            TOOL_SEND_REPLY,
            {"ticketId": ticket_id, "text": "Hi", "confirmed": True},
            actor="human",
        )
        after = {k for k in sys.modules if "gorgias_api" in k}
        self.assertEqual(before, after)

    def test_route_label(self) -> None:
        os.environ["HELPDESK_OUTBOUND_ENABLED"] = "0"
        self.assertEqual(route_label({"source": "seed"}, None), "Demo: stays local")
        self.assertEqual(route_label({"source": "gorgias", "fromEmail": "a@b.c"}), "Demo: stays local")
        os.environ["HELPDESK_OUTBOUND_ENABLED"] = "1"
        self.assertIn("email", route_label({"source": "agentmail", "fromEmail": "a@b.c"}))


class GorgiasApiAdapterTests(unittest.TestCase):
    """Direct adapter tests — offline; the bridge stays dormant unless flagged."""

    def setUp(self) -> None:
        os.environ["GORGIAS_SUBDOMAIN"] = "demo"
        os.environ["GORGIAS_API_EMAIL"] = "agent@example.com"
        os.environ["GORGIAS_API_KEY"] = "key"
        os.environ["GORGIAS_BRIDGE_ENABLED"] = "1"

    def tearDown(self) -> None:
        for key in ("GORGIAS_SUBDOMAIN", "GORGIAS_API_EMAIL", "GORGIAS_API_KEY", "GORGIAS_BRIDGE_ENABLED"):
            os.environ.pop(key, None)

    def test_body_html_matches_production_escaping(self) -> None:
        import html as html_mod

        import bridge.gorgias_api as api

        captured = {}
        fake_message = {"data": [
            {"from_agent": False, "channel": "email", "sender": {"email": "cust@example.com"}}]}

        def fake_request(method, path, *, body=None, retries=1):
            if "limit=" in path:
                return fake_message
            if method == "POST" and path == "/tickets/4242/messages":
                captured["body"] = body
                return {"id": 4243}
            if path == "/tickets/4242/messages/4243":
                return {"id": 4243, "sent_datetime": "2026-09-17T00:00:00Z"}
            raise AssertionError(f"unexpected _request call {method} {path}")

        with patch("helpdesk.send_access.send_access_enabled", lambda: True), \
             patch.object(api, "_request", side_effect=fake_request):
            result = api.send_public_reply("4242", "R&D <ship> & go\nline two")
        text = "R&D <ship> & go\nline two"
        self.assertEqual(result["ok"], True)
        self.assertEqual(captured["body"]["body_html"], html_mod.escape(text).replace("\n", "<br>"))

    def test_unreachable_api_raises_structured_runtime_error(self) -> None:
        import urllib.error

        import bridge.gorgias_api as api

        def raise_urlerror(*args, **kwargs):
            raise urllib.error.URLError("connect EPERM")

        with patch("urllib.request.urlopen", side_effect=raise_urlerror):
            with self.assertRaises(RuntimeError) as ctx:
                api._request("GET", "/tickets/1")
        self.assertIn("unreachable", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, urllib.error.URLError)

    def test_unreachable_api_returns_ok_false_from_send_public_reply(self) -> None:
        import bridge.gorgias_api as api

        with patch("helpdesk.send_access.send_access_enabled", lambda: True), \
             patch.object(api, "_request", side_effect=RuntimeError("Gorgias unreachable")):
            result = api.send_public_reply("4242", "a reply")
        self.assertEqual(result["ok"], False)
        self.assertIn("unreachable", result["error"])

    def test_non_numeric_ticket_id_returns_structured_error_not_crash(self) -> None:
        import bridge.gorgias_api as api

        with patch("helpdesk.send_access.send_access_enabled", lambda: True), \
             patch.object(api, "_request") as request:
            for bad in ("abc", "12;DROP", "  ", None, [], 4.2):
                self.assertEqual(api.send_public_reply(bad, "text"), {"ok": False, "error": "invalid ticket id"})
                self.assertEqual(api.close_ticket(bad), {"ok": False, "error": "invalid ticket id"})
        request.assert_not_called()


class LiveToolCountTests(unittest.TestCase):
    def test_seventeen_live_tools(self) -> None:
        self.assertEqual(len(TOOL_NAMES), 17)
        self.assertIn(TOOL_BRIDGE_STATUS, TOOL_NAMES)
        self.assertIn(TOOL_SEND_REPLY, TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
