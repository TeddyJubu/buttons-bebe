"""Intake organ: email + chat → signed ticket or spam. Cute Things join only."""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpdesk.dispatch import WRITE_TOOLS, dispatch, invoke
from helpdesk.fixtures_intake import (
    ADA_TRACKING,
    CHAT_WITH_1001,
    CHAT_WITHOUT_ORDER,
    JORDAN_WRONG,
    PRIYA_RETURN,
    LEE_PRIVACY,
    PRIYA_UNSUB,
    REMY_BUG,
    PRIZE_SPAM,
    SAM_RATTLE,
)
from helpdesk.fixtures_live_holes import C_UNFULFILLED, O_1001
from helpdesk.fixtures_sample import ADA
from helpdesk.names import SAMPLE_SHOP, TOOL_INGEST_CHAT, TOOL_INGEST_EMAIL, TOOL_NAMES
from helpdesk.queries import CUSTOMER_BY_EMAIL_QUERY, CUSTOMER_QUERY, ORDER_BY_NAME_QUERY
from helpdesk.tickets import reset as reset_tickets


class IntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tickets()
        os.environ["SHOPIFY_MUTATIONS_ENABLED"] = "0"

    def test_ingest_email_ada_joins_unfulfilled_1001(self) -> None:
        payload = dispatch(TOOL_INGEST_EMAIL, ADA_TRACKING)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["spam"])
        self.assertEqual(payload["customerName"], "Ada")
        self.assertNotEqual(payload["customerName"], "Demo Unfulfilled")
        self.assertNotIn("displayName", payload)
        self.assertEqual(payload["customerId"], C_UNFULFILLED)
        self.assertEqual(payload["orderId"], O_1001)
        self.assertEqual(payload["status"], "open")
        self.assertNotEqual(payload["status"], "OPEN")
        listed = dispatch("helpdesk.list_tickets", {"view": "open", "limit": 20})["tickets"]
        self.assertTrue(any(row["id"] == payload["id"] for row in listed))
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": payload["id"]})["ticket"]
        self.assertEqual(ticket["messages"][0]["body"], ADA_TRACKING["body"])
        self.assertEqual(ticket["messages"][0]["name"], "Ada")
        self.assertEqual(ticket["messages"][0]["from"], "customer")
        self.assertEqual(ticket["messages"][0]["fromName"], "Ada")
        self.assertEqual(ticket["messages"][0]["fromEmail"], "ada.tracking@example.com")
        self.assertNotEqual(ticket["messages"][0]["fromName"].lower(), "teddyjubu")
        self.assertNotIn("displayName", ticket["messages"][0])
        self.assertNotIn("customerId", ticket["messages"][0])
        self.assertNotIn("orderId", ticket["messages"][0])

    def test_ingest_email_prize_is_spam_and_not_listed(self) -> None:
        payload = dispatch(TOOL_INGEST_EMAIL, PRIZE_SPAM)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["spam"])
        # #33: spam files a reviewable ticket in Spam instead of being dropped.
        self.assertIsNotNone(payload["ticketId"])
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": payload["ticketId"]})["ticket"]
        self.assertTrue(ticket["spam"])
        self.assertEqual(ticket["spamSource"], "intake-keywords")
        self.assertEqual(ticket["messages"][0]["body"], PRIZE_SPAM["body"])
        for view in ("all", "open", "mine", "unassigned", "snoozed", "closed"):
            listed = dispatch("helpdesk.list_tickets", {"view": view, "limit": 100})["tickets"]
            self.assertFalse(any("prize" in f"{row['subject']} {row['snippet']}".lower() for row in listed), view)
            self.assertFalse(any(row.get("id") == payload.get("id") for row in listed), view)
        spam_rows = dispatch("helpdesk.list_tickets", {"view": "spam", "limit": 100})["tickets"]
        self.assertTrue(any(row["id"] == payload["ticketId"] for row in spam_rows))

    def test_ingest_email_unsubscribe_subject_sets_request_type(self) -> None:
        payload = dispatch(TOOL_INGEST_EMAIL, PRIYA_UNSUB)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["spam"])
        self.assertEqual(payload["requestType"], "marketing_unsubscribe")
        listed = dispatch("helpdesk.list_tickets", {"view": "open", "limit": 20})["tickets"]
        row = next(item for item in listed if item["id"] == payload["id"])
        self.assertEqual(row["requestType"], "marketing_unsubscribe")
        ada = dispatch(TOOL_INGEST_EMAIL, ADA_TRACKING)
        self.assertIsNone(ada.get("requestType"))
        prize = dispatch(TOOL_INGEST_EMAIL, PRIZE_SPAM)
        self.assertTrue(prize["spam"])
        self.assertIsNone(prize.get("requestType"))
        self.assertEqual(WRITE_TOOLS, frozenset({"helpdesk.send", "helpdesk.refund", "helpdesk.cancel"}))

    def test_ingest_email_privacy_subject_or_body_sets_request_type(self) -> None:
        payload = dispatch(TOOL_INGEST_EMAIL, LEE_PRIVACY)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["spam"])
        self.assertEqual(payload["requestType"], "privacy_request")
        listed = dispatch("helpdesk.list_tickets", {"view": "open", "limit": 20})["tickets"]
        row = next(item for item in listed if item["id"] == payload["id"])
        self.assertEqual(row["requestType"], "privacy_request")
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": payload["id"]})["ticket"]
        self.assertEqual(ticket["privacySubtype"], "delete")
        self.assertFalse(ticket["privacyHandled"])
        body_only = dispatch(
            TOOL_INGEST_EMAIL,
            {
                "from": "Lee Chen <lee.export@example.com>",
                "subject": "Account help",
                "body": "Please export my data under GDPR.",
                "receivedAt": "2026-08-30T14:22:00Z",
            },
        )
        self.assertTrue(body_only["ok"])
        self.assertFalse(body_only["spam"])
        self.assertEqual(body_only["requestType"], "privacy_request")
        export_ticket = dispatch("helpdesk.get_ticket", {"ticketId": body_only["id"]})["ticket"]
        self.assertEqual(export_ticket["privacySubtype"], "export")
        keyword = dispatch(
            TOOL_INGEST_EMAIL,
            {
                "from": "Lee Chen <lee.access@example.com>",
                "subject": "Data request",
                "body": "This is a privacy data request for a copy of my records.",
                "receivedAt": "2026-08-30T14:23:00Z",
            },
        )
        self.assertEqual(keyword["requestType"], "privacy_request")
        access_ticket = dispatch("helpdesk.get_ticket", {"ticketId": keyword["id"]})["ticket"]
        self.assertEqual(access_ticket["privacySubtype"], "access")
        unsub = dispatch(TOOL_INGEST_EMAIL, PRIYA_UNSUB)
        self.assertEqual(unsub["requestType"], "marketing_unsubscribe")
        ada = dispatch(TOOL_INGEST_EMAIL, ADA_TRACKING)
        self.assertIsNone(ada.get("requestType"))
        prize = dispatch(TOOL_INGEST_EMAIL, PRIZE_SPAM)
        self.assertTrue(prize["spam"])
        self.assertIsNone(prize.get("requestType"))
        self.assertEqual(WRITE_TOOLS, frozenset({"helpdesk.send", "helpdesk.refund", "helpdesk.cancel"}))

    def test_ingest_email_bug_keywords_set_severity_and_device(self) -> None:
        payload = dispatch(TOOL_INGEST_EMAIL, REMY_BUG)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["spam"])
        self.assertEqual(payload["requestType"], "bug")
        self.assertEqual(payload["severity"], "high")
        self.assertEqual(payload["device"], "iOS")
        listed = dispatch("helpdesk.list_tickets", {"view": "open", "limit": 20})["tickets"]
        row = next(item for item in listed if item["id"] == payload["id"])
        self.assertEqual(row["requestType"], "bug")
        self.assertEqual(row["severity"], "high")
        self.assertEqual(row["device"], "iOS")
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": payload["id"]})["ticket"]
        self.assertEqual(ticket["severity"], "high")
        self.assertEqual(ticket["device"], "iOS")
        android = dispatch(
            TOOL_INGEST_EMAIL,
            {
                "from": "Remy Cole <remy.android@example.com>",
                "subject": "Checkout bug on Android",
                "body": "Minor bug when I tap pay on Android.",
                "receivedAt": "2026-08-30T14:25:00Z",
            },
        )
        self.assertEqual(android["requestType"], "bug")
        self.assertEqual(android["severity"], "low")
        self.assertEqual(android["device"], "Android")
        broken = dispatch(
            TOOL_INGEST_EMAIL,
            {
                "from": "Remy Cole <remy.broken@example.com>",
                "subject": "Broken checkout on iOS",
                "body": "The pay button is broken on my iPhone.",
                "receivedAt": "2026-08-30T14:26:00Z",
            },
        )
        self.assertEqual(broken["requestType"], "bug")
        self.assertEqual(broken["severity"], "medium")
        self.assertEqual(broken["device"], "iOS")
        sam = dispatch(TOOL_INGEST_EMAIL, SAM_RATTLE)
        self.assertTrue(sam["ok"])
        self.assertFalse(sam["spam"])
        self.assertIsNone(sam.get("requestType"))
        self.assertIsNone(sam.get("severity"))
        self.assertIsNone(sam.get("device"))
        unsub = dispatch(TOOL_INGEST_EMAIL, PRIYA_UNSUB)
        self.assertEqual(unsub["requestType"], "marketing_unsubscribe")
        self.assertIsNone(unsub.get("severity"))
        ada = dispatch(TOOL_INGEST_EMAIL, ADA_TRACKING)
        self.assertIsNone(ada.get("requestType"))
        self.assertIsNone(ada.get("severity"))
        self.assertIsNone(ada.get("device"))
        prize = dispatch(TOOL_INGEST_EMAIL, PRIZE_SPAM)
        self.assertTrue(prize["spam"])
        self.assertIsNone(prize.get("requestType"))
        self.assertEqual(WRITE_TOOLS, frozenset({"helpdesk.send", "helpdesk.refund", "helpdesk.cancel"}))
        for tool in WRITE_TOOLS:
            refused = invoke(tool, {"ticketId": payload["id"]})
            self.assertEqual(refused["error"], "forbidden")

    def test_ingest_email_without_order_number_stays_gid_null(self) -> None:
        for fixture in (SAM_RATTLE, PRIYA_RETURN, JORDAN_WRONG):
            reset_tickets()
            payload = dispatch(TOOL_INGEST_EMAIL, fixture)
            self.assertTrue(payload["ok"], fixture["subject"])
            self.assertFalse(payload["spam"])
            self.assertIsNone(payload["customerId"])
            self.assertIsNone(payload["orderId"])
            self.assertEqual(payload["status"], "open")
            listed = dispatch("helpdesk.list_tickets", {"view": "open", "limit": 20})["tickets"]
            self.assertTrue(any(row["id"] == payload["id"] for row in listed))

    def test_ingest_chat_joins_or_stays_null(self) -> None:
        joined = dispatch(TOOL_INGEST_CHAT, CHAT_WITH_1001)
        self.assertTrue(joined["ok"])
        self.assertFalse(joined["spam"])
        self.assertEqual(joined["customerName"], "Ada")
        self.assertEqual(joined["customerId"], C_UNFULFILLED)
        self.assertEqual(joined["orderId"], O_1001)
        lonely = dispatch(TOOL_INGEST_CHAT, CHAT_WITHOUT_ORDER)
        self.assertTrue(lonely["ok"])
        self.assertEqual(lonely["customerName"], "Sam")
        self.assertIsNone(lonely["customerId"])
        self.assertIsNone(lonely["orderId"])

    def test_customer_name_is_from_name_not_display_name(self) -> None:
        payload = dispatch(TOOL_INGEST_EMAIL, ADA_TRACKING)
        customer = dispatch(
            "helpdesk.get_customer",
            {"shop": "yznyc1-ez.myshopify.com", "customerId": C_UNFULFILLED},
        )["customer"]
        self.assertEqual(payload["customerName"], "Ada")
        self.assertEqual(customer["displayName"], "Demo Unfulfilled")
        self.assertNotEqual(payload["customerName"], customer["displayName"])
        sample = dispatch("helpdesk.get_customer", {"shop": SAMPLE_SHOP, "customerId": ADA})["customer"]
        self.assertNotEqual(payload["customerName"], sample["displayName"])

    def test_roleplay_from_uses_persona_not_mailbox_login(self) -> None:
        payload = dispatch(
            TOOL_INGEST_EMAIL,
            {
                "from": "Pat Rivera <teddyjubu@agentmail.to>",
                "subject": "Thank you — gift arrived perfectly",
                "body": "I'm Pat Rivera. The gift arrived perfectly.",
                "receivedAt": "2026-09-04T04:51:09Z",
            },
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["customerName"], "Pat Rivera")
        self.assertNotEqual(payload["customerName"].lower(), "teddyjubu")
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": payload["id"]})["ticket"]
        inbound = ticket["messages"][0]
        self.assertEqual(inbound["from"], "customer")
        self.assertEqual(inbound["fromName"], "Pat Rivera")
        self.assertEqual(inbound["name"], "Pat Rivera")
        self.assertEqual(inbound["fromEmail"], "teddyjubu@agentmail.to")
        listed = dispatch("helpdesk.list_tickets", {"view": "open", "limit": 20})["tickets"]
        row = next(item for item in listed if item["id"] == payload["id"])
        self.assertEqual(row["customerName"], "Pat Rivera")

    def test_mailbox_login_alone_is_not_the_visible_from(self) -> None:
        payload = dispatch(
            TOOL_INGEST_EMAIL,
            {
                "from": "teddyjubu <teddyjubu@agentmail.to>",
                "subject": "Where is my order",
                "body": "Any update on this order?",
                "receivedAt": "2026-09-04T04:52:00Z",
            },
        )
        self.assertTrue(payload["ok"])
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": payload["id"]})["ticket"]
        self.assertEqual(ticket["customerName"], "Customer")
        self.assertEqual(ticket["messages"][0]["fromName"], "Customer")
        self.assertNotEqual(ticket["messages"][0]["fromName"].lower(), "teddyjubu")
        listed = dispatch("helpdesk.list_tickets", {"view": "open", "limit": 20})["tickets"]
        row = next(item for item in listed if item["id"] == payload["id"])
        self.assertEqual(row["customerName"], "Customer")

    def test_join_uses_default_email_address_never_customer_email_field(self) -> None:
        for document in (CUSTOMER_QUERY, CUSTOMER_BY_EMAIL_QUERY, ORDER_BY_NAME_QUERY):
            self.assertNotRegex(document, r"customer\s*\{[^}]*\bemail\b")
            self.assertFalse(re.search(r"^\s*email\s*$", document, re.M))
        self.assertIn("defaultEmailAddress", CUSTOMER_QUERY)
        self.assertIn("defaultEmailAddress", CUSTOMER_BY_EMAIL_QUERY)
        join_src = (ROOT / "helpdesk" / "join.py").read_text(encoding="utf-8")
        self.assertIn('email:"', join_src)
        self.assertIn("name:", join_src)
        query_src = (ROOT / "helpdesk" / "queries.py").read_text(encoding="utf-8")
        self.assertNotIn("customerCreate", query_src)
        self.assertNotRegex(query_src, r"(?m)^\s*mutation\b")
        self.assertNotIn("Customer.email", query_src)
        self.assertIn("defaultEmailAddress", query_src)

    def test_mutations_still_refused(self) -> None:
        for tool in WRITE_TOOLS:
            payload = invoke(tool, {"ticketId": "t-in-1"})
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"], "forbidden")
        self.assertIn(TOOL_INGEST_EMAIL, TOOL_NAMES)
        self.assertIn(TOOL_INGEST_CHAT, TOOL_NAMES)
        self.assertNotIn(TOOL_INGEST_EMAIL, WRITE_TOOLS)


if __name__ == "__main__":
    unittest.main()
