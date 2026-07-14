#!/usr/bin/env python3
"""
test_telegram_notify.py — Stage 5, Task 15: owner-alert sender tests.

Proves the Task-15 owner alerts (escalation / KB-gap / weekly report) without
EVER sending a real message to the owner's live Telegram chat. Telegram is LIVE
in this project (real telegram_bot_token + telegram_chat_ids in config.json), so
this test uses TWO independent safety layers:

  1. dry_run=True on every call — _send returns the built payload and makes NO
     network call and NO config-dependent send.
  2. A monkeypatched telegram_notify._send_to_chat that FAILS the test if it is
     ever reached (the only place an HTTP request to Telegram is made). This is
     belt-and-braces: even a non-dry-run bug would trip the wire, not the owner.

Properties proven:
  * Each function targets the OWNER chat(s) only and never a customer.
  * The right text/prefix/snippet/deep-link is built for each alert type.
  * Long input is truncated to Telegram's ~4096-char limit.
  * A simulated network failure never raises out of the sender (resilient).

Run:  python3 test_telegram_notify.py     (stdlib unittest; prints OK on success)
"""

import json
import os
import unittest

import telegram_notify


# Read the real owner chat ids from config so we can assert "owner-only".
def _config_owner_chat_ids():
    cfg_path = telegram_notify.CONFIG_PATH
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return []
    ids = cfg.get("telegram_chat_ids") or []
    if not ids and cfg.get("telegram_chat_id") is not None:
        ids = [cfg["telegram_chat_id"]]
    return [int(x) for x in ids]


OWNER_CHAT_IDS = _config_owner_chat_ids()


class SendToChatTripWire(AssertionError):
    """Raised if a real Telegram HTTP send is ever attempted in the tests."""


class TelegramOwnerAlertTests(unittest.TestCase):

    def setUp(self):
        # TRIP-WIRE: the only place that issues a Telegram HTTP request is
        # _send_to_chat. Replace it so any LIVE send fails the test loudly
        # instead of messaging the owner's real chat.
        self._orig_send_to_chat = telegram_notify._send_to_chat

        def _no_live_send(*_a, **_k):
            raise SendToChatTripWire(
                "SAFETY VIOLATION: a real Telegram send was attempted in tests."
            )

        telegram_notify._send_to_chat = _no_live_send

    def tearDown(self):
        telegram_notify._send_to_chat = self._orig_send_to_chat

    # -- helpers ------------------------------------------------------------- #
    def _assert_owner_only(self, result):
        """Every built payload must target a configured OWNER chat id — and nothing else."""
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(OWNER_CHAT_IDS,
                        "OWNER_CHAT_IDS is empty — cannot verify owner-only constraint. "
                        "Ensure config.json has telegram_chat_ids configured.")
        self.assertIsInstance(result["results"], list)
        self.assertGreater(len(result["results"]), 0, "dry-run must build at least one payload")
        for r in result["results"]:
            self.assertTrue(r["dry_run"])
            chat_id = r["payload"]["chat_id"]
            self.assertEqual(chat_id, r["chat_id"])
            self.assertIn(
                chat_id, OWNER_CHAT_IDS,
                f"alert targeted {chat_id!r}, not a configured OWNER chat id",
            )
            # The payload must carry text and a link-preview flag — and never a
            # 'sender'/'receiver'/'email' (those would be a customer-message path).
            payload_str = json.dumps(r["payload"]).lower()
            for forbidden in ("receiver", "sender", "smsintegration", "@customer"):
                self.assertNotIn(forbidden, payload_str)

    def _payload_text(self, result):
        return result["results"][0]["payload"]["text"]

    # -- escalation alert ---------------------------------------------------- #
    def test_escalation_alert_builds_owner_payload(self):
        res = telegram_notify.send_escalation_alert(
            55123, category="refund", priority="urgent",
            reason="customer wants a refund + mentioned chargeback",
            customer_message="hi, I want a refund and I'm calling my bank",
            dry_run=True,
        )
        self._assert_owner_only(res)
        text = self._payload_text(res)
        self.assertIn("ESCALATION", text)
        self.assertIn("#55123", text)
        self.assertIn("refund", text)
        self.assertIn("urgent", text)
        self.assertIn("chargeback", text)        # the reason is included
        self.assertIn("calling my bank", text)   # the snippet is included
        self.assertIn("/55123", text)            # the ticket deep-link
        print("OK escalation: owner-only payload with id/category/priority/reason/snippet/link.")

    def test_escalation_alert_without_message(self):
        # No snippet supplied — still builds cleanly, owner-only.
        res = telegram_notify.send_escalation_alert(
            7, category="legal", priority="high", reason="legal threat",
            dry_run=True,
        )
        self._assert_owner_only(res)
        text = self._payload_text(res)
        self.assertIn("ESCALATION", text)
        self.assertIn("#7", text)
        self.assertNotIn("Customer said", text)
        print("OK escalation w/o message: builds without a snippet line.")

    # -- KB-gap question ----------------------------------------------------- #
    def test_kb_gap_question_builds_owner_payload(self):
        res = telegram_notify.send_kb_gap_question(
            88990, customer_message="do you restock the floral romper in 2T?",
            dry_run=True,
        )
        self._assert_owner_only(res)
        text = self._payload_text(res)
        self.assertIn("KB GAP", text)
        self.assertIn("#88990", text)
        self.assertIn("How should I answer", text)
        self.assertIn("floral romper", text)
        self.assertIn("/88990", text)
        print("OK kb-gap: owner-only 'how should I answer?' ask with the question + link.")

    # -- weekly report ------------------------------------------------------- #
    def test_weekly_report_from_dict(self):
        res = telegram_notify.send_weekly_report(
            {"tickets": 42, "escalations": 3, "kb_gaps": 5,
             "by_category": {"refund": 3, "shipping": 10}},
            dry_run=True,
        )
        self._assert_owner_only(res)
        text = self._payload_text(res)
        self.assertIn("WEEKLY REPORT", text)
        self.assertIn("tickets: 42", text)
        self.assertIn("escalations: 3", text)
        self.assertIn("refund: 3", text)         # nested dict rendered
        print("OK weekly (dict): owner-only formatted metrics block.")

    def test_weekly_report_from_string(self):
        res = telegram_notify.send_weekly_report(
            "42 tickets, 3 escalations, 5 KB gaps this week.", dry_run=True)
        self._assert_owner_only(res)
        text = self._payload_text(res)
        self.assertIn("WEEKLY REPORT", text)
        self.assertIn("42 tickets", text)
        print("OK weekly (str): owner-only, pre-formatted text passed through.")

    # -- truncation ---------------------------------------------------------- #
    def test_long_input_is_truncated_to_telegram_limit(self):
        huge = "x" * 50000
        for res in (
            telegram_notify.send_escalation_alert(
                1, category="c", priority="p", reason="r",
                customer_message=huge, dry_run=True),
            telegram_notify.send_kb_gap_question(
                1, customer_message=huge, dry_run=True),
            telegram_notify.send_weekly_report(huge, dry_run=True),
        ):
            text = self._payload_text(res)
            # Hard Telegram ceiling is 4096; our code truncates at _TELEGRAM_SAFE_CHARS
            # (4000) so a 50k-char input should produce ≤ 4096 chars of output.
            self.assertLessEqual(
                len(text), telegram_notify.TELEGRAM_MAX_CHARS,
                f"alert text must be ≤ 4096 chars, got {len(text)}",
            )
            # Also verify we're well under the hard limit (not just lucky):
            self.assertLess(
                len(text), telegram_notify.TELEGRAM_MAX_CHARS,
                f"alert text should be < 4096 (truncated at safe limit {telegram_notify._TELEGRAM_SAFE_CHARS}), got {len(text)}",
            )
        print("OK truncation: 50k-char input clamped under the 4096 Telegram limit.")

    # -- resilience: a network failure must never raise out of the sender ---- #
    def test_live_send_network_failure_never_raises(self):
        # Make the real HTTP layer raise, then call a LIVE (non-dry-run) send.
        # The sender must catch it, log, and return ok=False — never raise.
        # We also monkeypatch _load_telegram_config so the code always reaches
        # _send_to_chat regardless of whether config.json is present.
        orig_cfg = telegram_notify._load_telegram_config

        def _fake_cfg():
            return ("fake-token", [123456])

        def _boom(*_a, **_k):
            raise OSError("simulated network failure")

        telegram_notify._load_telegram_config = _fake_cfg
        telegram_notify._send_to_chat = _boom
        try:
            res = telegram_notify.send_escalation_alert(
                42, category="refund", priority="high", reason="boom",
                customer_message="will this crash?", dry_run=False,
            )
        except Exception as e:  # pragma: no cover - would be the bug we guard
            self.fail(f"sender leaked an exception on network failure: {e!r}")
        finally:
            telegram_notify._load_telegram_config = orig_cfg
        self.assertFalse(res["ok"], "a failed live send must report ok=False")
        self.assertEqual(res["dry_run"], False)
        self.assertIn("simulated network failure", str(res),
                      "error result must contain evidence of the OSError that caused it")
        print("OK resilience: a simulated network failure is caught — sender never raises.")

    def test_live_send_missing_config_never_raises(self):
        # If _load_telegram_config raises (e.g. missing token), a LIVE send must
        # still degrade gracefully, not crash.
        orig_cfg = telegram_notify._load_telegram_config

        def _bad_cfg():
            raise ValueError("simulated missing telegram config")

        telegram_notify._load_telegram_config = _bad_cfg
        try:
            res = telegram_notify.send_kb_gap_question(
                9, customer_message="q?", dry_run=False)
        except Exception as e:  # pragma: no cover
            self.fail(f"sender leaked an exception on bad config: {e!r}")
        finally:
            telegram_notify._load_telegram_config = orig_cfg
        self.assertFalse(res["ok"])
        print("OK resilience: missing config on a live send is caught — sender never raises.")

    # -- meta: confirm the trip-wire is actually armed ----------------------- #
    def test_tripwire_is_armed(self):
        with self.assertRaises(SendToChatTripWire):
            telegram_notify._send_to_chat("tok", 1, "hi")
        print("META OK: the live-send trip-wire is armed and fires.")


if __name__ == "__main__":
    import sys
    print("=" * 64)
    print("TELEGRAM OWNER-ALERT TEST (Stage 5, Task 15)")
    print(f"  owner chat ids (from config): {OWNER_CHAT_IDS or '(none configured)'}")
    print("  mode: dry_run=True + live-send trip-wire (NO message sent to owner)")
    print("=" * 64)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TelegramOwnerAlertTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    if result.wasSuccessful():
        print("\nTELEGRAM OWNER-ALERT TEST OK")
        sys.exit(0)
    else:
        print("\nTELEGRAM OWNER-ALERT TEST FAILED")
        sys.exit(1)
