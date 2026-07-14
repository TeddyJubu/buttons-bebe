#!/usr/bin/env python3
"""
test_weekly_review.py — Stage 5, Task 16: weekly-review metrics tests.

Proves weekly_review.compute_metrics / format_report against KNOWN synthetic
data WITHOUT touching the real feedback.db and WITHOUT sending any Telegram
message. Two safety layers, mirroring test_telegram_notify.py:

  1. All DB work happens in a throwaway temp feedback.db (FEEDBACK_DB_PATH /
     a tempfile), seeded via feedback_db.record_*; the real feedback.db is
     never opened for writing.
  2. The only Telegram call (send_weekly_report) is made with dry_run=True, AND
     telegram_notify._send_to_chat is monkeypatched to FAIL the test if it is
     ever reached (the single place a real HTTP send happens). Belt-and-braces:
     even a non-dry-run bug trips the wire, not the owner.

Also asserts the REAL feedback.db row counts are unchanged across the whole run.

Run:  python3 test_weekly_review.py
Prints "WEEKLY_REVIEW TEST OK" on success (and a sample + empty-DB report).
"""

import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

import feedback_db
import telegram_notify
import weekly_review


# Real feedback.db (whatever FEEDBACK_DB_PATH/default resolves to at import).
_REAL_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "feedback.db"
)


def _row_counts(path):
    """(drafts, replies, comparisons) counts for a db, or (None,)*3 if absent."""
    if not os.path.exists(path):
        return (None, None, None)
    conn = sqlite3.connect(path)
    try:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("drafts", "replies", "comparisons")
        )
    finally:
        conn.close()


def _iso_days_ago(days):
    """ISO8601 UTC string for `days` ago — used to place rows in/out of window."""
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return dt.isoformat()


# Capture the REAL db counts ONCE at import, before any test runs.
_REAL_COUNTS_BEFORE = _row_counts(_REAL_DB_PATH)


class WeeklyReviewSeededTest(unittest.TestCase):
    """compute_metrics + format_report over a seeded temp db with KNOWN values."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="weekly_review_test_")
        cls.db_path = os.path.join(cls.tmpdir, "feedback_test.db")
        feedback_db.init_db(cls.db_path)
        cls._seed()

    @classmethod
    def _seed(cls):
        """Seed a known mix of drafts/replies/comparisons.

        IN-WINDOW (created 1-2 days ago):
          drafts: 1 drafted (posted), 1 escalated (dry-run), 1 kb_gap (dry-run)
          replies: 2
          comparisons: 2 with similarity 0.80 and 0.60 (avg 0.70),
                       one exact_match=1, response_time_sec 100 and 300.
        OUT-OF-WINDOW (created 30 days ago): 1 drafted draft — must be EXCLUDED.
        """
        p = cls.db_path

        # --- in-window drafts (mix of statuses, posted/dry-run, kb_gap) ----- #
        d_drafted = feedback_db.record_draft(
            ticket_id=1001, customer_message="where is my order?",
            draft_text="Your order is on its way!", priority="high",
            status="drafted", dry_run=0, posted_note_id=55501,  # POSTED
            created_at=_iso_days_ago(1), path=p,
        )
        d_escalated = feedback_db.record_draft(
            ticket_id=1002, customer_message="I want a refund now",
            draft_text="(escalated to a human)", priority="urgent",
            status="escalated", dry_run=1, posted_note_id=None,
            created_at=_iso_days_ago(1), path=p,
        )
        feedback_db.record_draft(
            ticket_id=1003, customer_message="do you restock floral romper 2T?",
            draft_text="(no KB answer)", priority="normal",
            status="kb_gap", kb_gap=1, dry_run=1, posted_note_id=None,
            created_at=_iso_days_ago(2), path=p,
        )

        # --- out-of-window draft (30 days ago) — MUST be excluded ---------- #
        feedback_db.record_draft(
            ticket_id=9999, customer_message="old ticket",
            draft_text="old draft", priority="low",
            status="drafted", dry_run=1,
            created_at=_iso_days_ago(30), path=p,
        )

        # --- in-window replies --------------------------------------------- #
        r1 = feedback_db.record_reply(
            ticket_id=1001, reply_text="Your order shipped, tracking: ...",
            message_id=70001, created_at=_iso_days_ago(1), path=p,
        )
        r2 = feedback_db.record_reply(
            ticket_id=1002, reply_text="(human took over the refund)",
            message_id=70002, created_at=_iso_days_ago(1), path=p,
        )

        # --- in-window comparisons (KNOWN similarity / exact / resp time) -- #
        # similarities 0.80 + 0.60 => avg 0.70; one exact_match; resp 100 + 300.
        feedback_db.record_comparison(
            ticket_id=1001, draft_id=d_drafted, reply_id=r1,
            similarity_score=0.80, exact_match=1, response_time_sec=100,
            created_at=_iso_days_ago(1), path=p,
        )
        feedback_db.record_comparison(
            ticket_id=1002, draft_id=d_escalated, reply_id=r2,
            similarity_score=0.60, exact_match=0, response_time_sec=300,
            created_at=_iso_days_ago(1), path=p,
        )

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(cls.db_path)
            os.rmdir(cls.tmpdir)
        except OSError:
            pass

    def test_draft_aggregates(self):
        m = weekly_review.compute_metrics(days=7, path=self.db_path)
        self.assertTrue(m["has_activity"])
        # 3 in-window drafts; the 30-day-old one is excluded.
        self.assertEqual(m["drafts_total"], 3)
        self.assertEqual(m["drafts_by_status"]["drafted"], 1)
        self.assertEqual(m["drafts_by_status"]["escalated"], 1)
        self.assertEqual(m["drafts_by_status"]["kb_gap"], 1)
        self.assertEqual(m["drafts_posted"], 1)     # only the posted_note_id one
        self.assertEqual(m["drafts_dry_run"], 2)
        self.assertEqual(m["kb_gaps"], 1)
        self.assertEqual(m["escalations"], 1)
        self.assertAlmostEqual(m["escalation_rate"], 1 / 3, places=6)

    def test_window_excludes_old_rows(self):
        # A 1-day window still sees the 1-2-day rows? No — tighten to 0? Use 3.
        m3 = weekly_review.compute_metrics(days=3, path=self.db_path)
        self.assertEqual(m3["drafts_total"], 3)   # all in-window rows are <=2d
        # A 60-day window now includes the 30-day-old draft too (4 total).
        m60 = weekly_review.compute_metrics(days=60, path=self.db_path)
        self.assertEqual(m60["drafts_total"], 4)

    def test_top_priorities(self):
        m = weekly_review.compute_metrics(days=7, path=self.db_path)
        prios = dict(m["top_priorities"])
        self.assertEqual(prios.get("high"), 1)
        self.assertEqual(prios.get("urgent"), 1)
        self.assertEqual(prios.get("normal"), 1)

    def test_learning_loop_aggregates(self):
        m = weekly_review.compute_metrics(days=7, path=self.db_path)
        self.assertEqual(m["replies_total"], 2)
        self.assertEqual(m["comparisons_total"], 2)
        # avg of 0.80 and 0.60 == 0.70 (the load-bearing assertion).
        self.assertAlmostEqual(m["avg_similarity"], 0.70, places=6)
        self.assertEqual(m["exact_matches"], 1)
        self.assertAlmostEqual(m["exact_match_rate"], 0.5, places=6)
        # response times 100 + 300 -> avg 200, median 200.
        self.assertAlmostEqual(m["avg_response_time_sec"], 200.0, places=6)
        self.assertAlmostEqual(m["median_response_time_sec"], 200.0, places=6)

    def test_format_report_renders_metrics(self):
        m = weekly_review.compute_metrics(days=7, path=self.db_path)
        report = weekly_review.format_report(m)
        # Telegram-safe length.
        self.assertLess(len(report), 4096)
        # Key numbers appear.
        self.assertIn("Total: 3", report)
        self.assertIn("drafted=1", report)
        self.assertIn("escalated=1", report)
        self.assertIn("kb_gap=1", report)
        self.assertIn("0.700", report)          # avg similarity
        self.assertIn("KB gaps: 1", report)
        # Save for the human-readable print at the end.
        type(self)._sample_report = report

    def test_send_weekly_report_dry_run_does_not_send(self):
        """send_weekly_report(dry_run=True) must build a payload and send NOTHING."""
        m = weekly_review.compute_metrics(days=7, path=self.db_path)
        report = weekly_review.format_report(m)

        # Trip-wire: if a REAL HTTP send is attempted, fail loudly.
        original = telegram_notify._send_to_chat

        def _boom(*_a, **_k):
            raise AssertionError(
                "telegram_notify._send_to_chat was called — a REAL Telegram "
                "send was attempted during a dry-run test!"
            )

        telegram_notify._send_to_chat = _boom
        try:
            result = telegram_notify.send_weekly_report(report, dry_run=True)
        finally:
            telegram_notify._send_to_chat = original

        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        # The rendered report text is carried in the payload.
        self.assertIn("WEEKLY REPORT", result["text"])
        self.assertIn("Total: 3", result["text"])


class WeeklyReviewEmptyDbTest(unittest.TestCase):
    """The empty-DB path must NOT crash or divide by zero."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="weekly_review_empty_")
        self.db_path = os.path.join(self.tmpdir, "feedback_empty.db")
        feedback_db.init_db(self.db_path)  # schema, zero rows

    def tearDown(self):
        try:
            os.remove(self.db_path)
            os.rmdir(self.tmpdir)
        except OSError:
            pass

    def test_empty_db_metrics(self):
        m = weekly_review.compute_metrics(days=7, path=self.db_path)
        self.assertFalse(m["has_activity"])
        self.assertEqual(m["drafts_total"], 0)
        self.assertEqual(m["replies_total"], 0)
        self.assertEqual(m["comparisons_total"], 0)
        self.assertIsNone(m["avg_similarity"])        # no div-by-zero
        self.assertEqual(m["escalation_rate"], 0.0)
        self.assertEqual(m["exact_match_rate"], 0.0)

    def test_empty_db_report_says_no_activity(self):
        m = weekly_review.compute_metrics(days=7, path=self.db_path)
        report = weekly_review.format_report(m)
        self.assertIn("No activity this week", report)
        self.assertLess(len(report), 4096)
        type(self)._empty_report = report


class RealDbUntouchedTest(unittest.TestCase):
    """The real feedback.db must be byte-count-identical before and after."""

    def test_real_db_row_counts_unchanged(self):
        after = _row_counts(_REAL_DB_PATH)
        self.assertEqual(
            _REAL_COUNTS_BEFORE, after,
            f"real feedback.db row counts changed: {_REAL_COUNTS_BEFORE} -> {after}",
        )


def _run():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    # Order: seeded -> empty -> real-db-untouched (so the final check runs last).
    suite.addTests(loader.loadTestsFromTestCase(WeeklyReviewSeededTest))
    suite.addTests(loader.loadTestsFromTestCase(WeeklyReviewEmptyDbTest))
    suite.addTests(loader.loadTestsFromTestCase(RealDbUntouchedTest))
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    if result.wasSuccessful():
        sample = getattr(WeeklyReviewSeededTest, "_sample_report", None)
        empty = getattr(WeeklyReviewEmptyDbTest, "_empty_report", None)
        if sample:
            print("\n----- SAMPLE RENDERED REPORT (seeded temp db) -----")
            print(sample)
        if empty:
            print("\n----- EMPTY-DB REPORT -----")
            print(empty)
        print(f"\nReal feedback.db row counts (drafts,replies,comparisons): "
              f"{_REAL_COUNTS_BEFORE} (unchanged)")
        print("\nWEEKLY_REVIEW TEST OK")
        return 0
    print("\nWEEKLY_REVIEW TEST FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(_run())
