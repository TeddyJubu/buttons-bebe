"""Unit tests for the deterministic risk classifier (TESTING-STRATEGY §2.1).

Documents the chosen behaviour:
  * trigger words are matched case-insensitively as **substrings** (so
    "refundable" DOES match "refund" — a deliberate over-flag; a false positive
    here is safe because it only routes to a careful, no-promises draft);
  * "!!!" (3+ exclamation marks) flags;
  * >= 6 ALL-CAPS tokens (2+ letters) flags "shouting";
  * clean / mixed-case messages are low risk.
"""
import pytest


@pytest.fixture
def risk(server_modules):
    return server_modules["risk"]


# --- every trigger word flags ----------------------------------------------
@pytest.mark.parametrize("word", [
    "refund", "chargeback", "charge back", "dispute", "damaged", "broken",
    "wrong item", "wrong order", "missing", "never arrived", "didn't arrive",
    "hasn't arrived", "lost", "scam", "fraud", "lawyer", "attorney",
    "legal action", "sue",
])
def test_each_trigger_word_is_sensitive(risk, word):
    level, reason = risk.classify(f"Hello, my order was {word} and I am upset.")
    assert level == "sensitive"
    assert word in reason


def test_trigger_word_is_case_insensitive(risk):
    assert risk.classify("I want a REFUND now")[0] == "sensitive"
    assert risk.classify("My package is DaMaGeD")[0] == "sensitive"


def test_reason_names_the_matched_word(risk):
    level, reason = risk.classify("this item is broken")
    assert level == "sensitive"
    assert reason == "mentions 'broken'"


# --- angry signals ----------------------------------------------------------
def test_triple_exclamation_flags(risk):
    level, reason = risk.classify("Where is my stuff!!!")
    assert level == "sensitive"
    assert "exclamation" in reason


def test_double_exclamation_does_not_flag(risk):
    # only 3+ exclamation marks count as an angry signal
    assert risk.classify("Hello!! Any update?")[0] == "low"


def test_all_caps_six_words_flags(risk):
    level, reason = risk.classify("THIS IS COMPLETELY UNACCEPTABLE AND VERY DISAPPOINTING")
    assert level == "sensitive"
    assert "caps" in reason or "shouting" in reason


def test_five_caps_words_do_not_flag(risk):
    # 5 all-caps tokens is under the >=6 threshold
    assert risk.classify("THIS IS NOT GOOD OKAY")[0] == "low"


# --- low-risk / clean messages ---------------------------------------------
def test_clean_message_is_low(risk):
    level, reason = risk.classify("Hi! Do you ship to Canada? Thank you.")
    assert level == "low"
    assert reason is None


def test_mixed_case_order_question_is_low(risk):
    assert risk.classify("Where is my order #BB1015?")[0] == "low"


def test_empty_and_none_are_low(risk):
    assert risk.classify("")[0] == "low"
    assert risk.classify(None)[0] == "low"


# --- documented word-boundary behaviour ------------------------------------
def test_substring_match_is_intentional(risk):
    # "refundable" contains "refund" -> flagged. Documented over-flag: safe.
    assert risk.is_sensitive("Is this product refundable?") is True


def test_is_sensitive_helper_agrees_with_classify(risk):
    assert risk.is_sensitive("I need a refund") is True
    assert risk.is_sensitive("just a normal question") is False
