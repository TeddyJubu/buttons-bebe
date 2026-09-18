#!/usr/bin/env python3
"""Ticket-state normalizers stay honest against template renderings.

Gorgias HTTP Integration templates render scalars as strings: an unset
value arrives as "None", and `| tojson` over a missing object renders the
literal string "null". Those are absent values, never observations — a
stored assignee of "null" would break the inbox's Unassigned view and a
stored status of "none" would corrupt the status views.
"""
import unittest

from bb_webhook.webhook_handler import (
    _normalize_ticket_assignee,
    _normalize_ticket_priority,
    _normalize_ticket_snoozed,
    _normalize_ticket_spam,
    _normalize_ticket_status,
    _normalize_ticket_tags,
    _normalize_ticket_trashed,
)


class TemplateNoneRenderingsTest(unittest.TestCase):
    def test_none_and_null_renderings_are_absent_not_observations(self):
        for rendered in ("None", "none", "null", "NULL", " nil ", ""):
            self.assertIsNone(_normalize_ticket_status(rendered), rendered)
            self.assertIsNone(_normalize_ticket_priority(rendered), rendered)
            self.assertIsNone(_normalize_ticket_assignee(rendered), rendered)

    def test_unassigned_tojson_null_is_not_an_assignee(self):
        # "assignee_user": "{{ ticket.assignee_user | tojson }}" on an
        # unassigned ticket renders the string "null", not a JSON object.
        self.assertIsNone(_normalize_ticket_assignee("null"))
        self.assertIsNone(_normalize_ticket_assignee("None"))
        self.assertIsNone(_normalize_ticket_assignee(None))
        # The documented object and plain-string forms still work.
        self.assertEqual(
            _normalize_ticket_assignee('{"email": "amy@example.com", "id": 7}'),
            "amy@example.com",
        )
        self.assertEqual(_normalize_ticket_assignee("amy@example.com"), "amy@example.com")

    def test_real_observations_survive_normalization(self):
        self.assertEqual(_normalize_ticket_status(" open "), "open")
        self.assertEqual(_normalize_ticket_priority("Urgent"), "urgent")
        self.assertEqual(_normalize_ticket_assignee("agent@example.com"), "agent@example.com")
        self.assertEqual(
            _normalize_ticket_tags('["vip", "urgent"]'), ["vip", "urgent"]
        )
        self.assertEqual(_normalize_ticket_spam("True"), 1)
        self.assertEqual(_normalize_ticket_spam("False"), 0)
        self.assertEqual(_normalize_ticket_trashed("None"), 0)
        self.assertEqual(_normalize_ticket_snoozed("None"), 0)
        # A scheduled snooze while status still reads open stays observed.
        self.assertEqual(_normalize_ticket_snoozed("2026-09-01T12:00:00+00:00"), 1)


if __name__ == "__main__":
    unittest.main()
