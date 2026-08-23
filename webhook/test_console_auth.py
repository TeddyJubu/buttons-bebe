"""Tests for the form-login session primitives used by the console."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from bb_webhook.console_auth import (
    build_session_token,
    hash_password,
    safe_next_path,
    verify_password,
    verify_session_token,
)


class ConsolePasswordTests(unittest.TestCase):
    def test_password_hash_round_trip_and_wrong_password_fail_closed(self) -> None:
        encoded = hash_password("correct horse battery staple", salt=b"0123456789abcdef")

        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong password", encoded))
        self.assertFalse(verify_password("correct horse battery staple", "not-a-hash"))
        self.assertFalse(
            verify_password(
                "correct horse battery staple",
                "pbkdf2_sha256$600000$AA$" + "AA" * 43,
            )
        )

    def test_legacy_bcrypt_hash_can_be_verified_during_proxy_migration(self) -> None:
        bcrypt_hash = "$2b$12$" + "a" * 53
        with patch("bb_webhook.console_auth.crypt") as legacy_crypt:
            legacy_crypt.crypt.return_value = bcrypt_hash
            self.assertTrue(verify_password("correct password", bcrypt_hash))
            legacy_crypt.crypt.return_value = "$2b$12$wrong"
            self.assertFalse(verify_password("wrong password", bcrypt_hash))


class ConsoleSessionTests(unittest.TestCase):
    _now = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)

    def test_session_round_trip(self) -> None:
        token = build_session_token("chaim", "session-secret", now=self._now)

        self.assertEqual(
            verify_session_token(token, "session-secret", now=self._now),
            "chaim",
        )

    def test_expired_or_tampered_session_fails_closed(self) -> None:
        token = build_session_token("chaim", "session-secret", now=self._now)
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")

        self.assertIsNone(
            verify_session_token(
                token,
                "session-secret",
                now=self._now.replace(day=24, hour=3),
            )
        )
        self.assertIsNone(
            verify_session_token(tampered, "session-secret", now=self._now)
        )
        self.assertIsNone(
            verify_session_token(token, "different-secret", now=self._now)
        )
        self.assertIsNone(verify_session_token("x" * 4097, "session-secret", now=self._now))


class SafeNextPathTests(unittest.TestCase):
    def test_only_internal_console_paths_are_allowed(self) -> None:
        self.assertEqual(safe_next_path("/console/"), "/console/")
        self.assertEqual(safe_next_path("/console/api/tickets"), "/console/api/tickets")
        self.assertEqual(safe_next_path("https://evil.example/"), "/console/")
        self.assertEqual(safe_next_path("//evil.example/"), "/console/")
        self.assertEqual(safe_next_path("/dashboard"), "/console/")
        self.assertEqual(safe_next_path("/console.evil"), "/console/")
        self.assertEqual(safe_next_path(42), "/console/")


if __name__ == "__main__":
    unittest.main()
