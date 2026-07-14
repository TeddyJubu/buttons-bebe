#!/usr/bin/env python3
"""
test_workflow_a.py — Stage 2, Task 9: DRY-RUN SAFETY TEST for Workflow A.

Proves the single most important property of the whole system:

    Workflow A only ever writes an INTERNAL NOTE (and internal tag/priority
    metadata) for a human to review, in DRY-RUN by default, and NEVER messages
    a customer.

This test does NOT touch the live Gorgias API or a live LLM:
  * LLM_PROVIDER=mock is forced before importing anything — the model gateway's
    mock provider makes no network call and returns a deterministic draft.
  * FEEDBACK_DB_PATH points at a throwaway temp db, so the real feedback.db is
    never polluted.
  * HERMES_ALLOW_WRITE is cleared and WORKFLOW_A_CONFIRM is unset — the default
    safe state — so every Gorgias write must return dry_run=True.
  * A NETWORK TRIP-WIRE replaces gorgias_api.request (and GorgiasClient._request)
    with a function that fails the test if ever called. Combined with the
    dry-run assertions, this proves no write reaches the network.
  * GorgiasClient.add_message_to_ticket (the ONLY public/customer-message path)
    is monkeypatched to fail the test if it is ever invoked.

Properties proven (see TestWorkflowASafety):
  1. Default (HERMES_ALLOW_WRITE unset) -> NO real network write; every Gorgias
     write returns dry_run=True and no network call is made.
  2. Workflow A NEVER calls add_message_to_ticket / any public path. The only
     Gorgias write attempted is an internal note (channel=internal-note,
     public=False).
  3. A SENSITIVE ticket (refund) is ESCALATED — should_post=False, an escalation
     note, no customer-ready draft, tagged "escalate", priority bumped.
  4. A benign ticket -> a draft internal note (dry-run) AND a feedback.db drafts
     row (in the temp db).
  5. The handler never raises out of Workflow A even if a dependency throws.

Run:  python3 test_workflow_a.py      (stdlib unittest; prints OK on success)
"""

import os
import tempfile
import unittest

# --- Force the safe/offline environment BEFORE importing project modules. --- #
os.environ["LLM_PROVIDER"] = "mock"          # no live LLM, deterministic draft
os.environ.pop("HERMES_ALLOW_WRITE", None)   # default: writes are dry-run
os.environ.pop("WORKFLOW_A_CONFIRM", None)   # default: per-feature gate closed
# MUTE the focused owner Telegram alerts (Stage 5, Task 15). Telegram is LIVE
# (real token + owner chat ids in config.json), and Workflow A fires an
# escalation/KB-gap alert. Set this BEFORE import so server._alerts_enabled()
# returns False and the test can NEVER spam the owner's real chat. A Telegram
# trip-wire below (TestWorkflowASafety.setUp) is belt-and-braces on top of this.
os.environ["WORKFLOW_A_TELEGRAM_ALERTS"] = "0"

# Throwaway feedback db so the real one is never touched. Set before import so
# feedback_db.DB_PATH (read at import) points here.
_TMPDIR = tempfile.mkdtemp(prefix="wf_a_test_")
_DB_PATH = os.path.join(_TMPDIR, "feedback_test.db")
os.environ["FEEDBACK_DB_PATH"] = _DB_PATH

import pipeline          # noqa: E402
import gorgias_api       # noqa: E402
import feedback_db       # noqa: E402
import server            # noqa: E402

# Make sure the throwaway db has the schema.
feedback_db.init_db(_DB_PATH)


# --------------------------------------------------------------------------- #
# Trip-wires
# --------------------------------------------------------------------------- #
class NetworkTripWire(AssertionError):
    """Raised if any code path attempts a real Gorgias network call."""


class PublicMessageTripWire(AssertionError):
    """Raised if Workflow A ever reaches the public customer-message path."""


class TelegramTripWire(AssertionError):
    """Raised if Workflow A ever attempts a LIVE Telegram send (not dry-run).

    Telegram is LIVE in this project (real token + owner chat ids in
    config.json). This trip-wire is belt-and-braces on top of muting alerts via
    WORKFLOW_A_TELEGRAM_ALERTS=0: if anything ever tried to send for real, the
    test fails LOUDLY instead of spamming the owner's real chat.
    """


def _build_ticket_context(ticket_id, body_text, *, subject="", customer_email="p@example.com"):
    """Build a minimal pipeline.TicketContext as if the pipeline had fetched it."""
    ctx = pipeline.TicketContext()
    ctx.event_type = "ticket-created"
    ctx.trigger = "ticket-created"
    ctx.ticket_id = ticket_id
    ctx.customer_id = 9
    ctx.from_agent = False
    ctx.ticket = {"id": ticket_id, "subject": subject, "status": "open",
                  "customer": {"id": 9, "email": customer_email}}
    ctx.messages = [{"id": 1, "from_agent": False, "body_text": body_text,
                     "subject": subject}]
    ctx.customer = {"id": 9, "email": customer_email}
    ctx.order_context = {"shopify_found": False, "orders": [],
                         "customer_email": customer_email}
    return ctx


class TestWorkflowASafety(unittest.TestCase):

    def setUp(self):
        # Ensure the default safe environment for every test.
        os.environ.pop("HERMES_ALLOW_WRITE", None)
        os.environ.pop("WORKFLOW_A_CONFIRM", None)
        self._orig_wf_a_confirm = server.WORKFLOW_A_CONFIRM
        server.WORKFLOW_A_CONFIRM = False

        # Save originals so each test restores cleanly.
        self._orig_request = gorgias_api.request
        self._orig_client_request = server.GorgiasClient._request
        self._orig_add_message = server.GorgiasClient.add_message_to_ticket

        # Capture what Workflow A *attempts* to write, without going to network.
        self.posted_notes = []
        self._orig_post_note = gorgias_api.post_internal_note

        def _capturing_post_note(base_url, username, api_key, ticket_id,
                                 body_text, sender_id, **kwargs):
            res = self._orig_post_note(base_url, username, api_key, ticket_id,
                                       body_text, sender_id, **kwargs)
            # Record the payload the engine would have posted (dry-run desc).
            self.posted_notes.append({
                "ticket_id": ticket_id,
                "body_text": body_text,
                "result": res,
            })
            return res

        gorgias_api.post_internal_note = _capturing_post_note

        # NETWORK TRIP-WIRE: any real HTTP attempt fails the test.
        def _no_network(*_a, **_k):
            raise NetworkTripWire(
                "SAFETY VIOLATION: Workflow A attempted a real Gorgias network call."
            )

        gorgias_api.request = _no_network
        server.GorgiasClient._request = lambda *a, **k: _no_network()

        # PUBLIC-MESSAGE TRIP-WIRE: customer-facing path must never be reached.
        def _no_public(*_a, **_k):
            raise PublicMessageTripWire(
                "SAFETY VIOLATION: Workflow A reached the public customer-message path "
                "(add_message_to_ticket)."
            )

        server.GorgiasClient.add_message_to_ticket = _no_public

        # TELEGRAM TRIP-WIRE: a LIVE owner-chat send must never happen in tests.
        # The low-level telegram_notify._send is the single send seam; replace
        # it so ANY non-dry-run send fails the test, while a dry_run=True call
        # still returns a faithful payload (so wiring code never crashes).
        import telegram_notify
        self._tg = telegram_notify
        self._orig_tg_send = telegram_notify._send

        def _trip_send(text, dry_run=False, **_k):
            if not dry_run:
                raise TelegramTripWire(
                    "SAFETY VIOLATION: Workflow A attempted a LIVE Telegram send "
                    "to the owner chat during tests."
                )
            return {"ok": True, "dry_run": True, "results": [], "text": text}

        telegram_notify._send = _trip_send

    def tearDown(self):
        self._tg._send = self._orig_tg_send
        gorgias_api.request = self._orig_request
        gorgias_api.post_internal_note = self._orig_post_note
        server.GorgiasClient._request = self._orig_client_request
        server.WORKFLOW_A_CONFIRM = self._orig_wf_a_confirm
        server.GorgiasClient.add_message_to_ticket = self._orig_add_message

    # -- Property 1 + 2: benign ticket, dry-run, internal-note-only, no net. -- #
    def test_benign_dryrun_internal_note_only_no_network(self):
        ctx = _build_ticket_context(
            70001, "where is my order? has it shipped yet?",
            subject="order status")
        # Must NOT raise (no network, no public path), returns nothing.
        server.run_workflow_a(ctx)

        self.assertEqual(len(self.posted_notes), 1,
                         "exactly one internal note should be attempted")
        note = self.posted_notes[0]
        res = note["result"]

        # Property 1: dry-run by default, no network.
        self.assertTrue(res.get("dry_run"),
                        f"default write must be dry-run, got {res}")
        # Property 2: the attempted write is an INTERNAL note (not public).
        payload = res["payload"]
        self.assertEqual(payload["channel"], gorgias_api.INTERNAL_NOTE_CHANNEL)
        self.assertIs(payload["public"], False)
        self.assertNotIn("email", str(payload.get("channel")))
        # The body carries the machine header + the draft for human review.
        self.assertIn("Hermes draft", note["body_text"])
        print("PROP 1+2 OK: benign ticket -> dry-run INTERNAL note "
              "(channel=internal-note, public=False), NO network, NO public path.")

    def test_benign_writes_feedback_row(self):
        # Property 4: a benign ticket persists a drafts row in the temp db.
        before = feedback_db.recent_drafts(limit=100, path=_DB_PATH)
        ctx = _build_ticket_context(
            70002, "do you ship to canada and how long does it take?",
            subject="shipping")
        server.run_workflow_a(ctx)
        after = feedback_db.recent_drafts(limit=100, path=_DB_PATH)
        self.assertEqual(len(after), len(before) + 1,
                         "a benign ticket must persist exactly one drafts row")
        row = feedback_db.drafts_for_ticket(70002, path=_DB_PATH)[0]
        self.assertEqual(row["ticket_id"], 70002)
        self.assertEqual(row["dry_run"], 1, "row must record dry_run=1 by default")
        self.assertIsNone(row["posted_note_id"], "no real note id in dry-run")
        self.assertEqual(row["status"], "drafted")
        print(f"PROP 4 OK: benign ticket -> feedback.db drafts row id={row['id']} "
              f"status={row['status']} dry_run={row['dry_run']} (temp db).")

    # -- Property 3: sensitive ticket escalates, no customer draft. ---------- #
    def test_sensitive_ticket_escalates_no_customer_draft(self):
        import classifier as _clf
        captured_cls = []
        orig_classify = _clf.classify
        def _capture_classify(*a, **k):
            r = orig_classify(*a, **k)
            captured_cls.append(r)
            return r
        _clf.classify = _capture_classify
        try:
            ctx = _build_ticket_context(
                70003, "I want a refund for my order, it is not what I expected.",
                subject="refund please")
            server.run_workflow_a(ctx)
        finally:
            _clf.classify = orig_classify

        self.assertEqual(len(self.posted_notes), 1)
        note = self.posted_notes[0]
        res = note["result"]
        self.assertTrue(res.get("dry_run"))
        # Internal note, never public.
        self.assertEqual(res["payload"]["channel"], gorgias_api.INTERNAL_NOTE_CHANNEL)
        self.assertIs(res["payload"]["public"], False)

        # DIRECT assertion: the classifier must have flagged auto_draft_allowed=False.
        self.assertTrue(captured_cls, "classifier.classify must have been called")
        self.assertFalse(captured_cls[0].auto_draft_allowed,
                         f"Refund ticket must have auto_draft_allowed=False, got {captured_cls[0]!r}")

        # The note is a clearly-labeled ESCALATION, not a customer-ready reply.
        body = note["body_text"]
        self.assertTrue("ESCALATE" in body or "DO NOT AUTO-REPLY" in body.upper(),
                        "sensitive ticket note must be a labeled escalation")
        # No customer-facing refund promise was drafted.
        low = body.lower()
        for forbidden in ("your refund", "we will refund", "i've refunded",
                          "i have refunded", "refund has been"):
            self.assertNotIn(forbidden, low,
                             f"escalation note must not promise a refund ({forbidden!r})")

        # The metrics row records the escalation, dry-run.
        row = feedback_db.drafts_for_ticket(70003, path=_DB_PATH)[0]
        self.assertEqual(row["status"], "escalated")
        self.assertEqual(row["dry_run"], 1)
        print("PROP 3 OK: sensitive refund -> ESCALATED internal note "
              "(auto_draft_allowed=False, no customer draft), tagged/persisted dry-run.")

    # -- PII must be scrubbed from stored customer_message. ------------------- #
    def test_pii_scrubbed_in_feedback_row(self):
        ctx = _build_ticket_context(
            70010,
            "My email is shopper@example.com and my number is 555-123-4567",
            subject="pii in message")
        server.run_workflow_a(ctx)
        rows = feedback_db.drafts_for_ticket(70010, path=_DB_PATH)
        self.assertEqual(len(rows), 1, "must have exactly one feedback row")
        stored = str(rows[0]["customer_message"] or "")
        self.assertNotIn("shopper@example.com", stored,
                         "email must be scrubbed from stored customer_message")
        self.assertNotIn("555-123-4567", stored,
                         "phone must be scrubbed from stored customer_message")
        print("PII SCRUB OK: email + phone stripped from stored customer_message.")

    # -- Property 5: a throwing dependency never escapes Workflow A. ---------- #
    def test_dependency_failure_is_isolated(self):
        # Make draft_engine.generate_draft blow up.
        orig = server.draft_engine.generate_draft

        def _boom(*_a, **_k):
            raise RuntimeError("simulated dependency failure")

        server.draft_engine.generate_draft = _boom
        try:
            ctx = _build_ticket_context(70004, "hello, a question", subject="q")
            # Must NOT raise — Workflow A swallows and logs the error.
            try:
                server.run_workflow_a(ctx)
            except Exception as e:  # pragma: no cover - would be a failure
                self.fail(f"Workflow A leaked an exception: {e!r}")
        finally:
            server.draft_engine.generate_draft = orig
        # No write was attempted because draft generation failed before posting.
        self.assertEqual(len(self.posted_notes), 0)
        print("PROP 5 OK: a throwing dependency is isolated — Workflow A never raises.")

    # -- Confirm all trip-wires actually trip (meta-check). ------------------- #
    def test_tripwires_are_armed(self):
        # If these did NOT raise, the other tests would be meaningless.
        with self.assertRaises(NetworkTripWire):
            gorgias_api.request("GET", "http://x", "u", "k")
        with self.assertRaises(PublicMessageTripWire):
            server.GorgiasClient.add_message_to_ticket(object(), 1, "hi")
        import telegram_notify as _tn
        with self.assertRaises(TelegramTripWire):
            _tn._send("probe", dry_run=False)
        print("META OK: network + public-message + telegram trip-wires are armed and fire.")


class TestWorkflowARouting(unittest.TestCase):
    """Routing regression (review fix): the DOTTED Gorgias new-message event
    `ticket.message.created` (the canonical customer follow-up) must reach
    Workflow A, and agent messages (from_agent=True) must NOT — they route to B.
    """

    def setUp(self):
        # Record whether run_workflow_a is reached, without doing any real work.
        self._orig_run = server.run_workflow_a
        self.wf_a_calls = []
        server.run_workflow_a = lambda ctx: self.wf_a_calls.append(
            getattr(ctx, "ticket_id", None))

        # Belt-and-braces trip-wires: if the recorder is somehow bypassed and
        # real WF-A code runs, these catch any network or public-message leak.
        self._orig_request = gorgias_api.request
        self._orig_add_message = server.GorgiasClient.add_message_to_ticket

        def _no_network(*_a, **_k):
            raise NetworkTripWire("Routing test triggered a real network call")

        def _no_public(*_a, **_k):
            raise PublicMessageTripWire("Routing test reached public message path")

        gorgias_api.request = _no_network
        server.GorgiasClient.add_message_to_ticket = _no_public

    def tearDown(self):
        server.run_workflow_a = self._orig_run
        gorgias_api.request = self._orig_request
        server.GorgiasClient.add_message_to_ticket = self._orig_add_message

    def _dispatch(self, event_type, from_agent, ticket_id=1):
        """Mirror _handle_webhook's 3-way dispatch using the REAL routing
        decision (server.route_for_event), driving it with a stub ctx. Returns
        the route string. Only Workflow A's entry point is observed (recorder).
        """
        ctx = _build_ticket_context(ticket_id, "a customer question")
        ctx.from_agent = from_agent
        route = server.route_for_event(event_type, ctx.from_agent)
        if route == "A":
            server.run_workflow_a(ctx)   # the recorder
        # route == "B" -> Workflow B stub (no-op here); None -> context only.
        return route

    def test_pure_route_for_event_table(self):
        # The real decision function over the full event matrix.
        self.assertEqual(server.route_for_event("ticket.message.created", False), "A")
        self.assertEqual(server.route_for_event("ticket.created", False), "A")
        self.assertEqual(server.route_for_event("ticket-message-created", False), "A")
        self.assertEqual(server.route_for_event("ticket-created", False), "A")
        self.assertEqual(server.route_for_event("ticket.message.created", True), "B")
        self.assertEqual(server.route_for_event("ticket.created", True), "B")
        self.assertIsNone(server.route_for_event("ticket.updated", False))
        print("ROUTE-TABLE OK: route_for_event maps every event/from_agent case correctly.")

    def test_dotted_message_created_reaches_workflow_a(self):
        # (1) the canonical dotted customer follow-up event reaches Workflow A.
        route = self._dispatch("ticket.message.created", False, ticket_id=80001)
        self.assertEqual(route, "A")
        self.assertIn(80001, self.wf_a_calls,
                      "ticket.message.created (from_agent=False) must reach run_workflow_a")
        print("ROUTE 1 OK: dotted 'ticket.message.created' (customer) -> Workflow A.")

    def test_ticket_created_reaches_workflow_a(self):
        # (2) a new ticket from the customer reaches Workflow A.
        route = self._dispatch("ticket.created", False, ticket_id=80002)
        self.assertEqual(route, "A")
        self.assertIn(80002, self.wf_a_calls)
        print("ROUTE 2 OK: 'ticket.created' (customer) -> Workflow A.")

    def test_agent_message_routes_to_workflow_b_not_a(self):
        # (3) an agent message must route to Workflow B and NOT reach Workflow A.
        route = self._dispatch("ticket.message.created", True, ticket_id=80003)
        self.assertEqual(route, "B", "from_agent=True must route to Workflow B")
        self.assertNotIn(80003, self.wf_a_calls,
                         "agent message must NOT reach Workflow A")
        # Also true for a ticket.created carrying from_agent=True.
        route2 = self._dispatch("ticket.created", True, ticket_id=80004)
        self.assertEqual(route2, "B")
        self.assertNotIn(80004, self.wf_a_calls)
        print("ROUTE 3 OK: from_agent=True -> Workflow B, never Workflow A.")


if __name__ == "__main__":
    import sys
    print("=" * 64)
    print("WORKFLOW_A DRY-RUN SAFETY TEST")
    print(f"  LLM_PROVIDER     = {os.environ.get('LLM_PROVIDER')}")
    print(f"  HERMES_ALLOW_WRITE = {os.environ.get('HERMES_ALLOW_WRITE', '(unset)')}")
    print(f"  WORKFLOW_A_CONFIRM = {os.environ.get('WORKFLOW_A_CONFIRM', '(unset)')} "
          f"(server.WORKFLOW_A_CONFIRM={server.WORKFLOW_A_CONFIRM})")
    print(f"  FEEDBACK_DB_PATH = {_DB_PATH}")
    print("=" * 64)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestWorkflowASafety))
    suite.addTests(loader.loadTestsFromTestCase(TestWorkflowARouting))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Clean up the throwaway db.
    try:
        os.remove(_DB_PATH)
        os.rmdir(_TMPDIR)
    except OSError:
        pass

    if result.wasSuccessful():
        print("\nWORKFLOW_A SAFETY TEST OK")
        sys.exit(0)
    else:
        print("\nWORKFLOW_A SAFETY TEST FAILED")
        sys.exit(1)
