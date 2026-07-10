"""Risk-engine parity — TESTING-READINESS T5 (feature F1: the port did not drift).

Feeds the SAME inputs through BOTH deterministic risk engines and proves they
agree on the sensitive / not-sensitive flag:

  * ``fable/server/app/risk.py``            — the SOURCE OF TRUTH (used by the
    Fable pipeline). Substring word match + angry signals; returns
    ``(level, reason)`` with ``level in {"low","sensitive"}``.
  * ``deploy/vps-patches/classifier.py``    — the PORT that runs on the live VPS
    processor. Word-boundary match over a larger phrase set; returns a
    ``Classification`` (``.sensitive``, ``.escalate``, ``.category``, ``.reason``,
    ``.reasons``, ``.matched``).

Why the two reason FORMATS differ (and how we map them):
  ``risk.py`` names the matched trigger word — ``"mentions 'refund'"``. The port
  emits a topic ``category`` (``"refund"``) plus a human ``reason``. So for word
  triggers we assert a stable *category mapping* (risk word -> port category);
  for the two structural "angry" signals both engines emit the IDENTICAL reason
  string, so we assert exact equality there.

Scope of parity (honest, not fabricated):
  The port is a deliberate SUPERSET of the source of truth — it additionally
  flags CLAUDE.md §2 categories that ``risk.py`` does not (e.g. "cancel", a bare
  "tear", "I ordered X but got Y", "speak to a manager"). Those port-only
  additions are NOT drift and are out of scope for a *parity* test. This test
  therefore exercises the SHARED contract: every trigger word in ``risk.py``'s
  ``SENSITIVE_WORDS`` plus both angry signals, and a set of clean/benign and
  word-boundary-guard messages that BOTH engines must leave unflagged.

The one real within-contract divergence found while writing this test — the bare
word "lost" (``risk.py`` flags it as a substring; the port only caught
"lost package"/"lost in transit") — was fixed in the PORT (added "lost" to its
``not_delivered`` rule) rather than by loosening this test, since ``risk.py`` is
the source of truth.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

# --- load the ported VPS classifier from its file path ----------------------
# deploy/ is not a package, so import it directly. The module MUST be registered
# in sys.modules before exec_module(), otherwise its @dataclass (which resolves
# annotations via sys.modules[cls.__module__]) fails to build.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_CLASSIFIER_PATH = _REPO_ROOT / "deploy" / "vps-patches" / "classifier.py"


def _load_vps_classifier():
    spec = importlib.util.spec_from_file_location(
        "vps_classifier_under_test", _CLASSIFIER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vps_classifier_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


VPS = _load_vps_classifier()


@pytest.fixture
def risk(server_modules):
    return server_modules["risk"]


# --- shared contract: every risk.py trigger word ----------------------------
# (message, risk.py word named in its reason, expected port category)
# Each message is crafted so the trigger appears as a clean whole word, so the
# substring engine (risk.py) and the word-boundary engine (port) both fire.
SENSITIVE_WORDS = [
    ("I want a full refund for my order.",            "refund",        "refund"),
    ("I am going to file a chargeback with my bank.", "chargeback",    "chargeback"),
    ("I'll charge back the payment to my card.",      "charge back",   "chargeback"),
    ("I want to dispute this charge.",                "dispute",       "dispute"),
    ("The dress arrived damaged.",                    "damaged",       "damaged_item"),
    ("The zipper is broken and will not move.",       "broken",        "damaged_item"),
    ("I received the wrong item in my package.",      "wrong item",    "wrong_item"),
    ("You sent me the wrong order entirely.",         "wrong order",   "wrong_item"),
    ("An accessory is missing from my order.",        "missing",       "missing_item"),
    ("My package never arrived.",                     "never arrived", "not_delivered"),
    ("My order still hasn't arrived.",                "hasn't arrived", "not_delivered"),
    ("The tracking says it didn't arrive.",           "didn't arrive", "not_delivered"),
    ("This whole thing is a total scam.",             "scam",          "fraud"),
    ("I am certain this is fraud.",                    "fraud",         "fraud"),
    ("I am contacting my lawyer about this.",          "lawyer",        "legal"),
    ("My attorney will be in touch with you.",         "attorney",      "legal"),
    ("I will be taking legal action.",                 "legal action",  "legal"),
    ("I am going to sue your company.",                "sue",           "legal"),
    ("This is about a lost package from last week.",   "lost",          "not_delivered"),
]

# --- structural "angry" signals: both engines emit the SAME reason string ----
ANGRY_SIGNALS = [
    ("Where is my order!!!",                       "excessive exclamation (!!!)"),
    ("PLEASE HELP ME FIND MY ORDER RIGHT AWAY NOW", "shouting (all-caps message)"),
]

# --- benign / boundary-guard messages both engines must leave unflagged ------
NORMAL_INPUTS = [
    "Do you ship to Canada and how much?",
    "What size bodysuit should I order for a 4 month old?",
    "What brands do you carry?",
    "Can I pick up locally instead of shipping?",
    "Thanks so much, the dress is adorable!",
    "I took a trip to the store and loved it.",     # 'trip' must not trip 'rip'
    "I scanned the QR code on the box.",            # 'scanned' != 'scam'/'cancel'
    "Please discard the old invoice.",              # 'discard' != 'card'
]


# --- sensitive word triggers: both flag + reason/category map consistently ---
@pytest.mark.parametrize("text,word,category", SENSITIVE_WORDS,
                         ids=[w for _, w, _ in SENSITIVE_WORDS])
def test_risk_parity_sensitive_word_triggers(risk, text, word, category):
    level, reason = risk.classify(text)
    verdict = VPS.classify(text)

    # 1. Both engines agree the message is SENSITIVE.
    assert level == "sensitive", f"risk.py did not flag: {text!r}"
    assert verdict.sensitive is True, f"port did not flag: {text!r}"

    # 2. The port's safety invariant: sensitive => escalate => never auto-draftable.
    assert verdict.escalate is True
    assert verdict.auto_draft_allowed is False

    # 3. Reasons map consistently: risk.py names the matched word; the port
    #    classifies it into the mapped topic category.
    assert word in reason, f"risk.py reason {reason!r} does not name {word!r}"
    assert verdict.category == category, (
        f"{text!r}: port category {verdict.category!r} != expected {category!r}"
    )


# --- angry signals: identical reason string on both engines -----------------
@pytest.mark.parametrize("text,expected_reason", ANGRY_SIGNALS,
                         ids=["exclaim", "shouting"])
def test_risk_parity_angry_signals(risk, text, expected_reason):
    level, reason = risk.classify(text)
    verdict = VPS.classify(text)

    assert level == "sensitive"
    assert verdict.sensitive is True
    assert verdict.auto_draft_allowed is False
    # risk.py returns exactly this reason string...
    assert reason == expected_reason
    # ...and the port emits the very same string among its reasons.
    assert expected_reason in verdict.reasons


# --- benign messages: both engines agree NOT sensitive ----------------------
@pytest.mark.parametrize("text", NORMAL_INPUTS)
def test_risk_parity_normal_inputs_not_sensitive(risk, text):
    level, reason = risk.classify(text)
    verdict = VPS.classify(text)

    assert level == "low", f"risk.py over-flagged benign: {text!r} ({reason})"
    assert verdict.sensitive is False, (
        f"port over-flagged benign: {text!r} (matched {verdict.matched})"
    )
    # benign, non-empty message stays auto-draftable in the port
    assert verdict.auto_draft_allowed is True


# --- coverage guard: at least 20 sensitive + 5 normal inputs (T5) -----------
def test_risk_parity_input_coverage():
    assert len(SENSITIVE_WORDS) + len(ANGRY_SIGNALS) >= 20
    assert len(NORMAL_INPUTS) >= 5


# --- the two engines never disagree on the flag across the whole set --------
def test_risk_parity_no_flag_disagreement(risk):
    sensitive = [t for t, _, _ in SENSITIVE_WORDS] + [t for t, _ in ANGRY_SIGNALS]
    for text in sensitive:
        assert (risk.classify(text)[0] == "sensitive") == VPS.classify(text).sensitive, (
            f"flag disagreement on sensitive input: {text!r}"
        )
    for text in NORMAL_INPUTS:
        assert (risk.classify(text)[0] == "sensitive") == VPS.classify(text).sensitive, (
            f"flag disagreement on normal input: {text!r}"
        )
