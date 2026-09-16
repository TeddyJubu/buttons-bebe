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
                "ticket_id": 12,
                "job_status": "done",
                "priority": "high",
                "ticket_subject": "Where is my order?",
                "processed_at": "2026-07-14T10:00:00+00:00",
            },
            {
                "message_id": "failed",
                "ticket_id": 7,
                "job_status": "failed",
                "priority": "critical",
                "ticket_subject": "Refund request",
                "job_finished_at": "2026-07-14T11:00:00+00:00",
            },
        ])

        self.assertEqual(
            [item["id"] for item in notifications],
            ["failed:failed:7", "review:review:12"],
        )
        self.assertEqual(notifications[0]["filter"], "failed")
        self.assertEqual(notifications[1]["title"], "High-priority ticket needs review")

    def test_tickets_sharing_a_message_id_get_distinct_notification_ids(self) -> None:
        """Two failed tickets on one message_id must not collide on read-state.

        The legacy store shape lets distinct tickets share a message_id; with a
        shared id, marking one as read silently suppressed the other's badge.
        """
        notifications = dashboard_notifications([
            {
                "message_id": "shared",
                "ticket_id": 1,
                "job_status": "failed",
                "ticket_subject": "First failure",
            },
            {
                "message_id": "shared",
                "ticket_id": 2,
                "job_status": "failed",
                "ticket_subject": "Second failure",
            },
        ])

        self.assertEqual(
            [item["id"] for item in notifications],
            ["failed:shared:1", "failed:shared:2"],
        )

    def test_review_tickets_sharing_a_message_id_get_distinct_notification_ids(self) -> None:
        """The review feed has the same collision; same fix, same proof."""
        notifications = dashboard_notifications([
            {
                "message_id": "shared-review",
                "ticket_id": 3,
                "job_status": "done",
                "priority": "high",
                "ticket_subject": "First review",
            },
            {
                "message_id": "shared-review",
                "ticket_id": 4,
                "job_status": "done",
                "action": "escalated",
                "ticket_subject": "Second review",
            },
        ])

        self.assertEqual(
            [item["id"] for item in notifications],
            ["review:shared-review:3", "review:shared-review:4"],
        )

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
