#!/usr/bin/env python3
"""
deploy/vps-patches/classifier.py — deterministic risk gate for the VPS processor.

Origin: ported from fable/server/app/risk.py @ Fable_buttonsbebe
        (Sprint 2, Stream V / item V1). Keep the two in parity — see the
        Stream T risk-engine parity test (TESTING-READINESS.md §2, T5).

Replaces the STUB `processor/classifier.py` (which returns NORMAL for everything —
DEV-ISSUES #3). This is a code-level safety net that runs BEFORE and AFTER the
model, so a bad model day can never let a sensitive ticket auto-draft.

It is a port of Fable's `fable/server/app/risk.py` (the shared, unit-tested risk
engine — IMPROVEMENT-PLAN F1) re-expressed in the processor's documented
IMMEDIATE / HIGH / NORMAL vocabulary, and extended with the owner's Core-Rule
sensitive categories from CLAUDE.md §2 (wrong / damaged / missing items,
cancellations).

SAFETY MODEL (CLAUDE.md §2):
  Refunds, chargebacks, disputes, damaged/wrong/missing items, cancellations, and
  angry customers are SENSITIVE. They are flagged for a human and are NEVER
  auto-draftable. The gate is deterministic and can only ESCALATE — it never
  clears a flag. Over-flagging is deliberate and safe: a false escalation costs a
  human one glance; a wrong auto-draft on a refund/chargeback is customer-facing
  and expensive.

Priority tiers:
  IMMEDIATE — ping the owner now (chargebacks, bank disputes, fraud/scam, legal
              threats, "speak to a manager"/demands, unauthorised charges).
              This is what `twilio_notifier.py` alerts on (CLAUDE.md §8).
  HIGH      — escalate to a human queue, no owner ping (refunds, cancellations,
              damaged/defective, wrong item, missing/never-arrived, final-sale
              exceptions, plain anger signals like "!!!" or shouting).
  NORMAL    — benign; auto-draftable (the model + KB still decide the reply).

PUBLIC API:
  classify(text, subject=None) -> Classification
  classify_priority(text, subject=None) -> "IMMEDIATE" | "HIGH" | "NORMAL"
  is_sensitive(text, subject=None) -> bool

Stdlib only (re, dataclasses). No network, no LLM, no Gorgias/Shopify calls.
Run directly for the labelled self-test:  python3 classifier.py
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# --- priority levels (ordered: higher index = more urgent) ------------------
IMMEDIATE = "IMMEDIATE"
HIGH = "HIGH"
NORMAL = "NORMAL"

_RANK = {NORMAL: 0, HIGH: 1, IMMEDIATE: 2}


def _more_urgent(a: str, b: str) -> str:
    """Return the more urgent of two priorities (used to escalate, never lower)."""
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


# ---------------------------------------------------------------------------
# Trigger tables. Each entry: (category, [phrases], human reason).
# Phrases are matched case-insensitively on WORD BOUNDARIES, so "trip" does not
# trip "rip", "discard" does not trip "card", and "scanned" does not trip
# "scam"/"cancel". Multi-word phrases match across normalised whitespace.
# ---------------------------------------------------------------------------

# IMMEDIATE — the owner should hear about these right away.
_IMMEDIATE_RULES = [
    ("chargeback", [
        "chargeback", "charge back", "charge-back", "charged back",
        "reverse the charge", "reverse the payment",
    ], "chargeback / payment reversal"),
    ("dispute", [
        "dispute", "disputing", "disputed",
        "dispute the charge", "dispute this with my bank", "file a dispute",
        "contest the charge", "unauthorized charge", "unauthorised charge",
        "unauthorized transaction", "unauthorised transaction",
        "did not authorize", "didn't authorize", "never authorized",
    ], "payment / bank dispute"),
    ("fraud", [
        "fraud", "fraudulent", "scam", "scammed", "scammer", "rip off",
        "ripoff", "ripped me off", "stole my money", "you stole", "theft",
    ], "fraud / scam accusation"),
    ("legal", [
        "lawyer", "attorney", "legal action", "legal counsel", "sue", "suing",
        "lawsuit", "small claims", "cease and desist", "bbb",
        "better business bureau", "consumer protection", "file a complaint",
    ], "legal / regulatory threat"),
    ("escalation_request", [
        "speak to a manager", "speak to your manager", "talk to a manager",
        "get me a manager", "want a manager", "your supervisor",
        "speak to a supervisor", "i demand", "this is unacceptable", "unacceptable",
        "worst company",
    ], "demand for a manager / escalation"),
]

# HIGH — escalate to a human, but no owner ping.
_HIGH_RULES = [
    ("refund", [
        "refund", "refunded", "refunding", "reimburse", "reimbursement",
        "money back", "my money back",
    ], "refund / money-back request"),
    ("cancellation", [
        "cancel", "canceled", "cancelled", "canceling", "cancelling",
        "cancellation", "cancel my order", "stop my order",
    ], "cancellation request"),
    ("wrong_item", [
        "wrong item", "wrong order", "wrong product", "wrong size sent",
        "wrong colour sent", "wrong color sent", "got the wrong",
        "sent the wrong", "received the wrong", "not what i ordered",
        "different item", "isn't what i ordered", "wasn't what i ordered",
        # "I ordered X but got Y" phrasings that carry no explicit "wrong" word
        "but got a", "but got the", "but received a", "but i got",
        "instead i got", "instead of the",
    ], "wrong item received"),
    ("damaged_item", [
        "damaged", "defective", "broken", "cracked", "shattered", "torn",
        "ripped", "frayed", "stained", "fell apart",
        "a rip", "a tear", "a hole", "a stain", "tear on", "tear in", "rip in",
        "hole in", "stain on", "arrived broken", "arrived damaged",
        "zipper is broken", "seam ripped",
    ], "damaged / defective item"),
    ("not_delivered", [
        "never arrived", "never came", "never received", "didn't arrive",
        "did not arrive", "hasn't arrived", "has not arrived", "not delivered",
        # Bare "lost" mirrors the source-of-truth risk.py trigger (Fable's
        # SENSITIVE_WORDS flags any "lost"); keep the port in parity with it.
        "lost", "lost package", "lost in transit", "stolen package",
        "marked delivered but",
    ], "possible lost / undelivered order"),
    ("missing_item", [
        "missing", "without the", "didn't come with", "did not come with",
        "not included", "wasn't included", "left out", "supposed to include",
    ], "missing item / accessory"),
    ("final_sale_exception", [
        "final sale exception", "return a final sale", "exchange a final sale",
        "exception for final sale", "exception on final sale",
        "return my final sale",
    ], "final-sale exception request"),
]

# --- angry signals (structural, not keyword) --------------------------------
_EXCLAIM_RE = re.compile(r"!{3,}")             # 3+ exclamation marks in a row
_CAPS_WORD_RE = re.compile(r"\b[A-Z]{2,}\b")   # ALL-CAPS words (2+ letters)
_WS_RE = re.compile(r"\s+")


def _compile(phrases: List[str]) -> re.Pattern:
    body = "|".join(re.escape(p) for p in phrases)
    return re.compile(r"\b(?:" + body + r")\b", re.IGNORECASE)


# Pre-compile every rule's regex once at import.
_IMMEDIATE = [(cat, _compile(ph), reason) for cat, ph, reason in _IMMEDIATE_RULES]
_HIGH = [(cat, _compile(ph), reason) for cat, ph, reason in _HIGH_RULES]


@dataclass
class Classification:
    """Verdict for one ticket.

    priority            — "IMMEDIATE" | "HIGH" | "NORMAL".
    sensitive           — True for the always-escalate set (implies escalate).
    escalate            — True => route to a human, never auto-draft.
    auto_draft_allowed  — True ONLY when (not sensitive) and (not escalate).
    category            — best-guess topic (e.g. "refund", "chargeback").
    reason              — short human-readable reason for the top match.
    reasons             — every rule that fired (audit trail).
    matched             — the literal phrases that tripped the rules.
    """
    priority: str = NORMAL
    sensitive: bool = False
    escalate: bool = False
    auto_draft_allowed: bool = True
    category: str = "general"
    reason: str = ""
    reasons: List[str] = field(default_factory=list)
    matched: List[str] = field(default_factory=list)

    def recompute(self) -> None:
        """Re-derive the auto-draft gate from the safety invariant. The ONLY
        place auto_draft_allowed is set True."""
        self.auto_draft_allowed = (not self.sensitive) and (not self.escalate)

    def as_dict(self) -> dict:
        return {
            "priority": self.priority,
            "sensitive": self.sensitive,
            "escalate": self.escalate,
            "auto_draft_allowed": self.auto_draft_allowed,
            "category": self.category,
            "reason": self.reason,
            "reasons": list(self.reasons),
            "matched": list(self.matched),
        }


def _normalize(text: Optional[str]) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", str(text)).strip()


def classify(text: str, subject: Optional[str] = None) -> Classification:
    """Classify a ticket message (subject is folded into the haystack because
    refund/chargeback wording often lives in the subject line)."""
    haystack = (_normalize(subject) + " " + _normalize(text)).strip()
    result = Classification()

    # --- empty / unintelligible -> conservative: escalate for a human glance --
    if len(re.sub(r"[^a-zA-Z]", "", haystack)) < 2:
        result.priority = HIGH
        result.escalate = True
        result.sensitive = False           # not "sensitive", but not draftable
        result.category = "unknown"
        result.reason = "empty or unintelligible message"
        result.reasons.append(result.reason)
        result.recompute()
        return result

    first_category = None

    # --- IMMEDIATE rules -----------------------------------------------------
    for category, pat, reason in _IMMEDIATE:
        hits = pat.findall(haystack)
        if hits:
            result.sensitive = True
            result.escalate = True
            result.priority = _more_urgent(result.priority, IMMEDIATE)
            if first_category is None:
                first_category = category
                result.reason = reason
            if reason not in result.reasons:
                result.reasons.append(reason)
            for h in hits:
                if h.lower() not in [m.lower() for m in result.matched]:
                    result.matched.append(h)

    # --- HIGH rules ----------------------------------------------------------
    for category, pat, reason in _HIGH:
        hits = pat.findall(haystack)
        if hits:
            result.sensitive = True
            result.escalate = True
            result.priority = _more_urgent(result.priority, HIGH)
            if first_category is None:
                first_category = category
                result.reason = reason
            if reason not in result.reasons:
                result.reasons.append(reason)
            for h in hits:
                if h.lower() not in [m.lower() for m in result.matched]:
                    result.matched.append(h)

    # --- angry signals (only raise, never lower) -----------------------------
    if _EXCLAIM_RE.search(haystack):
        result.sensitive = True
        result.escalate = True
        result.priority = _more_urgent(result.priority, HIGH)
        r = "excessive exclamation (!!!)"
        if first_category is None:
            first_category = "angry"
            result.reason = r
        if r not in result.reasons:
            result.reasons.append(r)
    if len(_CAPS_WORD_RE.findall(haystack)) >= 6:
        result.sensitive = True
        result.escalate = True
        result.priority = _more_urgent(result.priority, HIGH)
        r = "shouting (all-caps message)"
        if first_category is None:
            first_category = "angry"
            result.reason = r
        if r not in result.reasons:
            result.reasons.append(r)

    result.category = first_category or "general"
    result.recompute()
    return result


def classify_priority(text: str, subject: Optional[str] = None) -> str:
    """Convenience: just the IMMEDIATE / HIGH / NORMAL label."""
    return classify(text, subject).priority


def is_sensitive(text: str, subject: Optional[str] = None) -> bool:
    return classify(text, subject).sensitive


# ---------------------------------------------------------------------------
# Self-test — labelled cases + the safety invariant.
# ---------------------------------------------------------------------------
def _selftest() -> Tuple[bool, int]:
    # (message, expected_priority, expected_sensitive)
    CASES = [
        # ---- IMMEDIATE (owner ping) ----------------------------------------
        ("I'm filing a chargeback with my bank.", IMMEDIATE, True),
        ("I never authorized this charge and I'm disputing it with my bank.", IMMEDIATE, True),
        ("This is a scam, you stole my money.", IMMEDIATE, True),
        ("I'm contacting a lawyer about this.", IMMEDIATE, True),
        ("I will file a BBB complaint if this isn't resolved.", IMMEDIATE, True),
        ("I want to speak to a manager right now.", IMMEDIATE, True),
        ("This is unacceptable, I demand a fix.", IMMEDIATE, True),
        # ---- HIGH (escalate, no owner ping) --------------------------------
        ("I changed my mind, I want a full refund for order #10322.", HIGH, True),
        ("Please cancel order #10345 immediately.", HIGH, True),
        ("The jumpsuit has a big tear on the sleeve.", HIGH, True),
        ("The item arrived damaged.", HIGH, True),
        ("The zipper on the coat is broken, it won't move.", HIGH, True),
        ("I ordered a blue bodysuit but got a pink dress.", HIGH, True),
        ("This isn't what I ordered at all.", HIGH, True),
        ("My order never arrived.", HIGH, True),
        ("The romper set arrived without the matching headband.", HIGH, True),
        ("I know it was final sale but can I return a final sale item?", HIGH, True),
        # ---- angry signals -------------------------------------------------
        ("Where is my stuff!!!", HIGH, True),
        ("THIS IS COMPLETELY UNACCEPTABLE AND VERY DISAPPOINTING SERVICE", IMMEDIATE, True),
        # ---- NORMAL (auto-draftable) ---------------------------------------
        ("Where is my order? Has it shipped yet?", NORMAL, False),
        ("Do you ship to Canada and how much?", NORMAL, False),
        ("What size bodysuit should I order for a 4 month old?", NORMAL, False),
        ("What brands do you carry?", NORMAL, False),
        ("Can I pick up locally instead of shipping?", NORMAL, False),
        ("Thanks so much, the dress is adorable!", NORMAL, False),
        # ---- word-boundary false-positive guards (must stay NORMAL) --------
        ("I took a trip to the store and loved it.", NORMAL, False),   # trip != rip
        ("Do you have a good grip strap for strollers?", NORMAL, False),  # grip != rip
        ("Please discard the old invoice, the new one is correct.", NORMAL, False),  # discard != card
        ("I scanned the QR code on the package.", NORMAL, False),      # scanned != scam/cancel
        ("It arrived today and I love it, thank you!", NORMAL, False),  # arrival w/o damage
        # ---- empty / garbled -> HIGH escalate, NOT sensitive ---------------
        ("", HIGH, False),
        ("...", HIGH, False),
        ("   \n\t  ", HIGH, False),
    ]

    failures = []
    for msg, exp_prio, exp_sens in CASES:
        c = classify(msg)
        prio_ok = c.priority == exp_prio
        sens_ok = c.sensitive == exp_sens
        # invariant: auto_draft_allowed == (not sensitive and not escalate)
        inv_ok = c.auto_draft_allowed == ((not c.sensitive) and (not c.escalate))
        # sensitive tickets must never be auto-draftable
        safe_ok = (not c.sensitive) or (not c.auto_draft_allowed)
        if not (prio_ok and sens_ok and inv_ok and safe_ok):
            failures.append(
                f"  FAIL msg={msg!r}\n"
                f"       got priority={c.priority} sensitive={c.sensitive} "
                f"escalate={c.escalate} auto_draft={c.auto_draft_allowed} "
                f"category={c.category!r}\n"
                f"       want priority={exp_prio} sensitive={exp_sens}"
                + ("" if inv_ok else "\n       *** INVARIANT VIOLATED ***")
                + ("" if safe_ok else "\n       *** SENSITIVE AUTO-DRAFTABLE ***")
            )

    # Adversarial: the gate is deterministic and cannot be talked out of a
    # chargeback flag — re-classifying the same text always escalates.
    cb = classify("I'm filing a chargeback with my bank.")
    if cb.auto_draft_allowed or not cb.sensitive or cb.priority != IMMEDIATE:
        failures.append("  FAIL [adversarial] chargeback must be IMMEDIATE, "
                        "sensitive, not auto-draftable")

    total = len(CASES) + 1
    if failures:
        print("CLASSIFIER SELF-TEST FAILED:")
        print("\n".join(failures))
        return False, total

    print(f"Ran {total} labelled checks.")
    print("Sensitive set (escalate, not auto-draftable): refund, chargeback, "
          "dispute, fraud, legal, cancellation, wrong/damaged/missing item, "
          "not-delivered, final-sale exception, angry signals.")
    print("IMMEDIATE (owner ping): chargeback, dispute, fraud, legal, "
          "manager-demand, shouting. HIGH: everything else sensitive.")
    print("Invariant proven: auto_draft_allowed == (not sensitive and not escalate).")
    print(f"CLASSIFIER SELF-TEST OK ({total} checks passed)")
    return True, total


if __name__ == "__main__":
    import sys
    ok, _ = _selftest()
    sys.exit(0 if ok else 1)
