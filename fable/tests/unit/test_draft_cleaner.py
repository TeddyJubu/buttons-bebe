"""Unit tests for the shared draft cleaner (feature F2, TESTING-READINESS T2).

Seeded with the REAL model outputs from the live QA run (testing/results-live.json)
so the leak fixes are exercised against genuine drafts, not toy strings:

  * QA #01 (R01, WISMO), #04 (R04, intl + return), #10 (R10, fabric) — the model
    sometimes repeats the whole draft twice or appends self-commentary
    ("The response above was complete...").
  * QA #19 (E01, empty message) — nothing to answer, must NOT draft.

Also proves the cleaner does NOT touch clean drafts (no false positives): a normal
reply that happens to say "complete" or "note" mid-sentence is left untouched, and
a legitimate escalation internal note is not cut.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

# --- import the module under test (independent of the heavier server fixture) ---
UNIT_DIR = pathlib.Path(__file__).resolve().parent
FABLE_DIR = UNIT_DIR.parents[1]
REPO_ROOT = FABLE_DIR.parent
SERVER_DIR = FABLE_DIR / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app import draft_cleaner as dc  # noqa: E402

RESULTS_LIVE = REPO_ROOT / "testing" / "results-live.json"


# --- helpers ---------------------------------------------------------------
def _norm(s: str) -> str:
    """Whitespace-flatten + lowercase (mirrors the cleaner's own normalisation)."""
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _answer_body(hermes_output: str) -> str:
    """Pull just the drafted reply out of a `RISK/ACTION/ANSWER:` block."""
    marker = "ANSWER:\n"
    i = hermes_output.find(marker)
    return (hermes_output[i + len(marker):] if i != -1 else hermes_output).strip()


def _live_answer(ticket_id: str) -> str:
    data = json.loads(RESULTS_LIVE.read_text())
    for row in data:
        if row.get("id") == ticket_id:
            return _answer_body(row["hermes_output"])
    raise KeyError(f"{ticket_id} not found in {RESULTS_LIVE}")


# Three real DRAFT answers from the live QA run (used as seed content).
REAL_DRAFT_IDS = ["R01", "R04", "R10"]


# ===========================================================================
# 1. Self-talk markers — each one is stripped from the tail of a draft.
# ===========================================================================
_GOOD = "Hi! Thanks for reaching out. Your order usually ships within 24-48 hours."

# (label, marker_line) — one representative line per regex alternative.
SELF_TALK_LINES = [
    ("response-above", "The response above was complete and answers the question."),
    ("previous-complete", "The previous response was already complete."),
    ("prior-complete", "The prior draft is complete."),
    ("above-response", "The above response addresses the customer's question."),
    ("this-reply-complete", "This reply is complete."),
    ("this-response-above", "This response above is now complete."),
    ("i-have-completed", "I have completed the response."),
    ("i-have-finished", "I have now finished this draft."),
    ("note-to-reviewer", "Note to the reviewer: double-check the tone before sending."),
    ("note-to-team", "Note to team: this looks right."),
    ("end-of-response", "End of response"),
    ("end-of-draft-bracket", "[End of draft]"),
    ("as-an-ai", "As an AI, I cannot process the refund myself."),
]


@pytest.mark.parametrize("label,marker", SELF_TALK_LINES, ids=[c[0] for c in SELF_TALK_LINES])
def test_each_self_talk_marker_is_stripped(label, marker):
    draft = f"{_GOOD}\n\n{marker}\n\nSome trailing model chatter."
    res = dc.clean_draft(draft)
    assert res.no_draft is False
    assert "stripped model self-commentary" in res.reasons
    assert res.text == _GOOD
    assert marker not in res.text


def test_marker_with_leading_markdown_is_stripped():
    # markers may arrive prefixed with quote/bullet/heading markup
    draft = f"{_GOOD}\n\n> The response above was complete."
    res = dc.clean_draft(draft)
    assert res.text == _GOOD
    assert "stripped model self-commentary" in res.reasons


def test_draft_that_is_only_self_talk_becomes_no_draft():
    res = dc.clean_draft("The response above was complete.")
    assert res.no_draft is True
    assert res.text == ""
    assert "nothing left after cleaning" in res.reasons


# ===========================================================================
# 2. Duplicated-draft cases — whole-text and paragraph-level.
# ===========================================================================
def test_whole_text_duplicated_blank_line():
    dup = f"{_GOOD}\n\n{_GOOD}"
    res = dc.clean_draft(dup)
    assert "removed duplicated draft body" in res.reasons
    assert res.text == _GOOD


def test_whole_text_duplicated_single_newline():
    # separated by only ONE newline (the old code missed this)
    dup = f"{_GOOD}\n{_GOOD}"
    res = dc.clean_draft(dup)
    assert "removed duplicated draft body" in res.reasons
    assert _norm(res.text) == _norm(_GOOD)


def test_whole_text_tripled():
    dup = f"{_GOOD}\n\n{_GOOD}\n\n{_GOOD}"
    res = dc.clean_draft(dup)
    assert "removed duplicated draft body" in res.reasons
    assert _norm(res.text) == _norm(_GOOD)


def test_paragraph_level_duplication_multi_paragraph():
    body = (
        "Hi there,\n\n"
        "Thanks for reaching out about your order. It usually ships in 24-48 hours "
        "before it leaves our warehouse.\n\n"
        "Warmly,\nButtons Bebe Support"
    )
    res = dc.clean_draft(f"{body}\n\n{body}")
    assert "removed duplicated draft body" in res.reasons
    assert res.text == body
    # the sign-off appears exactly once now
    assert res.text.count("Buttons Bebe Support") == 1


def test_dedup_recovers_original_formatting_exactly():
    body = "Line one of the reply here.\n\n- bullet a\n- bullet b\n\nThanks so much!"
    res = dc.clean_draft(f"{body}\n\n{body}")
    assert res.text == body  # newlines/bullets preserved, not flattened


# ===========================================================================
# 3. Seeded with REAL leaked-style drafts from testing/results-live.json.
# ===========================================================================
@pytest.mark.parametrize("ticket_id", REAL_DRAFT_IDS)
def test_real_answer_duplicated_is_collapsed(ticket_id):
    answer = _live_answer(ticket_id)
    assert len(answer) > 40  # sanity: it's a real, substantial draft
    res = dc.clean_draft(f"{answer}\n\n{answer}")
    assert res.no_draft is False
    assert "removed duplicated draft body" in res.reasons
    assert _norm(res.text) == _norm(answer)
    # the duplicate is truly gone (result ~half the doubled input)
    assert len(res.text) < len(answer) * 1.2


@pytest.mark.parametrize("ticket_id", REAL_DRAFT_IDS)
def test_real_answer_with_appended_self_talk_is_trimmed(ticket_id):
    answer = _live_answer(ticket_id)
    leaked = f"{answer}\n\nThe response above was complete and ready for review."
    res = dc.clean_draft(leaked)
    assert "stripped model self-commentary" in res.reasons
    assert _norm(res.text) == _norm(answer)


def test_real_answer_duplicated_then_self_talk():
    answer = _live_answer("R01")
    leaked = f"{answer}\n\n{answer}\n\nThe response above was complete."
    res = dc.clean_draft(leaked)
    assert "stripped model self-commentary" in res.reasons
    assert "removed duplicated draft body" in res.reasons
    assert _norm(res.text) == _norm(answer)


@pytest.mark.parametrize("ticket_id", REAL_DRAFT_IDS)
def test_real_clean_answer_passes_through_untouched(ticket_id):
    # A genuine, non-leaked draft must be returned byte-for-byte with no reasons.
    answer = _live_answer(ticket_id)
    res = dc.clean_draft(answer)
    assert res.no_draft is False
    assert res.reasons == []
    assert res.text == answer


# ===========================================================================
# 4. No false positives — clean drafts pass through UNCHANGED.
# ===========================================================================
def test_clean_reply_saying_complete_is_not_cut():
    draft = "Hi! Your order is complete and on its way. Thanks so much for your patience!"
    res = dc.clean_draft(draft)
    assert res.text == draft
    assert res.reasons == []


def test_clean_reply_saying_note_is_not_cut():
    draft = "Hi! Note that processing takes 24-48 hours before your order ships. Thanks!"
    res = dc.clean_draft(draft)
    assert res.text == draft
    assert res.reasons == []


def test_legitimate_escalation_internal_note_is_not_cut():
    # These phrasings look self-referential but are real escalation content.
    note = (
        "Internal note for human review:\n"
        "Customer is requesting a refund on order #10322. Do not promise money.\n"
        "Notes for the human agent: verify the delivery date and 7-day window first."
    )
    res = dc.clean_draft(note)
    assert res.text == note
    assert res.reasons == []


def test_two_different_paragraphs_are_not_deduped():
    draft = (
        "Hi! Yes, we ship to Canada; the rate shows at checkout.\n\n"
        "Customs and duties are the customer's responsibility. Thanks!"
    )
    res = dc.clean_draft(draft)
    assert res.text == draft
    assert res.reasons == []


def test_short_repeated_content_is_not_collapsed():
    # below the duplication length floor -> left alone (harmless, avoids over-cut)
    draft = "Yes.\n\nYes."
    res = dc.clean_draft(draft)
    assert res.text == draft
    assert "removed duplicated draft body" not in res.reasons


# ===========================================================================
# 5. Empty / whitespace drafts -> no_draft.
# ===========================================================================
@pytest.mark.parametrize("bad", ["", "   ", "\n\n", "  \n \t ", None])
def test_empty_or_whitespace_draft_is_no_draft(bad):
    res = dc.clean_draft(bad)
    assert res.no_draft is True
    assert res.text == ""


def test_clean_draft_is_idempotent():
    leaked = f"{_GOOD}\n\n{_GOOD}\n\nThe response above was complete."
    once = dc.clean_draft(leaked).text
    twice = dc.clean_draft(once)
    assert twice.text == once
    assert twice.reasons == []  # already clean the second time


# ===========================================================================
# 6. should_draft — gate on the CUSTOMER message.
# ===========================================================================
@pytest.mark.parametrize("msg", [
    "",
    "  ",
    "!",
    "...",
    "thanks",
    "Thanks!",
    "Thank you!!",
    "thank you so much!",   # extended ack still recognised
    "ty",
    "ok",
    "\U0001F44D",           # 👍
    "\U0001F64F",           # 🙏
    None,
])
def test_should_not_draft_for_no_content(msg):
    s = dc.should_draft(msg)
    assert s.ok is False
    assert s.reason  # a human-readable reason is always given


@pytest.mark.parametrize("msg", [
    "Where is my order #BB1015?",
    "I want a refund for order #10322.",
    "Do you ship to Canada and how much?",
    "thanks, but where is my order?",   # thanks + a real question -> draft
    "Can I change my shipping address?",
])
def test_should_draft_for_real_questions(msg):
    s = dc.should_draft(msg)
    assert s.ok is True


def test_empty_customer_message_qa19():
    # QA #19: the empty-message ticket (E01) — nothing to answer.
    from_live = json.loads(RESULTS_LIVE.read_text())
    e01 = next(r for r in from_live if r["id"] == "E01")
    assert e01["message"] == ""  # confirm we're testing the real empty case
    assert dc.should_draft(e01["message"]).ok is False
