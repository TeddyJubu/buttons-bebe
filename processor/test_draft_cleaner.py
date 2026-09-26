"""Unit tests for processor/draft_cleaner.py.

Ported from fable/tests/unit/test_draft_cleaner.py and converted from pytest to
unittest so the offline release gate (tools/verify_release.sh) can run it.

Where possible the cases are seeded with the REAL model outputs from the live QA
run (testing/results-live.json), so the leak fixes are exercised against genuine
drafts rather than toy strings:

  * QA #01 (R01, WISMO), #04 (R04, intl + return), #10 (R10, fabric) — the model
    sometimes repeats the whole draft twice or appends self-commentary
    ("The response above was complete...").
  * QA #19 (E01, empty message) — nothing to answer, must NOT draft.

Those seeded cases skip themselves if testing/ has not been merged yet (Task 1 of
the Fable port). Everything else runs unconditionally.

The other half of the suite proves the cleaner does NOT touch clean drafts: a
normal reply that happens to say "complete" or "note" mid-sentence is left
untouched, and a legitimate escalation internal note is not cut.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import time
import unittest

PROCESSOR_DIR = pathlib.Path(__file__).resolve().parent
if str(PROCESSOR_DIR) not in sys.path:
    sys.path.insert(0, str(PROCESSOR_DIR))

import draft_cleaner as dc  # noqa: E402

RESULTS_LIVE = PROCESSOR_DIR.parent / "testing" / "results-live.json"
_HAVE_LIVE = RESULTS_LIVE.is_file()
_SKIP_REASON = f"{RESULTS_LIVE} not present — merge the Task 1 test harness first"

_GOOD = "Hi! Thanks for reaching out. Your order usually ships within 24-48 hours."
REAL_DRAFT_IDS = ["R01", "R04", "R10"]

SELF_TALK_LINES = [
    ("response-above", "The response above was complete and answers the question."),
    ("previous-complete", "The previous response was already complete."),
    ("prior-complete", "The prior draft is complete."),
    ("above-response", "The above response addresses the customer's question."),
    ("this-reply-complete", "This reply is complete."),
    ("this-response-above", "This response above is now complete."),
    ("i-have-completed", "I have completed the response."),
    ("i-have-finished", "I have now finished this draft."),
    ("end-of-response", "End of response"),
    ("end-of-draft-bracket", "[End of draft]"),
    ("as-an-ai", "As an AI, I cannot process the refund myself."),
]


def _norm(s: str) -> str:
    """Whitespace-flatten + lowercase (mirrors the cleaner's own normalisation)."""
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _answer_body(hermes_output: str) -> str:
    """Pull just the drafted reply out of a `RISK/ACTION/ANSWER:` block."""
    marker = "ANSWER:\n"
    i = hermes_output.find(marker)
    return (hermes_output[i + len(marker):] if i != -1 else hermes_output).strip()


def _live_answer(ticket_id: str) -> str:
    data = json.loads(RESULTS_LIVE.read_text(encoding="utf-8"))
    for row in data:
        if row.get("id") == ticket_id:
            return _answer_body(row["hermes_output"])
    raise KeyError(f"{ticket_id} not found in {RESULTS_LIVE}")


# ===========================================================================
# 1. Self-talk markers — each one is stripped from the tail of a draft.
# ===========================================================================
class SelfTalkTests(unittest.TestCase):
    def test_each_self_talk_marker_is_stripped(self):
        for label, marker in SELF_TALK_LINES:
            with self.subTest(marker=label):
                draft = f"{_GOOD}\n\n{marker}\n\nSome trailing model chatter."
                res = dc.clean_draft(draft)
                self.assertFalse(res.no_draft)
                self.assertIn("stripped model self-commentary", res.reasons)
                self.assertEqual(res.text, _GOOD)
                self.assertNotIn(marker, res.text)

    def test_marker_with_leading_markdown_is_stripped(self):
        res = dc.clean_draft(f"{_GOOD}\n\n> The response above was complete.")
        self.assertEqual(res.text, _GOOD)
        self.assertIn("stripped model self-commentary", res.reasons)

    def test_draft_that_is_only_self_talk_becomes_no_draft(self):
        res = dc.clean_draft("The response above was complete.")
        self.assertTrue(res.no_draft)
        self.assertEqual(res.text, "")
        self.assertIn("nothing left after cleaning", res.reasons)


# ===========================================================================
# 2. Duplicated-draft cases — whole-text and paragraph-level.
# ===========================================================================
class DuplicationTests(unittest.TestCase):
    def test_whole_text_duplicated_blank_line(self):
        res = dc.clean_draft(f"{_GOOD}\n\n{_GOOD}")
        self.assertIn("removed duplicated draft body", res.reasons)
        self.assertEqual(res.text, _GOOD)

    def test_whole_text_duplicated_single_newline(self):
        res = dc.clean_draft(f"{_GOOD}\n{_GOOD}")
        self.assertIn("removed duplicated draft body", res.reasons)
        self.assertEqual(_norm(res.text), _norm(_GOOD))

    def test_whole_text_tripled(self):
        res = dc.clean_draft(f"{_GOOD}\n\n{_GOOD}\n\n{_GOOD}")
        self.assertIn("removed duplicated draft body", res.reasons)
        self.assertEqual(_norm(res.text), _norm(_GOOD))

    def test_paragraph_level_duplication_multi_paragraph(self):
        body = (
            "Hi there,\n\n"
            "Thanks for reaching out about your order. It usually ships in 24-48 hours "
            "before it leaves our warehouse.\n\n"
            "Warmly,\nButtons Bebe Support"
        )
        res = dc.clean_draft(f"{body}\n\n{body}")
        self.assertIn("removed duplicated draft body", res.reasons)
        self.assertEqual(res.text, body)
        self.assertEqual(res.text.count("Buttons Bebe Support"), 1)

    def test_dedup_recovers_original_formatting_exactly(self):
        body = "Line one of the reply here.\n\n- bullet a\n- bullet b\n\nThanks so much!"
        res = dc.clean_draft(f"{body}\n\n{body}")
        self.assertEqual(res.text, body)


# ===========================================================================
# 3. Seeded with REAL leaked-style drafts from testing/results-live.json.
# ===========================================================================
@unittest.skipUnless(_HAVE_LIVE, _SKIP_REASON)
class RealDraftTests(unittest.TestCase):
    def test_real_answer_duplicated_is_collapsed(self):
        for ticket_id in REAL_DRAFT_IDS:
            with self.subTest(ticket=ticket_id):
                answer = _live_answer(ticket_id)
                self.assertGreater(len(answer), 40)
                baseline = dc.clean_draft(answer)
                self.assertFalse(baseline.no_draft)
                self.assertTrue(baseline.text)
                res = dc.clean_draft(f"{answer}\n\n{answer}")
                self.assertIn("removed duplicated draft body", res.reasons)
                self.assertEqual(res.no_draft, baseline.no_draft)
                self.assertEqual(res.text, baseline.text)

    def test_real_answer_with_appended_self_talk_is_trimmed(self):
        for ticket_id in REAL_DRAFT_IDS:
            with self.subTest(ticket=ticket_id):
                answer = _live_answer(ticket_id)
                baseline = dc.clean_draft(answer)
                self.assertFalse(baseline.no_draft)
                self.assertTrue(baseline.text)
                leaked = f"{answer}\n\nThe response above was complete and ready for review."
                res = dc.clean_draft(leaked)
                self.assertIn("stripped model self-commentary", res.reasons)
                self.assertEqual(res.no_draft, baseline.no_draft)
                self.assertEqual(res.text, baseline.text)

    def test_real_answer_duplicated_then_self_talk(self):
        answer = _live_answer("R01")
        baseline = dc.clean_draft(answer)
        self.assertFalse(baseline.no_draft)
        self.assertTrue(baseline.text)
        leaked = f"{answer}\n\n{answer}\n\nThe response above was complete."
        res = dc.clean_draft(leaked)
        self.assertIn("stripped model self-commentary", res.reasons)
        self.assertIn("removed duplicated draft body", res.reasons)
        self.assertEqual(res.no_draft, baseline.no_draft)
        self.assertEqual(res.text, baseline.text)

    def test_real_answers_never_reach_console_overlong(self):
        for ticket_id in REAL_DRAFT_IDS:
            with self.subTest(ticket=ticket_id):
                answer = _live_answer(ticket_id)
                res = dc.clean_draft(answer)
                self.assertFalse(res.no_draft)
                self.assertTrue(res.text)
                self.assertFalse(dc._exceeds_sentence_limit(res.text))
                if dc._exceeds_sentence_limit(answer):
                    self.assertIn("concise-output sentence limit", " ".join(res.reasons))

    def test_empty_customer_message_qa19(self):
        rows = json.loads(RESULTS_LIVE.read_text(encoding="utf-8"))
        e01 = next(r for r in rows if r["id"] == "E01")
        self.assertEqual(e01["message"], "")
        self.assertFalse(dc.should_draft(e01["message"]).ok)


# ===========================================================================
# 4. No false positives — clean drafts pass through UNCHANGED.
# ===========================================================================
class NoFalsePositiveTests(unittest.TestCase):
    def test_clean_reply_saying_complete_is_not_cut(self):
        draft = "Hi! Your order is complete and on its way. Thanks so much for your patience!"
        res = dc.clean_draft(draft)
        self.assertEqual(res.text, draft)
        self.assertEqual(res.reasons, [])

    def test_clean_reply_saying_note_is_not_cut(self):
        draft = "Hi! Note that processing takes 24-48 hours before your order ships. Thanks!"
        res = dc.clean_draft(draft)
        self.assertEqual(res.text, draft)
        self.assertEqual(res.reasons, [])

    def test_an_internal_note_is_cut_but_never_lost(self):
        # CONTRACT CHANGE, round 9. These used to be left in the draft on the
        # grounds that stripping them "threw the warning away". That stopped
        # being true when _cut_self_talk started returning what it removed and
        # hermes_runner started putting it on `reason`, which the console
        # shows. Leaving them in meant the human could Send an internal note
        # about the customer TO that customer.
        note = (
            "Internal note for human review:\n"
            "Customer is requesting a refund on order #10322. Do not promise money.\n"
            "Notes for the human agent: verify the delivery date and 7-day window first."
        )
        res = dc.clean_draft(note)
        self.assertNotIn("Do not promise money", res.text)
        self.assertIn("Do not promise money", res.removed_note)
        # Nothing was left that could be sent to a customer.
        self.assertTrue(res.no_draft)

    def test_two_different_paragraphs_are_not_deduped(self):
        draft = (
            "Hi! Yes, we ship to Canada; the rate shows at checkout.\n\n"
            "Customs and duties are the customer's responsibility. Thanks!"
        )
        res = dc.clean_draft(draft)
        self.assertEqual(res.text, draft)
        self.assertEqual(res.reasons, [])

    def test_short_repeated_content_is_not_collapsed(self):
        draft = "Yes.\n\nYes."
        res = dc.clean_draft(draft)
        self.assertEqual(res.text, draft)
        self.assertNotIn("removed duplicated draft body", res.reasons)

    def test_sensitive_prefix_draft_survives(self):
        # The console strips the [SENSITIVE ...] banner itself; the cleaner must
        # not treat the banner line as self-talk and throw the draft away.
        draft = ("[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]\n\n"
                 "Hi! Thanks for explaining the problem. Please share a photo of the item with its tag.")
        res = dc.clean_draft(draft)
        self.assertEqual(res.text, draft)
        self.assertEqual(res.reasons, [])


class QualityGuardTests(unittest.TestCase):
    """Customer-facing drafts are guarded against the three brand-risk patterns."""

    def test_bracketed_internal_note_is_not_sendable(self):
        result = dc.clean_draft(
            "[INTERNAL NOTE FOR HUMAN REVIEW — do not send as-is]\n"
            "Customer needs a confirmed size before we answer."
        )
        self.assertTrue(result.no_draft)
        self.assertEqual(result.text, "")
        self.assertIn("Customer needs", result.removed_note)

    def test_plain_internal_note_labels_are_not_sendable(self):
        for label in (
            "SUGGESTED REPLY:",
            "RECOMMENDED CUSTOMER-FACING REPLY:",
            "ACTION NEEDED:",
            "POLICY BASIS:",
            "HUMAN REVIEW:",
        ):
            with self.subTest(label=label):
                result = dc.clean_draft(
                    f"Hi! Thanks for your message.\n\n{label}\n"
                    "Ask the warehouse to send a replacement."
                )
                self.assertFalse(result.no_draft)
                self.assertEqual(
                    result.text,
                    "Hi! Thanks for your message.",
                )
                self.assertIn("Ask the warehouse", result.removed_note)

    def test_internal_note_after_customer_text_is_removed(self):
        result = dc.clean_draft(
            "Hi! Thanks for your message. I don't have a confirmed answer to share yet.\n\n"
            "[SUGGESTED CUSTOMER-FACING REPLY]\n"
            "Ask the warehouse to send a replacement."
        )
        self.assertFalse(result.no_draft)
        self.assertEqual(
            result.text,
            "Hi! Thanks for your message. I don't have a confirmed answer to share yet.",
        )
        self.assertIn("Ask the warehouse", result.removed_note)

    def test_overlong_normal_draft_is_shortened(self):
        draft = (
            "Hi! Shipping depends on the method selected. "
            "USPS usually takes 7–14 days. "
            "UPS is usually next day. "
            "ETA shipping is around 1–2 days. "
            "Processing time is separate from carrier transit."
        )
        result = dc.clean_draft(draft)
        self.assertFalse(result.no_draft)
        self.assertTrue(result.text)
        self.assertFalse(dc._exceeds_sentence_limit(result.text))
        self.assertIn("concise-output sentence limit", " ".join(result.reasons))

    def test_sensitive_draft_allows_five_sentences_and_shortens_six(self):
        body = " ".join([
            "Hi, thanks for your message.",
            "We want to make sure everything is correct.",
            "Please share the item name.",
            "Thank you for your patience.",
            "We appreciate your understanding.",
        ])
        prefix = "[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]\n\n"
        allowed = dc.clean_draft(prefix + body)
        self.assertFalse(allowed.no_draft)

        rejected = dc.clean_draft(prefix + body + " Please wait for our update.")
        self.assertFalse(rejected.no_draft)
        self.assertTrue(rejected.text.startswith(prefix.strip()))
        self.assertFalse(dc._exceeds_sentence_limit(rejected.text))
        self.assertIn("concise-output sentence limit", " ".join(rejected.reasons))

    def test_unsupported_operational_promises_use_review_fallback(self):
        for draft in [
            "Hi! We'll switch your order to pickup right away.",
            "Hi! We'll send you a prepaid return label.",
            "Hi! We have issued your refund.",
            "Hi! We're going to replace the item.",
            "Hi! We're going to send you a prepaid return label.",
            "Hi! I'm going to update your address.",
            "Hi! We'll get your order switched over.",
            "Hi! We'll take care of your order on our end.",
            "Hi! Your refund has been issued.",
            "Hi! A prepaid return label has been sent.",
            "Hi! A replacement has been shipped.",
            "Hi! The warehouse has been contacted.",
            "Hi! We've shipped your replacement.",
            "Hi! The return has been processed.",
            ]:
            with self.subTest(draft=draft):
                result = dc.clean_draft(draft)
                self.assertTrue(result.no_draft)
                self.assertEqual(result.text, '')
                self.assertLessEqual(len(result.text), len(draft))
                self.assertIn("rejected unsupported operational promise", " ".join(result.reasons))

    def test_abbreviations_do_not_consume_the_sentence_budget(self):
        draft = (
            "Hi! U.S. orders usually ship in 2–3 days. "
            "International delivery varies by destination. "
            "Please share the item name."
        )
        result = dc.clean_draft(draft)
        self.assertFalse(result.no_draft)
        self.assertEqual(result.text, draft)
        self.assertEqual(result.reasons, [])

    def test_factual_uncertainty_and_customer_questions_are_not_blocked(self):
        for draft in [
            "Hi! Thanks for your message. I don't have a confirmed answer to share yet.",
            "Hi! Please share the order number.",
            "Hi! We can help with that once we confirm the order details.",
            "Hi! I don't have a confirmed dispatch date.",
            "Hi! We can provide product details and care instructions.",
            "Hi! Could you share a photo of the item with its tag?",
        ]:
            with self.subTest(draft=draft):
                result = dc.clean_draft(draft)
                self.assertFalse(result.no_draft)
                self.assertEqual(result.text, draft)

    def test_reviewed_action_does_not_allow_later_promise(self):
        result = dc.clean_draft(
            "Hi! We're reviewing whether we can update the order, and we'll switch it right away."
        )
        self.assertTrue(result.no_draft)
        self.assertEqual(result.text, '')
        self.assertIn("rejected unsupported operational promise", " ".join(result.reasons))

    def test_short_unsafe_draft_gets_a_short_safe_fallback(self):
        result = dc.clean_draft("We'll ship.")
        self.assertTrue(result.no_draft)
        self.assertEqual(result.text, '')
        self.assertLessEqual(len(result.text), len("We'll ship."))

    def test_verified_shipping_status_is_not_treated_as_a_promise(self):
        for draft in (
            "Hi! Your order has shipped.",
            "Hi! Order #123 has shipped.",
        ):
            with self.subTest(draft=draft):
                result = dc.clean_draft(draft)
                self.assertFalse(result.no_draft)
                self.assertEqual(result.text, draft)


# ===========================================================================
# 5. Empty / whitespace drafts -> no_draft.
# ===========================================================================
class EmptyDraftTests(unittest.TestCase):
    def test_empty_or_whitespace_draft_is_no_draft(self):
        for bad in ["", "   ", "\n\n", "  \n \t ", None]:
            with self.subTest(value=repr(bad)):
                res = dc.clean_draft(bad)
                self.assertTrue(res.no_draft)
                self.assertEqual(res.text, "")

    def test_repetition_collapses_all_the_way_not_just_once(self):
        """4x used to come out doubled: the dedupe only knew 2x and 3x."""
        # Includes the PRIME multiples. The first fix iterated a 2x/3x dedupe,
        # which reaches 4, 6, 8 and 9 but never 5 or 7.
        # Returning at the SMALLEST k meant each pass only divided the copy
        # count by its smallest prime factor: 32 came out as 2, 64 as 4.
        for copies in list(range(2, 13)) + [16, 24, 32, 48, 64]:
            with self.subTest(copies=copies):
                res = dc.clean_draft("\n\n".join([_GOOD] * copies))
                self.assertEqual(_norm(res.text), _norm(_GOOD),
                                 f"{copies} copies did not collapse to one")

    def test_clean_draft_is_idempotent(self):
        leaked = f"{_GOOD}\n\n{_GOOD}\n\nThe response above was complete."
        once = dc.clean_draft(leaked).text
        twice = dc.clean_draft(once)
        self.assertEqual(twice.text, once)
        self.assertEqual(twice.reasons, [])


class NoCatastrophicBacktrackingTests(unittest.TestCase):
    """This gate must never be slow, because slow here means the shop stops.

    should_draft() runs SYNCHRONOUSLY inside the job coroutine, so
    asyncio.wait_for cannot interrupt it, and orchestrator.py holds an
    exclusive flock so there is exactly one processor. A regex that takes
    minutes on an attacker-supplied subject line freezes every ticket, every
    owner alert and the heartbeat until it returns.

    This file has now had TWO such bugs, and the second was introduced by the
    fix for the first - an exponential whole-message alternation was replaced,
    and the replacement's subject pattern carried a quadratic. Measured on it:
    8 000 spaces 0.80 s, 16 000 2.93 s, 32 000 11.64 s.

    Probe sizes are chosen so the broken version is slow but still RETURNS:
    32 000 spaces took 2.67 s before the fix and 0.001 s after, a 2500x gap.
    Bigger probes would be a stronger signal in principle and useless in
    practice - the quadratic version simply never finishes, so the test hangs
    instead of failing, and a hanging test reports nothing.

    The budget is ~1000x the fixed timing, so this is a blowup detector rather
    than a benchmark, and will not flake on a loaded machine.
    """

    BUDGET = 1.0
    # Large enough that a quadratic is unmissable, small enough that it still
    # completes and the assertion can fire.
    BLOWUP_PROBE = 32_000
    GOOD_REPLY = "Hi! Thanks for reaching out. Your order ships within 24-48 hours."

    def _timed(self, fn, *args):
        start = time.perf_counter()
        fn(*args)
        return time.perf_counter() - start

    def test_a_padded_subject_does_not_blow_up(self):
        # The exact shape: the bare word "order", a long whitespace run, and
        # no digit to finish the match.
        for pad in (8_000, self.BLOWUP_PROBE):
            with self.subTest(pad=pad):
                probe = "Order" + " " * pad
                self.assertLess(
                    self._timed(dc._SUBJECT_NOISE_RE.sub, " ", probe),
                    self.BUDGET,
                    "the subject-noise pattern is backtracking again")

    def test_should_draft_is_fast_on_hostile_input(self):
        for subject, message in [
            ("Order" + " " * 200_000, ""),
            ("Re: " * 20_000, ""),
            ("#" + " " * 200_000, "thanks"),
            ("", "thanks " + " " * 200_000),
            ("Order  #  " * 20_000, ""),
            ("Order" + " " * 1_000_000, ""),
        ]:
            with self.subTest(subject=subject[:20], message=message[:20]):
                self.assertLess(self._timed(dc.should_draft, message, subject),
                                self.BUDGET)

    def test_a_horizontal_rule_in_a_draft_does_not_blow_up(self):
        # Round-9 BLOCKER, and this one was mine: round 8 added "[-\\s]*" to
        # an _SELF_TALK_MARKERS entry, and _MARKER_RE already prefixes every
        # alternative with "^[\\s>*#\\-]*". Two adjacent greedy classes
        # overlapping on "-" and whitespace is quadratic. 16 000 dashes took
        # 0.94s, 32 000 took 9.2s, 64 000 took 34.7s end to end - on the HAPPY
        # path, a well-formed model reply containing a horizontal rule.
        for n in (8_000, 32_000):
            with self.subTest(dashes=n):
                draft = f"Hi! Here is the info you asked for.\n{'-' * n}\nThanks!"
                self.assertLess(self._timed(dc.clean_draft, draft), self.BUDGET,
                                "_MARKER_RE is backtracking again")

    def test_the_end_of_marker_still_matches_every_shape(self):
        # ...and dropping the "[-\\s]*" must not have cost the matches it was
        # added for. The outer "^[\\s>*#\\-]*" covers them.
        for line in ("-- end of the draft --", "[End of draft]",
                     "--- End of the reply ---", "> end of draft",
                     "End of response", "  ## end of reply"):
            with self.subTest(line=line):
                result = dc.clean_draft(f"{self.GOOD_REPLY}\n\n{line}")
                self.assertEqual(result.text.strip(), self.GOOD_REPLY)

    def test_a_padded_acknowledgement_cannot_hide_a_complaint(self):
        # Round-9 HIGH. The docstring claimed "truncating cannot lose a
        # decision". It can: truncation REMOVES content, so an ack that fills
        # the cap hides whatever follows. This exact message was suppressed -
        # empty console card, no owner alert, Hermes never invoked.
        padded = (("thanks " * 2857) + " Also, my parcel has been sitting at "
                  "the depot since Tuesday and nobody has called me back.")
        self.assertGreater(len(padded), dc._MAX_GATE_MESSAGE)
        self.assertTrue(dc.should_draft(padded).ok,
                        "a complaint past the cap was silently dropped")

    def test_a_padded_subject_cannot_hide_a_question_either(self):
        # Round-10 MEDIUM. The BODY got the refuse-to-judge fix and the
        # subject kept its hard slice, three lines below the comment saying
        # why that reasoning is wrong. The question fell off the end.
        padded = ("thanks " * 286) + "Why has my refund still not arrived?"
        self.assertGreater(len(padded), dc._MAX_GATE_SUBJECT)
        self.assertTrue(dc.should_draft("", padded).ok,
                        "a question past the subject cap was dropped")
        # ...and the same subject just under the cap always worked.
        self.assertTrue(dc.should_draft("", padded[:1_996]).ok)

    def test_a_padded_subject_alone_is_still_an_acknowledgement(self):
        self.assertFalse(dc.should_draft("thanks", "Re: " + " " * 100_000).ok)
        self.assertFalse(dc.should_draft("thanks", "  Thanks!  " * 5).ok)

    def test_padding_alone_is_still_an_acknowledgement(self):
        # ...and the fix must not have disabled the gate. Whitespace is
        # collapsed BEFORE the length check, so padding cannot fill the budget.
        self.assertFalse(dc.should_draft("thanks" + " " * 100_000).ok)
        self.assertFalse(dc.should_draft("thanks" + "\n" * 100_000).ok)
        self.assertFalse(dc.should_draft("  thanks  " * 3).ok)

    def test_the_gate_bounds_its_input(self):
        # Second line of defence behind the pattern itself. Truncation cannot
        # lose a decision here - this gate only ever asks "is there NOTHING to
        # answer", and more text can only mean more content.
        self.assertLessEqual(dc._MAX_GATE_SUBJECT, 4_000)
        self.assertLessEqual(dc._MAX_GATE_MESSAGE, 50_000)

    def test_no_pattern_ever_sees_more_than_the_cap(self):
        # Structural, and it has to be: with the pattern itself fixed, the
        # caps make no observable difference to any verdict or any timing, so
        # nothing behavioural can tell whether they are still applied. They
        # exist for the NEXT pattern someone adds to this file - which is not
        # hypothetical, since that is exactly how the last one arrived.
        seen: list[int] = []
        real = dc._SUBJECT_NOISE_RE

        class Spy:
            pattern = real.pattern
            search = staticmethod(real.search)

            @staticmethod
            def sub(repl, text, *args, **kwargs):
                seen.append(len(text))
                return real.sub(repl, text, *args, **kwargs)

        dc._SUBJECT_NOISE_RE = Spy()
        try:
            # The BODY has to be contentless, or should_draft returns before
            # it ever looks at the subject.
            dc.should_draft("", "Order" + " " * 500_000)
        finally:
            dc._SUBJECT_NOISE_RE = real
        self.assertTrue(seen, "the subject pattern was never reached")
        self.assertLessEqual(max(seen), dc._MAX_GATE_SUBJECT,
                             "the subject cap is not being applied")

    def test_bounding_does_not_change_any_real_verdict(self):
        # A real question stays a real question, and padding does not turn an
        # acknowledgement into one.
        self.assertTrue(dc.should_draft("Where is my order?" + " " * 100_000).ok)
        self.assertFalse(dc.should_draft("thanks" + " " * 100_000).ok)
        self.assertTrue(dc.should_draft("x" * 30_000 + " where is my order?").ok)


class SafetyNoteSurvivesTests(unittest.TestCase):
    """A warning to the reviewer must never be cut as self-talk.

    Round 6. The code argued that "note to the reviewer" was left out of
    _SELF_TALK_MARKERS because such a note can carry "do NOT send this,
    possible fraud". The reasoning was sound but the implementation was
    phrase-specific: a semantically identical warning that happened to open
    with "The above draft ..." matched a different marker and the whole
    warning was deleted, leaving a clean, sendable draft and no trace.

    The veto is now on the CONTENT of the line, not its opening phrase.
    """

    GOOD = "Hi Sarah, thanks for your message. I don't have a confirmed answer to share yet."

    WARNINGS = [
        "The above draft should NOT be sent as-is — this customer was already "
        "refunded twice for the same order and this looks like fraud.",
        "The above reply must be reviewed carefully before sending.",
        "The previous draft is complete, but please verify the order total first.",
        "This response is complete — do not send until you check with the owner.",
        "End of draft. Warning: possible chargeback, escalate to the owner.",
        "The above response addresses the question, but hold this — the "
        "account looks suspicious.",
    ]

    # Warnings a keyword veto could never have caught - none of them contains
    # "do not send", "fraud", "escalate" or any other flag word. Round 7 found
    # all five still being deleted by the veto version.
    SUBTLE = [
        "The above draft assumes the customer is who they say they are; the "
        "billing address does not match the shipping address on this order.",
        "The above reply promises a replacement we have no stock for - the "
        "SKU is discontinued.",
        "The above response quotes a 30-day window; this order is 94 days old.",
        "I have completed the response, but the customer has 4 open tickets "
        "and 3 prior credits this month.",
        "[End of draft] - the order number the customer gave belongs to a "
        "different account.",
    ]

    def test_the_warning_is_never_lost(self):
        # It comes out of the sendable body - it is not a reply to the
        # customer - but it must still reach the reviewer verbatim.
        for warning in self.WARNINGS + self.SUBTLE:
            with self.subTest(warning=warning[:50]):
                result = dc.clean_draft(f"{self.GOOD}\n\n{warning}")
                self.assertNotIn(warning, result.text,
                                 "a note to the reviewer is not a customer reply")
                self.assertIn(warning, result.removed_note,
                              "a safety warning was silently deleted")

    def test_ordinary_self_talk_is_still_cut(self):
        for chatter in [
            "The above response addresses the customer's question.",
            "The response above was complete and covers everything.",
            "I have now completed the draft.",
            "[End of response]",
            # Round 7: the keyword veto WRONGLY KEPT these, because they
            # happen to contain a flag word.
            "The above response is complete and I double-checked it carefully.",
            "I have completed the response; escalate only if she writes back.",
        ]:
            with self.subTest(chatter=chatter):
                result = dc.clean_draft(f"{self.GOOD}\n\n{chatter}")
                self.assertEqual(result.text.strip(), self.GOOD)
                self.assertTrue(result.reasons, "the cut was not reported")

    # Round-8 review: every one of these reached the sendable draft body,
    # i.e. the text a human clicks Send on. _SELF_TALK_MARKERS only knew a
    # handful of exact phrasings.
    LEAKED_IN_ROUND_8 = [
        "The response above is complete.",
        "The reply above is complete and ready.",
        "The draft above is complete.",
        "Draft complete.",
        "[Draft complete]",
        "I've completed the draft.",
        "I have written the response above.",
        "-- end of the draft --",
        # These two matter most: the prompt asks the model for AGENT NOTE
        # lines AFTER the verdict, but it sometimes puts one inside the draft
        # tags - and they carry things the customer must never read.
        "AGENT NOTE: this customer has 3 prior chargebacks.",
        "(Internal: customer has 3 prior chargebacks.)",
    ]

    def test_the_round_8_leaks_are_cut_from_the_sendable_body(self):
        for chatter in self.LEAKED_IN_ROUND_8:
            with self.subTest(chatter=chatter):
                result = dc.clean_draft(f"{self.GOOD}\n\n{chatter}")
                self.assertEqual(result.text.strip(), self.GOOD,
                                 "model self-commentary reached the draft body")
                self.assertIn(chatter, result.removed_note,
                              "...and was not reported to the reviewer either")

    def test_an_internal_note_never_reaches_the_customer_text(self):
        # The one with real consequences, stated on its own.
        note = "AGENT NOTE: this customer has 3 prior chargebacks."
        result = dc.clean_draft(f"{self.GOOD}\n\n{note}")
        self.assertNotIn("chargeback", result.text)
        self.assertIn("chargeback", result.removed_note)

    def test_a_cut_is_always_reported(self):
        # The reviewer has to be able to tell that text was removed.
        result = dc.clean_draft(f"{self.GOOD}\n\n[End of response]")
        self.assertTrue(result.reasons)
        self.assertIn("End of response", result.removed_note)

    def test_a_clean_draft_reports_nothing_removed(self):
        result = dc.clean_draft(self.GOOD)
        self.assertEqual(result.removed_note, "")
        self.assertEqual(result.reasons, [])


# ===========================================================================
# 6. should_draft — gate on the CUSTOMER message.
# ===========================================================================
class ShouldDraftTests(unittest.TestCase):
    NO_CONTENT = [
        "", "  ", "...", "thanks", "Thanks!", "Thank you!!",
        "thank you so much!", "ty", "Perfect, thanks so much!",
        "cheers", "Thanks again!",
        "\U0001F44D", "\U0001F64F", "\U0001F44D\U0001F44D", None,
    ]
    REAL_QUESTIONS = [
        "Where is my order #BB1015?",
        "I want a refund for order #10322.",
        "Do you ship to Canada and how much?",
        "thanks, but where is my order?",
        "Can I change my shipping address?",
    ]

    # A DECISION word, however much gratitude surrounds it. Moved out of
    # NO_CONTENT in round 6: the agent's previous message may have been
    # "shall I cancel order #10234 before it ships?", and every one of these
    # is a yes. Suppressing them stored an empty console card - no draft, no
    # action controls, no owner alert - while the order shipped.
    #
    # The cost is asymmetric and this file already says so elsewhere: drafting
    # a reply to a thank-you costs the reviewer one glance; dropping an answer
    # costs a customer their order.
    DECISIONS = [
        "Got it, thank you!", "ok thanks", "Ok, thanks!", "Received, thanks",
        "Noted, thanks!", "understood thanks", "okay thank you", "kk thanks",
    ]

    def test_should_not_draft_for_no_content(self):
        for msg in self.NO_CONTENT:
            with self.subTest(message=repr(msg)):
                s = dc.should_draft(msg)
                self.assertFalse(s.ok)
                self.assertTrue(s.reason)

    def test_a_decision_word_always_drafts(self):
        for msg in self.DECISIONS:
            with self.subTest(message=msg):
                self.assertTrue(
                    dc.should_draft(msg).ok,
                    f"{msg!r} may be answering 'shall I cancel your order?'")
        # ...with a realistic subject line too.
        for msg in self.DECISIONS:
            with self.subTest(message=msg, subject="Re: Your order #10234"):
                self.assertTrue(dc.should_draft(msg, "Re: Your order #10234").ok)

    def test_pure_gratitude_still_does_not_draft(self):
        # The fix must not have swallowed the whole gate.
        for msg in ("thanks", "Thank you so much!", "cheers", "ty"):
            with self.subTest(message=msg):
                self.assertFalse(dc.should_draft(msg).ok)

    def test_should_draft_for_real_questions(self):
        for msg in self.REAL_QUESTIONS:
            with self.subTest(message=msg):
                self.assertTrue(dc.should_draft(msg).ok)

    def test_sensitive_message_always_drafts(self):
        # A gate that suppressed a refund or damage complaint would be a
        # customer-facing failure. These must never be gated out.
        for msg in ["refund", "my order arrived damaged", "this is unacceptable",
                    "I want to speak to a manager"]:
            with self.subTest(message=msg):
                self.assertTrue(dc.should_draft(msg).ok)

    # ── regressions from the code review ────────────────────────────
    def test_the_filler_list_never_swallows_a_complaint(self):
        """It used to list "there", "problem", "do", "yes", "no", "have" and
        "is", so whole complaints and instructions were suppressed."""
        for msg in ["Ok there is a problem", "Ok I have a problem",
                    "Great so this is a problem", "Yes thanks", "No thanks",
                    "Ok do it", "Ok take it", "Yes please",
                    "please cancel my order", "send me a return label",
                    "let me know", "Have you received it", "Is this ok"]:
            with self.subTest(message=msg):
                self.assertTrue(dc.should_draft(msg).ok, msg)

    def test_an_ascii_emoticon_is_content(self):
        """":", "-", "(" and "/" are all inert punctuation, so ":(" dissolved
        to nothing - while the emoji version was correctly kept."""
        for msg in ["thanks :(", "thanks :-(", "thanks :/", "thanks :'(",
                    ":(", ":-(", "ok =/"]:
            with self.subTest(message=msg):
                self.assertTrue(dc.should_draft(msg).ok, msg)
        # ...but a URL is not an emoticon
        self.assertFalse(dc.should_draft("thanks").ok)

    def test_filler_without_a_thanks_anchor_is_not_an_acknowledgement(self):
        """The original gate matched any string of filler words.

        "So much for the help!" is a customer being sarcastic, not a customer
        saying thank you. Suppressing it meant no draft AND no owner alert.
        """
        for msg in ["So much for the help!", "So much for your team.",
                    "well that is just great", "So much for all of it"]:
            with self.subTest(message=msg):
                self.assertTrue(dc.should_draft(msg).ok, msg)

    def test_a_bare_question_mark_or_angry_emoji_still_drafts(self):
        """A customer chasing a reply is not an acknowledgement."""
        for msg in ["?", "??", "?!", "!", "\U0001F621", "\U0001F92C", "\U0001F44E"]:
            with self.subTest(message=repr(msg)):
                self.assertTrue(dc.should_draft(msg).ok, repr(msg))

    def test_a_question_mark_anywhere_makes_it_content(self):
        """The token scan drops punctuation, so a question built only from
        filler and one anchor word read as a bare acknowledgement."""
        for msg in ["Have you received it?", "Was it received?", "Is this ok?",
                    "Received?", "Hi, thanks - are you there?",
                    "Hello? Are you there? Thanks.", "ok?", "thanks, and?"]:
            with self.subTest(message=msg):
                self.assertTrue(dc.should_draft(msg).ok, msg)

    def test_a_non_latin_message_is_never_read_as_an_acknowledgement(self):
        """A Latin-only tokeniser found no words in CJK/Cyrillic/Hebrew, so one
        stray English ack word made the whole sentence invisible."""
        for msg in ["ok \u6211\u8981\u9000\u6b3e",          # ok, I want a refund
                    "ok \u0433\u0434\u0435 \u043c\u043e\u0439 \u0437\u0430\u043a\u0430\u0437",  # ok, where is my order
                    "\u05ea\u05d5\u05d3\u05d4 thanks \u05d0\u05d9\u05e4\u05d4 \u05d4\u05d4\u05d6\u05de\u05e0\u05d4",
                    "\u062a\u0645 \u0627\u0644\u0627\u0633\u062a\u0644\u0627\u0645 thanks"]:
            with self.subTest(message=msg):
                self.assertTrue(dc.should_draft(msg).ok, msg)

    def test_an_angry_emoji_beside_a_word_still_drafts(self):
        """The emoji branch was unreachable once any word was present."""
        for msg in ["Great \U0001F44E", "Thanks \U0001F621", "ok \U0001F92C",
                    "perfect \U0001F4A9"]:
            with self.subTest(message=msg):
                self.assertTrue(dc.should_draft(msg).ok, msg)

    def test_decorated_thumbs_up_is_still_an_acknowledgement(self):
        """Skin tones and variation selectors survived the strip, so most
        mobile thumbs-ups were drafting a reply to an emoji."""
        for msg in ["\U0001F44D\U0001F3FB", "\U0001F44D\U0001F3FD", "\U0001F64F\U0001F3FE",
                    "\U0001F44D\ufe0f", "\u2705\ufe0f", "\u200b"]:
            with self.subTest(message=repr(msg)):
                self.assertFalse(dc.should_draft(msg).ok, repr(msg))

    def test_a_routine_mail_subject_does_not_defeat_the_gate(self):
        """Every Gorgias email ticket has a subject, and any subject with one
        non-ack word forced a draft - so this gate almost never ran. Measured
        at 0 of 15 acknowledgements suppressed with a realistic subject line.
        """
        for subject in ["", "Thanks", "Re: Your Buttons Bebe order #10234",
                        "Re: order confirmation", "(no subject)",
                        "Message from Contact Form", "Re: Thank you",
                        "Ticket #4821", "Your order"]:
            with self.subTest(subject=subject):
                self.assertFalse(dc.should_draft("thanks so much!", subject).ok)

    def test_a_subject_that_really_asks_something_still_drafts(self):
        self.assertTrue(dc.should_draft('', 'Do you have this in 6-9 months?').ok)
        for subject in ('wrong size sent','order not received','Damaged item','Refund request'):
            self.assertFalse(dc.should_draft('thanks!',subject).ok)

    def test_a_one_word_confirmation_is_not_an_acknowledgement(self):
        """"ok" answers "shall I cancel order #10234 before it ships?".
        Suppressing it stored an empty card and the order shipped - while
        "yes"/"sure"/"go ahead" already drafted, so it was incoherent too."""
        for message in ["ok", "okay", "kk", "noted", "received", "got it",
                        "perfect", "great", "understood", "awesome"]:
            with self.subTest(message=message):
                self.assertTrue(dc.should_draft(message).ok)

    def test_gratitude_can_still_stand_alone(self):
        for message in ["thanks", "thank you", "thanks so much", "cheers",
                        "ty", "thx", "thank you so much!"]:
            with self.subTest(message=message):
                self.assertFalse(dc.should_draft(message).ok)

    def test_a_happy_emoticon_is_an_acknowledgement(self):
        for message in ["thanks :)", "thank you :D", "ty :P", "cheers :))",
                        "thanks 8)", "thanks =)", "thanks :o)"]:
            with self.subTest(message=message):
                self.assertFalse(dc.should_draft(message).ok)

    def test_the_reviewer_note_reaches_the_reviewer_but_not_the_customer(self):
        """It carries a safety warning, so it must survive - but on `reason`,
        not in the text a human clicks Send on."""
        reply = "Hi Sarah, thanks for your message."
        warning = ("Note to the reviewer: do NOT send this, the customer was "
                   "already refunded twice and this may be fraud.")
        result = dc.clean_draft(f"{reply}\n\n{warning}")
        self.assertEqual(result.text.strip(), reply)
        self.assertIn("may be fraud", result.removed_note)

    def test_internal_commentary_never_survives_into_the_sendable_body(self):
        for note in [
            "Note to the reviewer: this customer has 3 prior chargebacks.",
            "Internal: customer has 3 prior chargebacks, flag to owner.",
            "FOR INTERNAL USE ONLY — do not send this to the customer.",
            "Confidence: medium. The order number could not be verified.",
            "Notes for the human agent: verify the delivery window first.",
        ]:
            with self.subTest(note=note[:40]):
                result = dc.clean_draft(
                    f"Hi! Thanks for reaching out, we'll sort this out.\n\n{note}")
                self.assertNotIn("chargeback", result.text.lower())
                self.assertNotIn("internal", result.text.lower())
                self.assertIn(note.split(":")[-1].strip()[:20], result.removed_note)

    def test_an_ack_subject_cannot_silence_the_body(self):
        """Concatenating subject and body was a token union: the subject could
        only ever supply a missing anchor and make suppression MORE likely."""
        self.assertTrue(dc.should_draft("Hello?", subject="Thank you").ok)
        self.assertTrue(dc.should_draft("Are you there?", subject="Thanks").ok)
        self.assertTrue(dc.should_draft("where is it", subject="Perfect, thank you!").ok)

    def test_subject_counts_as_content(self):
        """HTML-only mail often arrives with an empty body and a real subject."""
        s = dc.should_draft("", subject="Do you have this in 6-9 months?")
        self.assertTrue(s.ok)
        s = dc.should_draft("thanks!", subject="Where is order #10322?")
        self.assertFalse(s.ok)
        # both empty is still nothing to answer
        self.assertFalse(dc.should_draft("", subject="").ok)
        # a thank-you subject with a thank-you body is still an ack
        self.assertFalse(dc.should_draft("thanks!", subject="Thank you!").ok)

    def test_gate_is_linear_not_exponential(self):
        """The original regex took >1s on 350 bytes and never returned on ~700.

        should_draft() runs synchronously before the first await, so a slow
        call freezes the whole processor - the job timeout cannot interrupt it.
        """
        import time
        payload = "Much appreciated! " * 400 + "Sent from my iPhone"
        self.assertGreater(len(payload), 7000)
        started = time.perf_counter()
        dc.should_draft(payload)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.5,
                        f"should_draft took {elapsed:.3f}s on {len(payload)} bytes")

    def test_gate_is_linear_on_a_long_hostile_message(self):
        import time
        for payload in ("thanks " * 5000, "a" * 50000, ("thank you so much " * 2000)):
            with self.subTest(length=len(payload)):
                started = time.perf_counter()
                dc.should_draft(payload)
                self.assertLess(time.perf_counter() - started, 0.5)


if __name__ == "__main__":
    unittest.main()
