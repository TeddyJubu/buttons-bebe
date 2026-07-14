"""Offline unit tests for the feedback loop. No network. Run:

    python3 -m unittest feedback.tests.test_all -v

Covers the paths the adversary review flagged: draft/reply pairing, macro skip,
multi-turn skip, empty/no-draft skips, non-English handling, PII highlighting,
similarity-as-hint, sensitive-ticket guard, and end-to-end capture + dedupe.
"""
from __future__ import annotations

import tempfile
import unittest
import pathlib

from feedback import config, pairing, pii, similarity, text_clean, collector, store


def msg(from_agent, public, text, ts, email="", mid=1, **extra):
    m = {
        "id": mid,
        "from_agent": from_agent,
        "public": public,
        "stripped_text": text,
        "created_datetime": ts,
        "sender": {"email": email, "id": str(mid)},
    }
    m.update(extra)
    return m


def customer(text, ts, **kw):
    return msg(False, True, text, ts, email="cust@example.com", **kw)


def draft(text, ts, **kw):
    return msg(True, False, text, ts, email="bot@buttonsbebe.com", **kw)


def human(text, ts, **kw):
    return msg(True, True, text, ts, email="agent@buttonsbebe.com", **kw)


class TmpEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        config.LEARNED_DIR = root / "learned"
        config.TICKETS_DIR = root / "tickets"
        config.ARCHIVE_DIR = root / "_archive_learned"
        config.STATE_DB = root / "state.db"
        config.KB_ROOT = root
        config.AGENT_BOT_EMAIL = "bot@buttonsbebe.com"
        config.CAPTURE_MULTI_TURN = False
        config.MACRO_SIGNATURES_FILE = root / "macros.txt"

    def tearDown(self):
        self.tmp.cleanup()


class TestPairing(TmpEnv):
    def test_clean_single_exchange(self):
        msgs = [
            customer("Do you ship to Canada?", "2026-07-01T10:00:00Z"),
            draft("Yes, we ship to Canada.", "2026-07-01T10:05:00Z"),
            human("Hi! Yes, we do ship to Canada. Shipping is calculated at checkout.",
                  "2026-07-01T10:20:00Z"),
        ]
        r = pairing.evaluate(101, msgs)
        self.assertIsInstance(r, pairing.Pair)
        self.assertFalse(r.multi_turn)
        self.assertIn("Canada", r.human_reply_text)

    def test_no_ai_draft(self):
        msgs = [customer("hi", "2026-07-01T10:00:00Z"),
                human("hello", "2026-07-01T10:10:00Z")]
        r = pairing.evaluate(102, msgs)
        self.assertIsInstance(r, pairing.Skip)
        self.assertEqual(r.reason, "no_ai_draft")

    def test_no_human_reply(self):
        msgs = [customer("hi", "2026-07-01T10:00:00Z"),
                draft("draft here", "2026-07-01T10:05:00Z")]
        r = pairing.evaluate(103, msgs)
        self.assertEqual(r.reason, "no_human_reply")

    def test_empty_reply(self):
        msgs = [customer("hi", "2026-07-01T10:00:00Z"),
                draft("draft here", "2026-07-01T10:05:00Z"),
                human("   ", "2026-07-01T10:10:00Z")]
        r = pairing.evaluate(104, msgs)
        self.assertEqual(r.reason, "empty_reply")

    def test_macro_metadata(self):
        msgs = [customer("hi", "2026-07-01T10:00:00Z"),
                draft("draft", "2026-07-01T10:05:00Z"),
                human("Thanks for reaching out!", "2026-07-01T10:10:00Z", rule_ids=[7])]
        r = pairing.evaluate(105, msgs)
        self.assertEqual(r.reason, "macro")

    def test_macro_signature_file(self):
        config.MACRO_SIGNATURES_FILE.write_text("our standard return policy\n")
        msgs = [customer("hi", "2026-07-01T10:00:00Z"),
                draft("draft", "2026-07-01T10:05:00Z"),
                human("Our standard return policy is 30 days.", "2026-07-01T10:10:00Z")]
        r = pairing.evaluate(106, msgs)
        self.assertEqual(r.reason, "macro")

    def test_multi_turn_skipped(self):
        msgs = [
            customer("q1", "2026-07-01T10:00:00Z"),
            draft("d1", "2026-07-01T10:05:00Z"),
            human("a1 long enough to be real content here", "2026-07-01T10:10:00Z"),
            customer("q2 follow up", "2026-07-01T11:00:00Z"),
            human("a2 second reply also content", "2026-07-01T11:10:00Z"),
        ]
        r = pairing.evaluate(107, msgs)
        self.assertEqual(r.reason, "multi_turn")

    def test_hebrew_reply_captures_but_band_na(self):
        msgs = [
            customer("שלום, האם אתם שולחים לישראל?", "2026-07-01T10:00:00Z"),
            draft("Yes we ship to Israel.", "2026-07-01T10:05:00Z"),
            human("שלום! כן, אנחנו שולחים לישראל. המשלוח מחושב בקופה.",
                  "2026-07-01T10:20:00Z"),
        ]
        r = pairing.evaluate(108, msgs)
        self.assertIsInstance(r, pairing.Pair)
        hint = similarity.compare(r.ai_draft_clean, r.human_reply_text)
        self.assertEqual(hint["band"], "n/a")
        self.assertFalse(hint["reliable"])
        self.assertEqual(hint["reply_language"], "he")


class TestTextClean(unittest.TestCase):
    def test_strip_glm_tail(self):
        raw = "Yes, we ship to Canada.\n\nThe response above was complete and accurate."
        self.assertNotIn("response above", text_clean.clean_draft(raw).lower())
        self.assertIn("Canada", text_clean.clean_draft(raw))

    def test_dedupe_repeated_block(self):
        one = "Hi there, your order ships in 2 days and tracking will follow shortly."
        raw = one + "\n\n" + one
        cleaned = text_clean.clean_draft(raw)
        self.assertEqual(cleaned.lower().count("your order ships"), 1)


class TestPII(unittest.TestCase):
    def test_finds_and_masks(self):
        t = "Email me at jane@doe.com about order #123456, call 415-555-1212."
        s = pii.summary(t)
        kinds = s["by_kind"]
        self.assertIn("email", kinds)
        self.assertIn("order_hash", kinds)
        self.assertIn("phone", kinds)
        masked = pii.mask(t)
        self.assertNotIn("jane@doe.com", masked)
        self.assertNotIn("123456", masked)

    def test_warns_about_names(self):
        self.assertIn("names", pii.summary("hi")["warning"].lower())

    def test_masks_unknown_name_in_customer_greeting(self):
        self.assertEqual(pii.mask("Hi Marjana, sure thing!"), "Hi [name], sure thing!")
        self.assertEqual(pii.mask("Hi there, welcome!"), "Hi there, welcome!")


class TestSimilarity(unittest.TestCase):
    def test_identical_is_close(self):
        txt = "Yes, we ship to Canada and shipping is calculated at checkout time."
        self.assertEqual(similarity.compare(txt, txt)["band"], "close")

    def test_different_english_is_rewrite(self):
        a = "Yes, we ship to Canada and shipping is calculated at checkout time here."
        b = "Our return window is thirty days from the delivery date for all items sold."
        self.assertEqual(similarity.compare(a, b)["band"], "rewrite")

    def test_short_is_na(self):
        self.assertEqual(similarity.compare("ok", "sure")["band"], "n/a")


class TestCollectorEndToEnd(TmpEnv):
    def _clean_ticket(self, tid=201):
        return [
            customer("Do you ship to Canada?", "2026-07-01T10:00:00Z"),
            draft("Yes, we ship to Canada.", "2026-07-01T10:05:00Z"),
            human("Hi! Yes, we ship to Canada — shipping shows at checkout.",
                  "2026-07-01T10:20:00Z"),
        ], tid

    def test_capture_writes_packet_and_ledger(self):
        msgs, tid = self._clean_ticket()
        out = collector.process_ticket(tid, msgs)
        self.assertEqual(out["outcome"], "captured")
        packet = config.LEARNED_DIR / f"ticket-{tid}.md"
        self.assertTrue(packet.exists())
        body = packet.read_text()
        self.assertIn("review_pending: true", body)
        self.assertIn("## Reviewer checklist", body)
        # ledger records it and blocks reprocessing
        self.assertTrue(store.already_processed(tid))
        out2 = collector.process_ticket(tid, msgs)
        self.assertEqual(out2["reason"], "already_processed")

    def test_sensitive_ticket_skipped(self):
        msgs, tid = self._clean_ticket(202)
        ticket = {"id": tid, "tags": [{"name": "refund"}], "subject": "refund please"}
        out = collector.process_ticket(tid, msgs, ticket=ticket)
        self.assertEqual(out["outcome"], "skipped")
        self.assertEqual(out["reason"], "sensitive")


if __name__ == "__main__":
    unittest.main(verbosity=2)
