"""Contract checks for the composed webhook application."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bb_webhook import app as app_module


class RouterContractTests(unittest.TestCase):
    def test_all_preexisting_http_paths_are_registered(self) -> None:
        expected = {
            ("GET", "/health"),
            ("GET", "/ready"),
            ("POST", "/webhook/gorgias/{tenant_id}"),
            ("POST", "/auth/login"),
            ("GET", "/auth/session"),
            ("GET", "/auth/check"),
            ("GET", "/auth/page-check"),
            ("POST", "/auth/logout"),
            ("GET", "/dashboard/api/messages"),
            ("GET", "/dashboard/api/stats"),
            ("GET", "/dashboard/api/ops"),
            ("GET", "/dashboard/api/tickets"),
            ("GET", "/dashboard/api/owner-alerts"),
            ("GET", "/dashboard/api/notification-health"),
            ("POST", "/dashboard/api/results"),
            ("GET", "/dashboard/api/notifications"),
            ("POST", "/dashboard/api/notifications/read"),
            ("POST", "/dashboard/api/ticket/{ticket_id}/send"),
            ("POST", "/dashboard/api/ticket/{ticket_id}/note"),
            ("POST", "/dashboard/api/ticket/{ticket_id}/rewrite"),
            ("POST", "/dashboard/api/ticket/{ticket_id}/retry-draft"),
            ("GET", "/dashboard/api/ticket/{ticket_id}/actions/{operation_id}"),
            ("GET", "/dashboard/api/learning"),
            ("GET", "/dashboard/api/inbox/review-context/{inbox_ticket_id}"),
            ("POST", "/dashboard/api/inbox/send-access"),
            ("POST", "/dashboard/api/inbox/ticket/{ticket_id}/send"),
        }
        actual = {
            (method, path)
            for path, item in app_module.app.openapi()["paths"].items()
            for method in item
            for method in (method.upper(),)
        }
        self.assertEqual(actual, expected)

    def test_lifespan_keeps_logging_settings_and_db_startup_order(self) -> None:
        events: list[str] = []
        settings = SimpleNamespace(
            webhook_host="127.0.0.1",
            webhook_port=8000,
            gorgias_subdomain="test",
            db_path_absolute="unused-test.sqlite3",
        )

        async def init_db() -> None:
            events.append("db")

        async def exercise() -> None:
            async with app_module.lifespan(app_module.app):
                events.append("yield")

        with (
            patch.object(app_module, "setup_logging", Mock(side_effect=lambda: events.append("logging"))),
            patch.object(app_module, "get_settings", Mock(side_effect=lambda: (events.append("settings") or settings))),
            patch.object(app_module, "log_event", Mock(side_effect=lambda *args, **kwargs: events.append("log"))),
            patch.object(app_module, "init_db", AsyncMock(side_effect=init_db)),
            patch.object(app_module.session_store, "initialize", AsyncMock(side_effect=lambda *args: events.append("sessions"))),
        ):
            import asyncio

            asyncio.run(exercise())

        self.assertEqual(events, ["logging", "settings", "log", "db", "sessions", "yield", "log"])


if __name__ == "__main__":
    unittest.main()
