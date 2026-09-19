"""Issue #33: Gorgias spam parity on the first-party helpdesk.

Spam intake files a reviewable ticket in the Spam view instead of dropping
it. Working views (including all) exclude flagged rows exactly like the JS
view-model does.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpdesk.dispatch import dispatch, invoke
from helpdesk.fixtures_intake import ADA_TRACKING, PRIZE_SPAM
from helpdesk.names import TOOL_INGEST_CHAT, TOOL_INGEST_EMAIL
from helpdesk.tickets import reset as reset_tickets, ticket_in_view


class SpamParityTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tickets()
        os.environ["SHOPIFY_MUTATIONS_ENABLED"] = "0"

    def test_ingest_email_spam_files_a_reviewable_ticket(self) -> None:
        payload = dispatch(TOOL_INGEST_EMAIL, PRIZE_SPAM)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["spam"])
        self.assertIsNotNone(payload["ticketId"], "spam must be reviewable, not dropped")
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": payload["ticketId"]})["ticket"]
        self.assertTrue(ticket["spam"])
        self.assertEqual(ticket["messages"][0]["body"], PRIZE_SPAM["body"])
        # The decision is ours on this path, so its source is recorded per ticket.
        self.assertEqual(ticket["spamSource"], "intake-keywords")

    def test_spam_ticket_lands_in_spam_view_and_out_of_working_views(self) -> None:
        payload = dispatch(TOOL_INGEST_EMAIL, PRIZE_SPAM)
        spam_view = dispatch("helpdesk.list_tickets", {"view": "spam", "limit": 100})["tickets"]
        self.assertTrue(any(row["id"] == payload["ticketId"] for row in spam_view))
        for view in ("all", "open", "mine", "unassigned", "snoozed", "closed"):
            rows = dispatch("helpdesk.list_tickets", {"view": view, "limit": 100})["tickets"]
            self.assertFalse(any(row["id"] == payload["ticketId"] for row in rows), view)

    def test_ingest_chat_spam_files_a_reviewable_ticket_too(self) -> None:
        payload = dispatch(
            TOOL_INGEST_CHAT,
            {"fromName": "Prize Desk", "body": "You won a cash prize!", "receivedAt": "2026-09-01T00:00:00Z"},
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["spam"])
        self.assertIsNotNone(payload["ticketId"])
        spam_view = dispatch("helpdesk.list_tickets", {"view": "spam", "limit": 100})["tickets"]
        self.assertTrue(any(row["id"] == payload["ticketId"] for row in spam_view))

    def test_ticket_in_view_mirrors_the_js_view_model_partition(self) -> None:
        spam = {"id": "x", "status": "open", "assignee": None, "spam": True}
        trashed = {"id": "y", "status": "open", "assignee": None, "trashed": True}
        plain = {"id": "z", "status": "open", "assignee": None}
        for row in (spam, trashed):
            for view in ("all", "open", "mine", "unassigned", "snoozed", "closed"):
                self.assertFalse(ticket_in_view(row, view), f"{row['id']} in {view}")
        self.assertTrue(ticket_in_view(spam, "spam"))
        self.assertFalse(ticket_in_view(trashed, "spam"))
        self.assertTrue(ticket_in_view(trashed, "trash"))
        self.assertFalse(ticket_in_view(spam, "trash"))
        self.assertTrue(ticket_in_view(plain, "all"))

    def test_views_tuple_matches_the_js_view_ids(self) -> None:
        from helpdesk.tickets import VIEWS

        self.assertEqual(set(VIEWS), {"open", "closed", "all", "snoozed", "mine", "unassigned", "spam", "trash"})

    def test_send_and_shopify_join_stay_locked_for_spam_tickets(self) -> None:
        payload = dispatch(TOOL_INGEST_EMAIL, PRIZE_SPAM)
        ticket = dispatch("helpdesk.get_ticket", {"ticketId": payload["ticketId"]})["ticket"]
        self.assertIsNone(ticket["customerId"], "spam never joins Shopify")
        self.assertIsNone(ticket["orderId"], "spam never joins Shopify")
        # Sends are locked by the global mutation gate, not by spam flags —
        # pin that the refusal happens for any ticket while mutations are off.
        plain = dispatch(TOOL_INGEST_EMAIL, ADA_TRACKING)
        for tool in ("helpdesk.send", "helpdesk.refund", "helpdesk.cancel"):
            for ticket_id in (payload["ticketId"], plain["ticketId"]):
                refused = invoke(tool, {"ticketId": ticket_id})
                self.assertEqual(refused["error"], "forbidden", tool)


if __name__ == "__main__":
    unittest.main()
