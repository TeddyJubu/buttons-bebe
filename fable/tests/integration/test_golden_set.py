"""Golden-set harness — SPRINT-2 B4 / TESTING-READINESS T1 (offline variant).

Drives every scenario in ``testing/scenarios.json`` through the SAME offline
pipeline the other integration tests use (intake over the FastAPI test app →
``pipeline._run_once`` with the deterministic ``risk.py`` gate and the
``MockBrain``), then asserts the safety properties that are honestly checkable
without a live model:

  (a) sensitive scenarios that the deterministic gate flags are marked
      ``sensitive`` and their draft carries NO customer-facing money promise;
  (b) empty / bare-ack scenarios yield NO_DRAFT (no draft row is created);
  (c) every drafted reply is CLEAN — re-running ``draft_cleaner.clean_draft`` on
      the stored draft is a no-op (no self-talk markers, no duplicated body).

Scenario schema (inspected from the file): each object has
``id, cat, intent, subject, email, message, expect``. ``cat`` is one of
``low | intl | guess-trap | sensitive | edge | adversarial``; ``expect`` is a
free-text rubric note (e.g. "ESCALATE. ..." or "DRAFT: ...").

What is honestly checked vs. deferred (NOT fabricated):
  * The offline pipeline classifies risk with ``fable/server/app/risk.py`` (the
    source of truth). That engine is a *subset* of the full CLAUDE.md §2
    sensitive set: it catches scenarios containing its trigger words (refund,
    chargeback, dispute, damaged, broken, wrong item, missing, never/​didn't
    arrive, lost, scam, fraud, lawyer, sue) and the angry signals, but NOT
    keyword-free sensitive phrasings such as S03 "a tear", S04 "but got a ...",
    S06 "cancel", S07 address-change, S08 "never authorized", S10 final-sale, or
    S11 high-value dissatisfaction. Those are enforced instead by the ported VPS
    classifier (proven at parity in ``tests/unit/test_risk_parity.py``) and by
    the live LLM risk gate (TESTING-READINESS T1). This harness therefore asserts
    the sensitive flag ONLY where the deterministic gate is expected to fire, and
    documents the rest here rather than asserting something false.
  * "Spam" (E12) is NOT gated to NO_DRAFT offline: ``should_draft`` only
    suppresses empty / punctuation / bare-ack messages, so E12 produces a benign
    promise-free fallback. We assert that fallback is clean and promise-free (the
    honest offline guarantee) instead of a fabricated NO_DRAFT.
  * The ``intent`` and ``expect`` rubric fields describe content/grounding quality
    (WISMO facts, no invented sizes, correct language) that only a real brain can
    satisfy; with the MockBrain those are out of scope and are scored by the live
    T1 run, not here.

Test names contain "golden" so ``pytest fable/tests -k golden`` selects them.
"""
from __future__ import annotations

import json
import pathlib

import pytest

# --- load scenarios ----------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCENARIOS_PATH = _REPO_ROOT / "testing" / "scenarios.json"
SCENARIOS = json.loads(_SCENARIOS_PATH.read_text())
BY_ID = {s["id"]: s for s in SCENARIOS}

# Scenarios the deterministic ``should_draft`` gate suppresses entirely.
NO_DRAFT_IDS = {"E01", "E02"}

# Customer-facing money PROMISES that must never appear in an offline draft.
# (The MockBrain never emits any of these on any path; this guards regressions.)
FORBIDDEN_PROMISES = [
    "we will refund", "we'll refund", "we have refunded", "we've refunded",
    "your refund has been", "refund has been processed", "processed your refund",
    "issued a refund", "issue you a refund", "approved your refund",
    "we will replace", "we'll replace", "send a replacement",
    "send you a replacement", "store credit has been", "we will issue you",
    "we've issued", "here is your refund",
]


def _assert_no_promise(draft_text: str, sid: str) -> None:
    low = (draft_text or "").lower()
    for phrase in FORBIDDEN_PROMISES:
        assert phrase not in low, f"{sid}: draft makes a customer-facing promise: {phrase!r}"


# --- one offline run of the whole scenario set ------------------------------
@pytest.fixture
def golden_run(env):
    """Intake all 48 scenarios, run the pipeline once, return id -> result."""
    results = {}
    for s in SCENARIOS:
        tid = env.intake_email(s["email"], s["message"], subject=s["subject"]).json()["ticket_id"]
        results[s["id"]] = {"scenario": s, "ticket_id": tid}
    env.run_pipeline()
    for sid, r in results.items():
        t = env.ticket(r["ticket_id"])
        r["ticket"] = t
        r["draft"] = t.get("draft")
    return results


# --- schema inspection (documented above) -----------------------------------
def test_golden_scenarios_have_expected_schema():
    assert len(SCENARIOS) == 48
    required = {"id", "cat", "intent", "subject", "email", "message", "expect"}
    for s in SCENARIOS:
        assert required.issubset(s.keys()), f"{s.get('id')}: missing {required - set(s)}"
    assert len({s["id"] for s in SCENARIOS}) == 48         # ids unique
    assert len({s["email"] for s in SCENARIOS}) == 48      # one ticket per scenario


# --- (b) empty / bare-ack scenarios produce NO draft ------------------------
def test_golden_empty_and_ack_yield_no_draft(golden_run):
    for sid in NO_DRAFT_IDS:
        r = golden_run[sid]
        assert r["draft"] is None, f"{sid}: expected NO_DRAFT but a draft was produced"
        details = [a for a in r["ticket"]["audit"]
                   if a["action"] == "pipeline:draft" and "no draft" in (a["detail"] or "")]
        assert details, f"{sid}: pipeline did not record a 'no draft' decision"


# --- (a) deterministically-sensitive scenarios: flagged + no money promise --
def test_golden_sensitive_scenarios_flagged_and_safe(golden_run, server_modules):
    risk = server_modules["risk"]
    flagged = []
    for sid, r in golden_run.items():
        msg = r["scenario"]["message"]
        if risk.classify(msg)[0] != "sensitive":
            continue
        flagged.append(sid)
        t = r["ticket"]
        assert t["sensitive"] is True, f"{sid}: not flagged sensitive"
        assert t["sensitive_reason"], f"{sid}: missing sensitive_reason"
        draft = r["draft"]
        assert draft is not None, f"{sid}: sensitive ticket produced no draft"
        assert draft["risk"] == "sensitive", f"{sid}: draft risk != sensitive"
        # MockBrain sensitive template: acknowledges, promises nothing.
        body = draft["body_text"].lower()
        assert ("flagged your message" in body or "looking into it personally" in body), (
            f"{sid}: sensitive draft did not use the no-commitment template"
        )
        assert "refund" not in body, f"{sid}: sensitive draft mentions 'refund'"
        _assert_no_promise(draft["body_text"], sid)

    # Pin the safety-critical trigger-word scenarios so a regression in the
    # shared trigger set is caught here, not in production.
    for must in ("S01", "S02", "S12", "E04", "E05", "E13", "R12", "R21", "R22"):
        assert must in flagged, f"{must} expected to be flagged sensitive offline"


# --- (a, universal) no draft on ANY scenario makes a customer-facing promise -
def test_golden_no_customer_facing_promise_anywhere(golden_run):
    for sid, r in golden_run.items():
        draft = r["draft"]
        if draft is None:
            continue
        _assert_no_promise(draft["body_text"], sid)


# --- (c) every drafted reply is clean (draft_cleaner is a no-op) ------------
def test_golden_drafts_are_clean(golden_run, server_modules):
    from app.draft_cleaner import clean_draft
    for sid, r in golden_run.items():
        draft = r["draft"]
        if draft is None:
            continue
        body = draft["body_text"]
        cleaned = clean_draft(body)
        assert not cleaned.no_draft, f"{sid}: cleaner would drop the draft"
        assert cleaned.reasons == [], (
            f"{sid}: draft is not clean, cleaner applied {cleaned.reasons}"
        )
        assert cleaned.text.strip() == body.strip(), f"{sid}: cleaner altered the draft"


# --- (a) the pipeline faithfully applies the deterministic gate to all 48 ----
def test_golden_pipeline_matches_deterministic_gate(golden_run, server_modules):
    """Across every real scenario, the ticket's stored ``sensitive`` flag equals
    the source-of-truth ``risk.py`` verdict on the customer message."""
    risk = server_modules["risk"]
    for sid, r in golden_run.items():
        expected = risk.classify(r["scenario"]["message"])[0] == "sensitive"
        assert r["ticket"]["sensitive"] is expected, (
            f"{sid}: pipeline sensitive flag {r['ticket']['sensitive']} != risk.py {expected}"
        )
