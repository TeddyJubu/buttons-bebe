"""Endpoint tests for the cookie-backed human console login."""

from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path
from bb_webhook import session_store
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import patch

from bb_webhook.console_auth import build_session_token, hash_password
from bb_webhook.routers import auth
from bb_webhook.middleware import console_session
from starlette.requests import Request


def request_for(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    cookie: str = "",
) -> Request:
    raw_body = b"" if body is None else json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"origin", b"https://support.buttonsbebe.com")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
        "scheme": "https",
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request(scope, receive)


class ConsoleAuthEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = SimpleNamespace(
            console_username="chaim",
            console_password_hash=hash_password("correct password", salt=b"0123456789abcdef"),
            console_session_secret="session-secret",
            demo_mode=True,
            db_path_absolute=Path(self.temp.name)/"db.sqlite3",
        )

        await session_store.initialize(self.settings.db_path_absolute)

    def tearDown(self):
        self.temp.cleanup()

    def cookie_from(self, response) -> str:
        parsed = SimpleCookie()
        parsed.load(response.headers["set-cookie"])
        return parsed["bb_console_session"].value

    async def test_login_sets_session_and_session_check_accepts_it(self) -> None:
        request = request_for(
            "POST", "/auth/login",
            body={"username": "chaim", "password": "correct password", "next": "/console/tickets"},
        )
        with patch.object(auth, "get_settings", return_value=self.settings), \
             patch.object(console_session, "get_settings", return_value=self.settings), \
             patch.object(auth, "_login_allowed", return_value=True):
            response = await auth.auth_login(request)

            self.assertEqual(response.status_code, 200)
            self.assertIn('"redirect":"/console/tickets"', response.body.decode())
            cookie = self.cookie_from(response)
            session = await auth.auth_session(request_for("GET", "/auth/session", cookie=f"bb_console_session={cookie}"))
            check = await auth.auth_check(request_for("GET", "/auth/check", cookie=f"bb_console_session={cookie}"))

        self.assertEqual(session.status_code, 200)
        self.assertEqual(check.status_code, 204)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])

    async def test_bad_credentials_and_unconfigured_auth_fail_closed(self) -> None:
        bad = request_for("POST", "/auth/login", body={"username": "chaim", "password": "wrong"})
        with patch.object(auth, "get_settings", return_value=self.settings), \
             patch.object(console_session, "get_settings", return_value=self.settings), \
             patch.object(auth, "_login_allowed", return_value=True):
            response = await auth.auth_login(bad)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("correct", response.body.decode())

        unconfigured = SimpleNamespace(
            console_username="chaim", console_password_hash="", console_session_secret="", demo_mode=False
        )
        with patch.object(auth, "get_settings", return_value=unconfigured), \
             patch.object(console_session, "get_settings", return_value=unconfigured), \
             patch.object(auth, "_login_allowed", return_value=True):
            response = await auth.auth_login(request_for("POST", "/auth/login", body={}))
        self.assertEqual(response.status_code, 503)

    async def test_page_check_redirects_and_logout_expires_cookie(self) -> None:
        with patch.object(auth, "get_settings", return_value=self.settings), \
             patch.object(console_session, "get_settings", return_value=self.settings):
            redirect = await auth.auth_page_check(request_for("GET", "/auth/page-check"))
            self.assertEqual(redirect.status_code, 302)
            self.assertEqual(redirect.headers["location"], "/console/login?next=%2Fconsole%2F")

            token = build_session_token("chaim", self.settings.console_session_secret)
            logged_out = await auth.auth_logout(request_for("POST", "/auth/logout"))

        self.assertEqual(logged_out.status_code, 200)
        self.assertIn('bb_console_session=""', logged_out.headers["set-cookie"])
        self.assertIsNotNone(token)


if __name__ == "__main__":
    unittest.main()
