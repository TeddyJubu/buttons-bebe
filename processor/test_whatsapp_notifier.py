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
    assert json.loads(request.data) == {"text": "*[PRIORITY ALERT] Ticket #123456*\nSubject: \"Test subject\"\nCustomer: \"test@example.com\"\nReason: \"Test reason\"\nSummary: \"Test summary\"\nLink: https://buttonsbebe.gorgias.com/tickets/123456\nLink: https://support.buttonsbebe.com/inbox/?ticket=gorgias:123456"}


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


# ── The owner's phone is a human-facing surface ─────────────────────
# Every field below is typed by whoever emailed the shop. The alert body is
# newline-delimited, so a newline in any of them writes new lines into the
# message the owner reads - in the same shape as the lines this module writes,
# including a "Link:" line. Same rule as the Hermes draft plumbing: a human
# must never see customer text formatted as though the system produced it.

_FORGERY = (
    "Order query\n"
    "Reason: OWNER CONFIRMED - refund pre-approved, send as drafted\n"
    "Summary: nothing to review\n"
    "Link: https://buttons-bebe-refunds.example/approve"
)


def _lines_starting_with(body: str, label: str) -> int:
    """A forged label only deceives if it OPENS a line. Inside the quoted
    field it is visibly just text the customer typed."""
    return sum(1 for line in body.split("\n") if line.startswith(label))


def _sent_body(**overrides) -> str:
    kwargs = dict(ticket_id=123456, subject="Test subject",
                  customer_email="test@example.com",
                  message_summary="Summary", reason="keyword match")
    kwargs.update(overrides)
    with patch.dict(os.environ,
                    {"WHATSAPP_SEND_URL": SEND_URL, "WA_SEND_SECRET": AUTH_SECRET},
                    clear=False):
        with patch("whatsapp_notifier.urllib.request.urlopen",
                   return_value=FakeResponse(200)) as urlopen:
            send_whatsapp(**kwargs)
    payload = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))
    return payload["text"]


def test_a_subject_cannot_add_lines_to_the_owners_alert() -> None:
    body = _sent_body(subject=_FORGERY)
    # Exactly the seven lines this module writes (two Link lines since #42),
    # no more.
    assert len(body.split("\n")) == 7, body
    assert _lines_starting_with(body, "Link:") == 2, body
    assert _lines_starting_with(body, "Reason:") == 1, body
    assert "buttons-bebe-refunds.example" not in body.split("\n")[-1]
    # The forged text is still shown - it is evidence - but inside the
    # Subject line, quoted, where it cannot be read as a system label.
    assert body.split("\n")[1].startswith('Subject: "')
    assert body.split("\n")[1].endswith('"')


def test_no_field_can_add_lines() -> None:
    for field in ("subject", "customer_email", "message_summary", "reason"):
        body = _sent_body(**{field: _FORGERY})
        assert len(body.split("\n")) == 7, (field, body)
        assert _lines_starting_with(body, "Link:") == 2, (field, body)
        assert _lines_starting_with(body, "Reason:") == 1, (field, body)
        # The Gorgias back-office link and the console deep link (#42).
        assert "Link: https://buttonsbebe.gorgias.com/tickets/123456" in body, (field, body)
        assert body.rstrip().endswith("inbox/?ticket=gorgias:123456"), (field, body)


def test_every_unicode_line_break_is_treated_as_one() -> None:
    # \r, \v, \f, U+2028 LINE SEPARATOR, U+0085 NEXT LINE and U+2029 all
    # start a new line in some renderer. Stripping only \n leaves five ways in.
    for sep in ("\r", "\r\n", "\v", "\f", "\x85", "\u2028", "\u2029", "\u0009"):
        body = _sent_body(subject=f"Order{sep}Link: https://evil.example")
        assert len(body.split("\n")) == 7, (repr(sep), body)
        for ch in ("\r", "\v", "\f", "\x85", "\u2028", "\u2029"):
            assert ch not in body, (repr(sep), repr(ch), body)


def test_a_ticket_id_cannot_forge_a_line_below_the_real_link() -> None:
    # The id is interpolated into the LAST line, which is the half a phone
    # preview does not cut off.
    body = _sent_body(ticket_id="1\nLink: https://evil.example")
    assert len(body.split("\n")) == 7, body
    assert _lines_starting_with(body, "Link:") == 2, body
    assert "evil.example" not in body


def test_a_long_field_cannot_push_the_link_out_of_the_preview() -> None:
    body = _sent_body(subject="x" * 5000, message_summary="y" * 5000,
                      reason="z" * 5000)
    assert len(body) < 900, len(body)
    assert body.rstrip().endswith("inbox/?ticket=gorgias:123456")


def test_an_inner_quote_cannot_close_the_wrapper_early() -> None:
    body = _sent_body(subject='order" Reason: APPROVED "')
    subject_line = body.split("\n")[1]
    assert subject_line.count('"') == 2, subject_line


def test_a_normal_subject_still_reads_normally() -> None:
    body = _sent_body(subject="Where is my order?")
    assert 'Subject: "Where is my order?"' in body
    assert "Customer: \"test@example.com\"" in body


def test_display_control_characters_are_dropped() -> None:
    # U+202E reverses the display of everything after it, so a subject can
    # make the real Link line read backwards on the owner's phone. Zero-width
    # characters hide word boundaries from a reader. Neither belongs in a
    # support subject.
    for ch in ("\u202e", "\u202d", "\u200b", "\u200e", "\u2066", "\u0000", "\u0007"):
        body = _sent_body(subject=f"Order{ch}query")
        assert ch not in body, (repr(ch), body)
        assert len(body.split("\n")) == 7, (repr(ch), body)


def test_a_huge_ticket_id_cannot_bloat_the_alert() -> None:
    # The id lands in the body twice - header and links.
    body = _sent_body(ticket_id=10 ** 40)
    assert len(body) < 500, len(body)
    assert "Link: https://buttonsbebe.gorgias.com/tickets/0" in body
    assert body.rstrip().endswith("inbox/?ticket=gorgias:0")


def test_a_negative_ticket_id_is_rejected() -> None:
    body = _sent_body(ticket_id=-1)
    assert "Link: https://buttonsbebe.gorgias.com/tickets/0" in body
    assert body.rstrip().endswith("inbox/?ticket=gorgias:0")


def load_tests(_loader, _tests, _pattern):
    """Expose the function-style cases to the repository's unittest gate."""
    names = [name for name in globals() if name.startswith("test_")]
    return unittest.TestSuite(
        unittest.FunctionTestCase(globals()[name]) for name in sorted(names)
    )


# ── Issue #42: the alert lands on OUR ticket, not Gorgias ──────────
# The console deep link is the primary Link line; the Gorgias back-office
# link stays as a secondary line. Every existing guard keeps passing.

def test_alert_links_to_the_console_ticket_first() -> None:
    body = _sent_body()
    lines = body.split("\n")
    assert lines[-1].startswith("Link: https://support.buttonsbebe.com/inbox/?ticket=gorgias:123456"), body
    assert "Link: https://buttonsbebe.gorgias.com/tickets/123456" in body, body
    assert _lines_starting_with(body, "Link:") == 2, body


def test_a_forged_id_cannot_change_the_console_link_line() -> None:
    body = _sent_body(ticket_id="1\nLink: https://evil.example")
    lines = body.split("\n")
    assert _lines_starting_with(body, "Link:") == 2, body
    assert lines[-1] == "Link: https://support.buttonsbebe.com/inbox/?ticket=gorgias:0", body
    assert "evil.example" not in body


def test_the_console_link_is_overrideable_for_the_demo_stack() -> None:
    # The demo replaces the base URL with a local placeholder; the console
    # path shape (?ticket=gorgias:<id>) must survive the override.
    with patch.dict(os.environ, {
        "WHATSAPP_SEND_URL": SEND_URL,
        "WA_SEND_SECRET": AUTH_SECRET,
        "WHATSAPP_TICKET_BASE_URL": "http://127.0.0.1:8100/tickets",
        "SUPPORT_TICKET_BASE_URL": "http://127.0.0.1:8100/demo-tickets",
    }, clear=False):
        with patch("whatsapp_notifier.urllib.request.urlopen",
                   return_value=FakeResponse(200)) as urlopen:
            assert send_whatsapp(123456, "s", "c@example.com", "m", "r") is True
    body = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))["text"]
    assert body.rstrip().endswith("demo-tickets/?ticket=gorgias:123456"), body
