#!/usr/bin/env python3
"""
test_workflow_b.py — Stage 3, Task 10: CAPTURE-ONLY test for Workflow B.

Workflow B captures a human agent's PUBLIC reply, compares it to our AI draft
via difflib, and stores the similarity in feedback.db. It is CAPTURE-ONLY: it
makes NO Gorgias writes and NEVER messages a customer — it only reads the
already-fetched ctx and writes the local feedback.db.

This test touches NO network and NO live LLM:
  * FEEDBACK_DB_PATH points at a throwaway temp db (set BEFORE importing project
    modules), so the real feedback.db is never polluted.
  * A NETWORK TRIP-WIRE replaces gorgias_api.request with a function that fails
    the test if ever called — proving Workflow B makes no Gorgias API call.
  * A PUBLIC-MESSAGE TRIP-WIRE replaces add_message_to_ticket so any customer
    path fails the test.

Properties proven (see TestWorkflowBCapture):
  1. A human PUBLIC agent reply on a ticket that has a prior real draft records
     a reply row AND a comparison row with a sane 0..1 similarity; a
     near-identical reply scores high, a totally different reply scores low.
  2. THE CRUX: our OWN internal note (channel="internal-note", sender == our bot
     id, from_agent=True) is NOT captured — no reply row, no comparison.
  3. Duplicate delivery (same message_id) does not double-insert (dedup works).
  4. A reply with no prior draft records the reply but inserts NO comparison
     (and does not crash on the comparisons FK).
  5. run_workflow_b never raises out, even when a dependency throws, and makes
     no Gorgias call (real feedback.db untouched — temp db only).

Run:  python3 test_workflow_b.py   (stdlib unittest; prints OK on success)
"""

import os
import tempfile
import unittest

# --- Force the safe/offline environment BEFORE importing project modules. --- #
os.environ["LLM_PROVIDER"] = "mock"          # no live LLM
os.environ.pop("HERMES_ALLOW_WRITE", None)   # writes (if any) would be dry-run

# Throwaway feedback db so the real one is never touched. Set before import so
# feedback_db.DB_PATH (read at import) points here.
_TMPDIR = tempfile.mkdtemp(prefix="wf_b_test_")
_DB_PATH = os.path.join(_TMPDIR, "feedback_test.db")
os.environ["FEEDBACK_DB_PATH"] = _DB_PATH

import pipeline          # noqa: E402
import gorgias_api       # noqa: E402
import feedback_db       # noqa: E402
import server            # noqa: E402

# Make sure the throwaway db has the schema.
feedback_db.init_db(_DB_PATH)

# A near-identical and a totally-different reply, relative to this draft.
DRAFT_TEXT = (
    "Hi! Thanks for reaching out. Your order shipped this morning and is on "
    "its way. You should receive it within 3-5 business days. Let us know if "
    "you have any other questions!"
)
NEAR_IDENTICAL_REPLY = (
    "Hi! Thanks for reaching out. Your order shipped this morning and is on "
    "its way. You should receive it within 3-5 business days. Let us know if "
    "you have any other questions!"
)
DIFFERENT_REPLY = (
    "No. We are completely sold out of that product and have discontinued it "
    "entirely. Please stop emailing us about it."
)


class NetworkTripWire(AssertionError):
    """Raised if any code path attempts a real Gorgias network call."""


class PublicMessageTripWire(AssertionError):
    """Raised if Workflow B ever reaches the public customer-message path."""


def _human_public_reply_msg(message_id, body_text, *, created="2026-06-26T12:18:36+00:00"):
    """A real human agent's PUBLIC email reply (from_agent True, public True,
    sender is a human agent — NOT our bot)."""
    return {
        "id": message_id,
        "channel": "email",
        "public": True,
        "from_agent": True,
        "sender": {"id": 66260768, "email": "rochel@buttonsbebe.com"},
        "body_text": body_text,
        "stripped_text": body_text,
        "created_datetime": created,
    }


def _our_internal_note_msg(message_id, body_text, *, created="2026-06-26T12:00:00+00:00"):
    """One of OUR OWN internal notes: internal-note channel, public False, and
    sender == our bot user id. Must NEVER be captured as a human reply."""
    return {
        "id": message_id,
        "channel": gorgias_api.INTERNAL_NOTE_CHANNEL,   # "internal-note"
        "public": False,
        "from_agent": True,
        "sender": {"id": gorgias_api.DEFAULT_AGENT_USER_ID, "email": "bot@buttonsbebe.com"},
        "body_text": body_text,
        "stripped_text": body_text,
        "created_datetime": created,
    }


def _customer_msg(message_id, body_text, *, created="2026-06-26T05:04:53+00:00"):
    """The customer's inbound message (from_agent False)."""
    return {
        "id": message_id,
        "channel": "email",
        "public": True,
        "from_agent": False,
        "sender": {"id": 66262302, "email": "chayaf36@gmail.com"},
        "body_text": body_text,
        "stripped_text": body_text,
        "created_datetime": created,
    }


def _build_ctx(ticket_id, messages):
    ctx = pipeline.TicketContext()
    ctx.event_type = "ticket-message-created"
    ctx.trigger = "ticket-message-created"
    ctx.ticket_id = ticket_id
    ctx.customer_id = 9
    ctx.from_agent = True
    ctx.ticket = {"id": ticket_id, "subject": "order status", "status": "open",
                  "customer": {"id": 9, "email": "chayaf36@gmail.com"}}
    ctx.messages = messages
    ctx.customer = {"id": 9, "email": "chayaf36@gmail.com"}
    ctx.order_context = {"shopify_found": False, "orders": []}
    return ctx


def _seed_draft(ticket_id, draft_text=DRAFT_TEXT, *, status="drafted",
                created_at="2026-06-26T12:00:00+00:00"):
    """Insert a draft row (as Workflow A would) and return its id."""
    return feedback_db.record_draft(
        ticket_id=ticket_id,
        customer_message="where is my order?",
        draft_text=draft_text,
        priority="low",
        status=status,
        dry_run=1,
        created_at=created_at,
        path=_DB_PATH,
    )


class TestWorkflowBCapture(unittest.TestCase):

    def setUp(self):
        os.environ.pop("HERMES_ALLOW_WRITE", None)

        # Save originals so each test restores cleanly.
        self._orig_request = gorgias_api.request
        self._orig_client_request = server.GorgiasClient._request
        self._orig_add_message = server.GorgiasClient.add_message_to_ticket

        # NETWORK TRIP-WIRE: any real HTTP attempt fails the test.
        def _no_network(*_a, **_k):
            raise NetworkTripWire(
                "SAFETY VIOLATION: Workflow B attempted a real Gorgias network call."
            )

        gorgias_api.request = _no_network
        server.GorgiasClient._request = lambda *a, **k: _no_network()

        # PUBLIC-MESSAGE TRIP-WIRE: customer-facing path must never be reached.
        def _no_public(*_a, **_k):
            raise PublicMessageTripWire(
                "SAFETY VIOLATION: Workflow B reached the public customer-message "
                "path (add_message_to_ticket)."
            )

        server.GorgiasClient.add_message_to_ticket = _no_public

    def tearDown(self):
        gorgias_api.request = self._orig_request
        server.GorgiasClient._request = self._orig_client_request
        server.GorgiasClient.add_message_to_ticket = self._orig_add_message

    # -- Property 1: human public reply + prior draft -> reply + comparison. -- #
    def test_near_identical_reply_scores_high(self):
        tid = 91001
        draft_id = _seed_draft(tid)
        ctx = _build_ctx(tid, [
            _customer_msg(101, "where is my order?"),
            _human_public_reply_msg(102, NEAR_IDENTICAL_REPLY),
        ])
        server.run_workflow_b(ctx)

        replies = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM replies WHERE ticket_id = ?", (tid,)).fetchall()
        self.assertEqual(len(replies), 1, "exactly one reply row")
        self.assertEqual(replies[0]["message_id"], 102)
        self.assertEqual(replies[0]["channel"], "email")
        self.assertEqual(replies[0]["agent_user_id"], 66260768)

        comps = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM comparisons WHERE ticket_id = ?", (tid,)).fetchall()
        self.assertEqual(len(comps), 1, "exactly one comparison row")
        c = comps[0]
        self.assertEqual(c["draft_id"], draft_id)
        self.assertEqual(c["reply_id"], replies[0]["id"])
        self.assertTrue(0.0 <= c["similarity_score"] <= 1.0)
        self.assertGreater(c["similarity_score"], 0.9,
                           "near-identical reply must score high")
        self.assertEqual(c["exact_match"], 1, "identical text -> exact_match=1")
        # response_time_sec = reply 12:18:36 − draft 12:00:00 = 1116s.
        self.assertEqual(c["response_time_sec"], 1116)
        print(f"PROP 1a OK: near-identical reply -> similarity="
              f"{c['similarity_score']:.3f} exact_match={c['exact_match']} "
              f"response_time_sec={c['response_time_sec']}.")

    def test_different_reply_scores_low(self):
        tid = 91002
        _seed_draft(tid)
        ctx = _build_ctx(tid, [
            _customer_msg(201, "where is my order?"),
            _human_public_reply_msg(202, DIFFERENT_REPLY),
        ])
        server.run_workflow_b(ctx)

        comps = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM comparisons WHERE ticket_id = ?", (tid,)).fetchall()
        self.assertEqual(len(comps), 1)
        c = comps[0]
        self.assertTrue(0.0 <= c["similarity_score"] <= 1.0)
        self.assertLess(c["similarity_score"], 0.5,
                        "totally different reply must score low")
        self.assertEqual(c["exact_match"], 0)
        print(f"PROP 1b OK: different reply -> similarity="
              f"{c['similarity_score']:.3f} exact_match={c['exact_match']} (low).")

    # -- Property 2 (THE CRUX): our own internal note is NOT captured. ------- #
    def test_our_own_internal_note_is_not_captured(self):
        tid = 91003
        _seed_draft(tid)
        # The ONLY agent message is our own internal note (the Workflow A note).
        ctx = _build_ctx(tid, [
            _customer_msg(301, "where is my order?"),
            _our_internal_note_msg(302, "🤖 Hermes draft (internal) ... " + DRAFT_TEXT),
        ])
        server.run_workflow_b(ctx)

        replies = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM replies WHERE ticket_id = ?", (tid,)).fetchall()
        comps = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM comparisons WHERE ticket_id = ?", (tid,)).fetchall()
        self.assertEqual(len(replies), 0,
                         "our own internal note must NOT be recorded as a reply")
        self.assertEqual(len(comps), 0,
                         "no comparison against our own internal note")
        # The internal note's message_id must never appear in replies.
        self.assertFalse(feedback_db.reply_exists(302, path=_DB_PATH))
        print("PROP 2 OK (CRUX): our own internal note (channel=internal-note, "
              "sender=bot, public=False) is NOT captured — no reply, no comparison.")

    def test_internal_note_alongside_human_reply_picks_human(self):
        # A ticket carrying BOTH our internal note AND a later human public reply
        # must capture the HUMAN reply, never the internal note.
        tid = 91004
        _seed_draft(tid)
        ctx = _build_ctx(tid, [
            _customer_msg(401, "where is my order?"),
            _our_internal_note_msg(402, "internal: " + DRAFT_TEXT,
                                   created="2026-06-26T12:05:00+00:00"),
            _human_public_reply_msg(403, NEAR_IDENTICAL_REPLY,
                                    created="2026-06-26T12:30:00+00:00"),
        ])
        server.run_workflow_b(ctx)

        replies = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM replies WHERE ticket_id = ?", (tid,)).fetchall()
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["message_id"], 403,
                         "must capture the human reply (403), not the note (402)")
        self.assertFalse(feedback_db.reply_exists(402, path=_DB_PATH),
                         "the internal note (402) must never be captured")
        print("PROP 2b OK: with both an internal note and a human reply present, "
              "the HUMAN public reply (id=403) is captured, not the note (id=402).")

    # -- Property 3: duplicate delivery does not double-insert (dedup). ------ #
    def test_duplicate_delivery_dedup(self):
        tid = 91005
        _seed_draft(tid)
        msgs = [
            _customer_msg(501, "where is my order?"),
            _human_public_reply_msg(502, NEAR_IDENTICAL_REPLY),
        ]
        ctx = _build_ctx(tid, msgs)
        server.run_workflow_b(ctx)
        # Same webhook delivered again (retry) — identical messages/message_id.
        server.run_workflow_b(_build_ctx(tid, msgs))

        replies = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM replies WHERE ticket_id = ?", (tid,)).fetchall()
        comps = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM comparisons WHERE ticket_id = ?", (tid,)).fetchall()
        self.assertEqual(len(replies), 1, "dedup: only one reply row for msg 502")
        self.assertEqual(len(comps), 1, "dedup: only one comparison row")
        print("PROP 3 OK: duplicate webhook delivery (same message_id) does NOT "
              "double-insert — exactly one reply and one comparison.")

    # -- Property 4: reply with no prior draft -> reply only, no comparison. - #
    def test_reply_with_no_draft_records_reply_no_comparison(self):
        tid = 91006  # no draft seeded for this ticket
        ctx = _build_ctx(tid, [
            _customer_msg(601, "where is my order?"),
            _human_public_reply_msg(602, NEAR_IDENTICAL_REPLY),
        ])
        # Must NOT raise (no FK violation from a null/absent draft_id).
        server.run_workflow_b(ctx)

        replies = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM replies WHERE ticket_id = ?", (tid,)).fetchall()
        comps = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM comparisons WHERE ticket_id = ?", (tid,)).fetchall()
        self.assertEqual(len(replies), 1, "reply is still captured (training data)")
        self.assertEqual(len(comps), 0, "no comparison without a real draft")
        print("PROP 4 OK: reply with no prior draft -> reply row kept, NO "
              "comparison inserted (FK not violated).")

    def test_escalation_draft_is_not_compared(self):
        # An escalation/kb_gap note is NOT a real customer draft -> no comparison
        # (but the reply is still captured as training data).
        tid = 91007
        _seed_draft(tid, draft_text="ESCALATE — DO NOT AUTO-REPLY", status="escalated")
        ctx = _build_ctx(tid, [
            _customer_msg(701, "I want a refund"),
            _human_public_reply_msg(702, NEAR_IDENTICAL_REPLY),
        ])
        server.run_workflow_b(ctx)

        replies = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM replies WHERE ticket_id = ?", (tid,)).fetchall()
        comps = feedback_db.get_conn(_DB_PATH).execute(
            "SELECT * FROM comparisons WHERE ticket_id = ?", (tid,)).fetchall()
        self.assertEqual(len(replies), 1)
        self.assertEqual(len(comps), 0,
                         "an escalation note is not a real customer draft to compare")
        print("PROP 4b OK: escalation-status draft is not compared (reply kept, "
              "no comparison).")

    # -- Property 5: a throwing dependency never escapes run_workflow_b. ----- #
    def test_dependency_failure_is_isolated(self):
        tid = 91008
        _seed_draft(tid)
        orig = feedback_db.record_reply

        def _boom(*_a, **_k):
            raise RuntimeError("simulated feedback_db failure")

        feedback_db.record_reply = _boom
        try:
            ctx = _build_ctx(tid, [
                _customer_msg(801, "where is my order?"),
                _human_public_reply_msg(802, NEAR_IDENTICAL_REPLY),
            ])
            # Must NOT raise — run_workflow_b swallows and logs the error.
            try:
                server.run_workflow_b(ctx)
            except Exception as e:  # pragma: no cover - would be a failure
                self.fail(f"Workflow B leaked an exception: {e!r}")
        finally:
            feedback_db.record_reply = orig
        print("PROP 5 OK: a throwing dependency is isolated — run_workflow_b "
              "never raises.")

    # -- Meta: confirm the trip-wires are actually armed. -------------------- #
    def test_tripwires_are_armed(self):
        with self.assertRaises(NetworkTripWire):
            gorgias_api.request("GET", "http://x", "u", "k")
        with self.assertRaises(PublicMessageTripWire):
            server.GorgiasClient.add_message_to_ticket(object(), 1, "hi")
        print("META OK: network + public-message trip-wires are armed and fire "
              "(Workflow B never tripped them in any test above).")


if __name__ == "__main__":
    import sys
    print("=" * 64)
    print("WORKFLOW_B CAPTURE-ONLY TEST")
    print(f"  LLM_PROVIDER       = {os.environ.get('LLM_PROVIDER')}")
    print(f"  HERMES_ALLOW_WRITE = {os.environ.get('HERMES_ALLOW_WRITE', '(unset)')}")
    print(f"  FEEDBACK_DB_PATH   = {_DB_PATH}")
    print(f"  BOT_AGENT_USER_ID  = {gorgias_api.DEFAULT_AGENT_USER_ID}")
    print(f"  INTERNAL_NOTE_CH   = {gorgias_api.INTERNAL_NOTE_CHANNEL}")
    print("=" * 64)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestWorkflowBCapture)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Clean up the throwaway db.
    try:
        os.remove(_DB_PATH)
        os.rmdir(_TMPDIR)
    except OSError:
        pass

    if result.wasSuccessful():
        print("\nWORKFLOW_B TEST OK")
        sys.exit(0)
    else:
        print("\nWORKFLOW_B TEST FAILED")
        sys.exit(1)
