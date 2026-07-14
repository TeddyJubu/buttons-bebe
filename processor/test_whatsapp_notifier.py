from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from whatsapp_notifier import send_whatsapp  # noqa: E402


AUTH_SECRET = "send-secret-" + "x" * 32
SEND_URL = "http://127.0.0.1:8085/connect-whatsapp/test-token/send"


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _send() -> bool:
    return send_whatsapp(
        ticket_id=123456,
        subject="Test subject",
        customer_email="test@example.com",
        message_summary="Test summary",
        reason="Test reason",
    )


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        SEND_URL,
        status,
        "test failure",
        hdrs=None,
        fp=io.BytesIO(),
    )


def test_missing_url_or_secret_fails_closed() -> None:
    with patch.dict(os.environ, {"WHATSAPP_SEND_URL": "", "WA_SEND_SECRET": ""}, clear=False):
        with patch("whatsapp_notifier.urllib.request.urlopen") as urlopen:
            assert _send() is False
            urlopen.assert_not_called()

    with patch.dict(os.environ, {"WHATSAPP_SEND_URL": SEND_URL, "WA_SEND_SECRET": ""}, clear=False):
        with patch("whatsapp_notifier.urllib.request.urlopen") as urlopen:
            assert _send() is False
            urlopen.assert_not_called()


def test_sends_bearer_secret_without_putting_it_in_url() -> None:
    with patch.dict(os.environ, {"WHATSAPP_SEND_URL": SEND_URL, "WA_SEND_SECRET": AUTH_SECRET}, clear=False):
        with patch("whatsapp_notifier.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            assert _send() is True

    request = urlopen.call_args.args[0]
    assert request.full_url == SEND_URL
    assert request.get_header("Authorization") == f"Bearer {AUTH_SECRET}"
    assert json.loads(request.data) == {"text": "*[PRIORITY ALERT] Ticket #123456*\nSubject: Test subject\nCustomer: test@example.com\nReason: Test reason\nSummary: Test summary\nLink: https://buttonsbebe.gorgias.com/tickets/123456"}


def test_401_is_not_retried() -> None:
    with patch.dict(os.environ, {"WHATSAPP_SEND_URL": SEND_URL, "WA_SEND_SECRET": AUTH_SECRET}, clear=False):
        with patch("whatsapp_notifier.urllib.request.urlopen", side_effect=_http_error(401)) as urlopen:
            with patch("whatsapp_notifier.time.sleep") as sleep:
                assert _send() is False

    assert urlopen.call_count == 1
    sleep.assert_not_called()


def test_409_retries_once_then_succeeds() -> None:
    with patch.dict(os.environ, {"WHATSAPP_SEND_URL": SEND_URL, "WA_SEND_SECRET": AUTH_SECRET}, clear=False):
        with patch(
            "whatsapp_notifier.urllib.request.urlopen",
            side_effect=[_http_error(409), FakeResponse()],
        ) as urlopen:
            with patch("whatsapp_notifier.time.sleep") as sleep:
                assert _send() is True

    assert urlopen.call_count == 2
    sleep.assert_called_once_with(2)


def test_5xx_exhaustion_has_three_bounded_waits() -> None:
    failures = [_http_error(503) for _ in range(4)]
    with patch.dict(os.environ, {"WHATSAPP_SEND_URL": SEND_URL, "WA_SEND_SECRET": AUTH_SECRET}, clear=False):
        with patch("whatsapp_notifier.urllib.request.urlopen", side_effect=failures) as urlopen:
            with patch("whatsapp_notifier.time.sleep") as sleep:
                assert _send() is False

    assert urlopen.call_count == 4
    assert sleep.call_args_list == [call(2), call(5), call(10)]


def test_unexpected_error_is_fail_soft_and_bounded() -> None:
    with patch.dict(os.environ, {"WHATSAPP_SEND_URL": SEND_URL, "WA_SEND_SECRET": AUTH_SECRET}, clear=False):
        with patch("whatsapp_notifier.urllib.request.urlopen", side_effect=RuntimeError("offline")) as urlopen:
            with patch("whatsapp_notifier.time.sleep") as sleep:
                assert _send() is False

    assert urlopen.call_count == 4
    assert sleep.call_args_list == [call(2), call(5), call(10)]


def load_tests(_loader, _tests, _pattern):
    """Expose the function-style cases to the repository's unittest gate."""
    names = [name for name in globals() if name.startswith("test_")]
    return unittest.TestSuite(
        unittest.FunctionTestCase(globals()[name]) for name in sorted(names)
    )
