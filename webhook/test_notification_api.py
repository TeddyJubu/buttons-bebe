"""Endpoint coverage for the live dashboard notification API."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from bb_webhook import app as app_module


class DashboardNotificationApiTests(unittest.IsolatedAsyncioTestCase):
    _tickets = [{
        "message_id": "review",
        "ticket_id": 12,
        "job_status": "done",
        "priority": "high",
        "ticket_subject": "Where is my order?",
    }]

    async def test_feed_includes_server_persisted_read_state(self) -> None:
        with (
            patch.object(app_module, "get_dashboard_tickets", AsyncMock(return_value=self._tickets)),
            patch.object(app_module, "get_setting", AsyncMock(return_value='{"review:review":"earlier"}')),
        ):
            response = await app_module.dashboard_notifications_api()

        body = response.body.decode()
        self.assertIn('"unread_count":0', body)
        self.assertIn('"read":true', body)

    async def test_acknowledgement_prunes_stale_ids_and_marks_current_items(self) -> None:
        class Request:
            async def json(self):
                return {"ids": ["review:review", "not-current"]}

        store = AsyncMock()
        with (
            patch.object(app_module, "get_dashboard_tickets", AsyncMock(return_value=self._tickets)),
            patch.object(app_module, "get_setting", AsyncMock(return_value='{"stale":"earlier"}')),
            patch.object(app_module, "set_setting", store),
        ):
            response = await app_module.mark_dashboard_notifications_read(Request())

        self.assertIn('"unread_count":0', response.body.decode())
        saved_state = store.await_args.args[1]
        self.assertIn("review:review", saved_state)
        self.assertNotIn("stale", saved_state)


if __name__ == "__main__":
    unittest.main()
