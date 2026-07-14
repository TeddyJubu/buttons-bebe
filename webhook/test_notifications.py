"""Offline coverage for live dashboard notification derivation."""

from __future__ import annotations

import unittest

from bb_webhook.notifications import dashboard_notifications


class DashboardNotificationTests(unittest.TestCase):
    def test_only_current_human_action_items_are_emitted(self) -> None:
        notifications = dashboard_notifications([
            {
                "message_id": "normal",
                "job_status": "done",
                "priority": "normal",
                "ticket_subject": "Sizing question",
            },
            {
                "message_id": "review",
                "job_status": "done",
                "priority": "high",
                "ticket_subject": "Where is my order?",
                "processed_at": "2026-07-14T10:00:00+00:00",
            },
            {
                "message_id": "failed",
                "job_status": "failed",
                "priority": "critical",
                "ticket_subject": "Refund request",
                "job_finished_at": "2026-07-14T11:00:00+00:00",
            },
        ])

        self.assertEqual([item["id"] for item in notifications], ["failed:failed", "review:review"])
        self.assertEqual(notifications[0]["filter"], "failed")
        self.assertEqual(notifications[1]["title"], "High-priority ticket needs review")

    def test_escalation_is_shown_as_a_sensitive_review(self) -> None:
        notifications = dashboard_notifications([
            {
                "message_id": "sensitive",
                "job_status": "done",
                "priority": "normal",
                "action": "escalated",
                "ticket_subject": "Damaged item",
            },
        ])

        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["kind"], "review")
        self.assertEqual(notifications[0]["title"], "Sensitive ticket needs review")
if __name__ == "__main__":
    unittest.main()
