"""Focused tests for the isolated webhook rate limiter."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from bb_webhook import app as app_module
from bb_webhook.middleware import rate_limit


class CheckRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit._rate_window.clear()

    def test_window_expires_and_other_ips_are_independent(self) -> None:
        now = [100.0]
        with patch.object(rate_limit.time, "monotonic", lambda: now[0]):
            self.assertTrue(rate_limit._check_rate_limit("198.51.100.10", max_requests=2))
            self.assertTrue(rate_limit._check_rate_limit("198.51.100.10", max_requests=2))
            self.assertFalse(rate_limit._check_rate_limit("198.51.100.10", max_requests=2))
            self.assertTrue(rate_limit._check_rate_limit("198.51.100.11", max_requests=2))

            now[0] = 160.01
            self.assertTrue(rate_limit._check_rate_limit("198.51.100.10", max_requests=2))

    def test_zero_limit_fails_closed(self) -> None:
        self.assertFalse(rate_limit._check_rate_limit("198.51.100.10", max_requests=0))


class RateLimitRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_request_returns_429_when_limiter_rejects(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.app),
            base_url="http://demo.test",
        ) as client:
            with (
                patch.object(app_module, "verify_signature", lambda *_args: True),
                patch.object(app_module, "_check_rate_limit", lambda _ip: False),
            ):
                response = await client.post(
                    "/webhook/gorgias/demo-tenant",
                    content=b"{}",
                    headers={"Content-Type": "application/json"},
                )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json(), {"error": "rate_limited"})

    async def test_app_level_limit_and_body_cap_patches_remain_effective(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.app),
            base_url="http://demo.test",
        ) as client:
            app_module._rate_window.clear()
            with (
                patch.object(app_module, "verify_signature", lambda *_args: True),
                patch.object(app_module, "parse_event", lambda _raw: None),
                patch.object(app_module, "_MAX_REQUESTS_PER_MINUTE", 1),
            ):
                first = await client.post("/webhook/gorgias/demo", content=b"{}")
                second = await client.post("/webhook/gorgias/demo", content=b"{}")

            with patch.object(app_module, "_MAX_WEBHOOK_BODY_BYTES", 1):
                oversized = await client.post(
                    "/webhook/gorgias/demo",
                    content=b"{}",
                )

        self.assertEqual(first.status_code, 400)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(oversized.status_code, 413)


if __name__ == "__main__":
    unittest.main()
