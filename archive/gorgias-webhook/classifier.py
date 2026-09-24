#!/usr/bin/env python3
"""
classifier.py — SAFETY-CRITICAL priority/escalation classifier (Stage 2, Task 5).

Its #1 job: make sure SENSITIVE tickets are ESCALATED TO A HUMAN and NEVER
auto-drafted. Per SYSTEM_WORKFLOW.md's SAFETY MODEL:

    "Sensitive tickets are escalated, not drafted.
     Refunds, chargebacks, disputes: always IMMEDIATE, always escalated."

DESIGN — deterministic-first, this ordering IS the safety invariant:

  1. RULES ARE AUTHORITATIVE FOR ESCALATION. A pure-stdlib regex/keyword rule
     engine flags the sensitive set (refund, chargeback, dispute, cancel,
     "never arrived", legal/lawyer/attorney, fraud, "speak to a manager",
     payment disputes, BBB/bank disputes, ...) as escalate=True, urgency
     "immediate", with a human-readable reason. This runs OFFLINE with no LLM.

  2. THE LLM CAN ONLY ADD CAUTION, NEVER CLEAR IT. The optional model_gateway
     pass may ESCALATE a ticket the rules thought was benign (e.g. subtle anger
     the keywords missed), or refine a NON-sensitive category label. It is
     STRUCTURALLY INCAPABLE of downgrading a rule-flagged sensitive ticket back
     to auto-draftable: after the LLM runs we re-assert the rule verdict with
     `_merge_llm` (rule escalate/sensitive are OR-ed in, never overwritten) and
     `auto_draft_allowed` is recomputed as (not sensitive and not escalate).
     This property is proven in the __main__ self-test (adversarial case).

  3. CONSERVATIVE BIAS. When uncertain — empty/garbled input, an LLM error, a
     never-seen category — we prefer escalate / no-auto-draft. A false escalation
     costs a human a glance; a wrong auto-draft on a refund/chargeback is
     expensive and customer-facing.

PUBLIC API:
    classify(ctx_or_text, subject=None) -> Classification

    Accepts:
      * a pipeline.TicketContext   (we pull the latest from_agent=False message
                                    body + the ticket subject)
      * a dict                     (a TicketContext.to_dict(), a raw webhook-ish
                                    payload, or {"text":..., "subject":...})
      * a raw str                  (the customer message; `subject=` optional)

Stdlib only (re, dataclasses, logging). Runs with NO LLM key. Does NOT call the
Gorgias API, post anything, or message a customer — this is pure classification.

Run directly for the labeled self-test (prints "CLASSIFIER SELF-TEST OK"):
    python3 classifier.py
"""

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("classifier")

# Optional LLM nuance pass. A teammate builds model_gateway.py in parallel; we
# degrade gracefully if it is absent. It can ONLY add caution (see _merge_llm).
try:  # pragma: no cover - presence depends on parallel build
    import model_gateway  # noqa: F401
    _HAVE_GATEWAY = True
except Exception:  # ImportError or anything during its import
    model_gateway = None
    _HAVE_GATEWAY = False


# --------------------------------------------------------------------------- #
# Urgency levels (ordered; higher index = more urgent)
# --------------------------------------------------------------------------- #
URGENCY_LOW = "low"
URGENCY_NORMAL = "normal"
URGENCY_HIGH = "high"
URGENCY_IMMEDIATE = "immediate"

_URGENCY_RANK = {
    URGENCY_LOW: 0,
    URGENCY_NORMAL: 1,
    URGENCY_HIGH: 2,
    URGENCY_IMMEDIATE: 3,
}


def _max_urgency(a, b):
    """Return the more urgent of two urgency labels (used to escalate, never to
    de-escalate)."""
    return a if _URGENCY_RANK.get(a, 1) >= _URGENCY_RANK.get(b, 1) else b


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #
@dataclass
class Classification:
    """The classifier verdict for one ticket.

    Fields:
      category            — best-guess topic, e.g. "refund", "chargeback",
                            "dispute", "cancellation", "not_delivered",
                            "legal", "fraud", "escalation_request",
                            "shipping_status", "sizing", "returns_exchanges",
                            "order_change", "product_question", "general",
                            or "unknown".
      urgency / priority  — "immediate" | "high" | "normal" | "low".
                            `priority` is an alias of `urgency` (the workflow doc
                            and feedback.db use the word "priority"; callers may
                            use either).
      escalate            — True => route to a human, post an escalation note
                            only, send the Telegram notify. NEVER auto-draft.
      sensitive           — True => belongs to the always-escalate sensitive set
                            (refund/chargeback/dispute/legal/fraud/...). Sensitive
                            implies escalate.
      reasons             — human-readable list of which rules fired (audit trail;
                            maps to drafts.classification_reason).
      auto_draft_allowed  — True ONLY if (not sensitive) AND (not escalate). This
                            is the single gate Workflow A checks before drafting a
                            customer-facing reply.
      matched_keywords    — the literal phrases/keywords that tripped the rules.
    """

    category: str = "unknown"
    urgency: str = URGENCY_NORMAL
    escalate: bool = False
    sensitive: bool = False
    reasons: list = field(default_factory=list)
    auto_draft_allowed: bool = False
    matched_keywords: list = field(default_factory=list)
    # SOFT, NON-ESCALATING advisory: the ticket asks for guess-prone product
    # info (sizing / fit / measurements / fabric / sleeve length / launch date).
    # Per the owner's "Do not guess product information" Core Rule, these must be
    # answered ONLY from product data/notes — otherwise say "we'll check". We do
    # NOT hard-escalate (the KB now answers many such questions, and the draft
    # engine's KB-gap path already produces a safe "we'll check" holding note),
    # so this flag NEVER touches escalate/sensitive/auto_draft_allowed. It is a
    # hint for the draft engine / audit trail only. Deliberately a soft signal so
    # a grounded "we'll check" draft can still be produced instead of a hard stop.
    product_info_caution: bool = False

    # `priority` is a read/write alias of `urgency` so either name works.
    @property
    def priority(self):
        return self.urgency

    @priority.setter
    def priority(self, value):
        self.urgency = value

    def recompute_auto_draft(self):
        """Re-derive auto_draft_allowed from the safety invariant. The ONLY place
        auto_draft_allowed is ever set True. Called after the rules and again
        after any LLM merge, so the LLM can never widen the gate."""
        self.auto_draft_allowed = (not self.sensitive) and (not self.escalate)
        return self.auto_draft_allowed

    def as_dict(self):
        return {
            "category": self.category,
            "urgency": self.urgency,
            "priority": self.urgency,
            "escalate": self.escalate,
            "sensitive": self.sensitive,
            "reasons": list(self.reasons),
            "auto_draft_allowed": self.auto_draft_allowed,
            "matched_keywords": list(self.matched_keywords),
            "product_info_caution": self.product_info_caution,
        }


# --------------------------------------------------------------------------- #
# SENSITIVE RULES — the always-escalate, never-auto-draft set.
# Each entry: (category, urgency, [regex patterns], human reason).
# Patterns are compiled case-insensitively with word boundaries so "discard"
# does not trip "card", "scanceled" does not trip "cancel", etc.
# Order matters only for which category is reported first; ALL matches are
# recorded in matched_keywords and reasons.
# --------------------------------------------------------------------------- #
def _kw(*alts):
    """Build a case-insensitive, word-boundary regex matching any alternative.
    Each alternative may itself contain spaces; runs of whitespace in the input
    are normalized to a single space before matching (see _normalize)."""
    # \b at the edges; alternatives joined with |. Escape literal phrases.
    body = "|".join(re.escape(a) for a in alts)
    return re.compile(r"\b(?:" + body + r")\b", re.IGNORECASE)


# NOTE on phrasing coverage: these mine the SYSTEM_WORKFLOW IMMEDIATE list plus
# the real customer phrasings called out in the task. Keep additive — adding a
# pattern can only escalate more, never less.
SENSITIVE_RULES = [
    # --- Refund / money-back -------------------------------------------------
    ("refund", URGENCY_IMMEDIATE, [
        _kw("refund", "refunded", "refunding", "reimburse", "reimbursement"),
        _kw("money back", "my money back", "i want my money back",
            "give me my money", "return my money", "want a refund",
            "full refund", "partial refund"),
    ], "Refund request — money-back / refund language present"),

    # --- Chargeback ----------------------------------------------------------
    ("chargeback", URGENCY_IMMEDIATE, [
        _kw("chargeback", "charge back", "charge-back", "charged back"),
        _kw("reverse the charge", "reverse the payment", "reverse this charge"),
    ], "Chargeback — customer threatening/initiating a card chargeback"),

    # --- Bank / payment dispute ----------------------------------------------
    ("dispute", URGENCY_IMMEDIATE, [
        _kw("dispute", "disputing", "disputed"),
        _kw("dispute this with my bank", "dispute the charge",
            "dispute with my bank", "file a dispute", "open a dispute",
            "contact my bank", "call my bank", "report to my bank",
            "report it to my bank", "contest the charge"),
        _kw("unauthorized charge", "unauthorised charge",
            "unauthorized transaction", "unauthorised transaction"),
    ], "Payment dispute — bank/card dispute language present"),

    # --- Cancellation --------------------------------------------------------
    ("cancellation", URGENCY_IMMEDIATE, [
        _kw("cancel", "canceled", "cancelled", "canceling", "cancelling",
            "cancellation"),
        _kw("cancel my order", "cancel the order", "cancel this order",
            "cancel my subscription", "stop my order", "stop the order"),
    ], "Cancellation request — order/subscription cancel language present"),

    # --- Never arrived / not delivered (lost package) ------------------------
    ("not_delivered", URGENCY_IMMEDIATE, [
        _kw("never arrived", "never came", "it never came", "never received",
            "never got it", "never got my", "didn't arrive", "did not arrive",
            "hasn't arrived", "has not arrived", "not delivered",
            "not received", "didn't receive", "did not receive",
            "still hasn't arrived", "still haven't received",
            "where is my package", "where's my package", "lost package",
            "lost in transit", "package is lost", "stolen package",
            "marked delivered but"),
    ], "Possible lost/undelivered order — 'never arrived' language present"),

    # --- Legal threat --------------------------------------------------------
    ("legal", URGENCY_IMMEDIATE, [
        _kw("lawyer", "lawyers", "attorney", "attorneys", "legal action",
            "legal counsel", "sue", "suing", "lawsuit", "litigation",
            "take you to court", "small claims", "cease and desist",
            "consumer protection"),
        _kw("bbb", "better business bureau", "ftc",
            "file a complaint", "filing a complaint", "report you to",
            "consumer affairs"),
    ], "Legal/regulatory threat — lawyer/attorney/BBB/complaint language present"),

    # --- Fraud / scam accusation ---------------------------------------------
    ("fraud", URGENCY_IMMEDIATE, [
        _kw("fraud", "fraudulent", "fraudster", "scam", "scammed", "scammer",
            "scamming", "this is a scam", "this is fraud", "rip off",
            "ripped me off", "ripoff", "theft", "stole my money",
            "stolen money", "you stole"),
    ], "Fraud/scam accusation — fraud/scam/theft language present"),

    # --- Escalation request (speak to a manager / supervisor) ----------------
    ("escalation_request", URGENCY_IMMEDIATE, [
        _kw("speak to a manager", "speak to manager", "speak to your manager",
            "talk to a manager", "talk to your manager", "get me a manager",
            "your supervisor", "speak to a supervisor", "talk to a supervisor",
            "escalate this", "escalate my", "i demand", "unacceptable",
            "this is unacceptable"),
    ], "Escalation demand — 'speak to a manager/supervisor' language present"),

    # --- Wrong item received (owner Core Rule: "Customer received wrong item")
    # Warehouse must verify the physical item against the order and ship the
    # correct one (KB Intent 15). Never auto-draft — a human/warehouse owns it.
    ("wrong_item", URGENCY_IMMEDIATE, [
        _kw("wrong item", "wrong product", "wrong order", "wrong size sent",
            "wrong color sent", "wrong colour sent"),
        _kw("received the wrong", "got the wrong", "sent me the wrong",
            "sent the wrong", "shipped the wrong", "you sent the wrong",
            "i got the wrong", "i received the wrong", "received a different",
            "got a different item"),
        _kw("not what i ordered", "not the item i ordered",
            "isn't what i ordered", "isnt what i ordered",
            "different from what i ordered", "different item than i ordered",
            "wasn't what i ordered", "wasnt what i ordered"),
    ], "Wrong item received — warehouse must verify/ship correct item (Intent 15)"),

    # --- Damaged / defective on arrival (owner Core Rule: "received damaged
    # item"). Photos required + vendor/warehouse handling (KB Intent 16). Word
    # boundaries on every term so e.g. "ripper", "torn-down" copy, "staining
    # instructions" do not false-trip, and \brip\b cannot match inside "trip"/
    # "grip". The bare "damage-noun" fragments below catch the most common
    # phrasings on their own; the DAMAGE CO-OCCURRENCE rule (see
    # SENSITIVE_COOCCUR_RULES) catches the rest (a damage noun + an arrival/
    # location context anywhere in the message).
    ("damaged_item", URGENCY_IMMEDIATE, [
        # always-damage words (unambiguous on their own)
        _kw("damaged", "defective", "fell apart"),
        # arrival/possession + damage (contiguous)
        _kw("arrived broken", "came broken", "arrived damaged", "came damaged",
            "arrived ripped", "came ripped", "arrived torn", "came torn",
            "arrived stained", "came stained", "arrived dirty", "came dirty",
            "arrived with a hole", "arrived with holes", "came with a hole",
            "ripped on arrival", "torn on arrival", "stained on arrival",
            "broken on arrival"),
        # bare damage-noun fragments (word-boundary, so "trip"/"grip" are safe)
        _kw("a rip", "a tear", "a hole", "a stain", "a crack",
            "rip in it", "tear in it", "hole in it", "stain on it",
            "rip in the", "tear in the", "hole in the", "stain on the",
            "tear in the fabric", "hole in the sleeve",
            "has a hole", "has a tear", "has a rip", "has a stain",
            "is ripped", "is torn", "is stained", "is broken", "is cracked",
            "zipper is broken", "seam is ripped", "it's broken",
            "its broken", "the zipper broke", "the seam ripped"),
    ], "Damaged/defective item — photos + vendor/warehouse handling (Intent 16)"),

    # --- Final sale exception request (owner Core Rule: "final sale
    # exception"). Any return/exchange/exception ask on a final-sale item is
    # warehouse-only and approval-gated (KB Intent 12) — never auto-promise.
    ("final_sale_exception", URGENCY_IMMEDIATE, [
        _kw("final sale exception", "final sale return", "final sale exchange",
            "exception for final sale", "exception on final sale",
            "return a final sale", "exchange a final sale",
            "return my final sale", "exchange my final sale",
            "final sale item back", "send back a final sale"),
    ], "Final sale exception request — warehouse-only, approval-gated (Intent 12)"),
]


# --------------------------------------------------------------------------- #
# CO-OCCURRENCE SENSITIVE RULES — fire only when ALL trigger groups match
# somewhere in the text (not necessarily contiguous). This catches phrasings the
# contiguous-phrase rules miss, e.g. "this was final sale, can I get an
# exception?" or "I know it's final sale but I'd like to return it." Each group
# is a single _kw() pattern; the rule fires iff EVERY group matches. Like the
# contiguous rules, this can only ESCALATE — it never clears a flag.
# Each entry: (category, urgency, [group_pattern, ...], reason).
# --------------------------------------------------------------------------- #
SENSITIVE_COOCCUR_RULES = [
    # "final sale" + an exception/return/exchange ask anywhere in the message.
    ("final_sale_exception", URGENCY_IMMEDIATE, [
        _kw("final sale", "final-sale"),
        _kw("exception", "return", "returns", "returning", "exchange",
            "exchanges", "exchanging", "send it back", "send them back",
            "send back", "swap", "refund", "make an exception"),
    ], "Final sale exception request — 'final sale' + return/exchange/exception "
       "language present (warehouse-only, approval-gated; Intent 12)"),

    # DAMAGE CO-OCCURRENCE: a damage noun + an arrival/possession/location
    # context anywhere in the message (KB Intent 16). Catches natural phrasings
    # the contiguous rule misses ("my dress arrived with a big rip in it",
    # "there's a tear in the fabric", "it came with a stain on it"). Requires
    # BOTH groups, so a damage word alone in unrelated copy won't escalate, and
    # a bare context word alone ("it arrived today!") won't either. Word
    # boundaries keep "rip" out of "trip"/"grip" and "tear" out of "tearing up".
    ("damaged_item", URGENCY_IMMEDIATE, [
        # damage noun / state
        _kw("rip", "ripped", "tear", "torn", "hole", "holes", "stain",
            "stained", "broken", "broke", "crack", "cracked", "defect",
            "defective", "damaged"),
        # arrival / possession / location context
        _kw("arrived", "came", "received", "got", "it has", "has a",
            "there's a", "there is a", "in the", "on the", "in it", "on it",
            "with a", "with holes", "came with", "arrived with"),
    ], "Damaged/defective item — damage + arrival/location context present "
       "(photos + vendor/warehouse handling; Intent 16)"),
]


# --------------------------------------------------------------------------- #
# NON-SENSITIVE category rules — used only to LABEL benign tickets. These NEVER
# set escalate/sensitive on their own; they just pick a category and an urgency
# so Workflow A can route a draft. (Empty match => "general".)
# Each entry: (category, urgency, [patterns]).
# --------------------------------------------------------------------------- #
NORMAL_RULES = [
    ("shipping_status", URGENCY_HIGH, [
        _kw("where is my order", "where's my order", "track my order",
            "tracking", "tracking number", "has my order shipped",
            "has it shipped", "when will it arrive", "when will it ship",
            "shipping update", "shipment", "order status", "status of my order",
            "estimated delivery", "delivery date", "out for delivery"),
    ]),
    ("returns_exchanges", URGENCY_HIGH, [
        _kw("return", "returns", "exchange", "exchanges", "swap", "send back",
            "return label", "return policy", "exchange for", "store credit",
            "wrong size sent"),
    ]),
    ("sizing", URGENCY_NORMAL, [
        _kw("size", "sizing", "fit", "fits", "measurements", "size chart",
            "what size", "which size", "too small", "too big", "runs small",
            "runs large", "size guide"),
    ]),
    ("order_change", URGENCY_HIGH, [
        _kw("change my order", "change the order", "update my order",
            "update my address", "change my address", "wrong address",
            "edit my order", "add to my order", "modify my order",
            "change the size", "change the color", "change the colour"),
    ]),
    ("product_question", URGENCY_NORMAL, [
        _kw("material", "fabric", "cotton", "washing", "wash", "care",
            "color", "colour", "in stock", "restock", "back in stock",
            "available", "how do i use", "does it come with",
            "what is the", "is this", "do you have"),
    ]),
]


# --------------------------------------------------------------------------- #
# PRODUCT-INFO CAUTION patterns — the owner's "Do not guess product information"
# Core Rule (sizing / how it runs / measurements / fabric-material / sleeve
# length / launch dates). These set the SOFT, NON-ESCALATING product_info_caution
# flag ONLY. They deliberately DO NOT escalate or block auto-draft: the KB now
# carries approved "we'll check" templates and answers many of these, and the
# draft engine's KB-gap path already produces a safe "we'll check" holding note
# when the KB is silent. Hard-escalating every sizing question would over-escalate.
# --------------------------------------------------------------------------- #
PRODUCT_INFO_CAUTION_PATTERNS = [
    # sizing / fit / how it runs
    _kw("size", "sizing", "fit", "fits", "true to size", "runs small",
        "runs large", "runs big", "how does it run", "how does this run",
        "what size", "which size", "size chart", "size guide", "too small",
        "too big"),
    # measurements
    _kw("measurement", "measurements", "dimensions", "length", "width",
        "chest", "waist", "inseam", "how long is", "how wide"),
    # fabric / material
    _kw("fabric", "material", "made of", "made from", "cotton", "polyester",
        "what is it made"),
    # sleeve length
    _kw("sleeve", "sleeves", "long sleeve", "short sleeve", "sleeveless"),
    # launch / drop dates (guess-prone if no saved date)
    _kw("launch", "launching", "launch date", "drop date", "release date",
        "when does it drop", "when is the drop", "new arrival", "new arrivals",
        "restock date"),
]


def _detect_product_info_caution(haystack):
    """True if the text asks for guess-prone product info (sizing/measurements/
    fabric/sleeve/launch). Soft signal only — never escalates."""
    for pat in PRODUCT_INFO_CAUTION_PATTERNS:
        if pat.search(haystack):
            return True
    return False


# --------------------------------------------------------------------------- #
# Input normalization + extraction
# --------------------------------------------------------------------------- #
_WS_RE = re.compile(r"\s+")


def _normalize(text):
    """Lower-noise normalization for matching: collapse whitespace (incl.
    newlines/tabs) to single spaces and strip. We DO NOT strip punctuation —
    word-boundary regexes already ignore it — and we keep the original case
    (the regexes are IGNORECASE) so callers can still read the text."""
    if not text:
        return ""
    return _WS_RE.sub(" ", str(text)).strip()


# Email quoted-reply patterns that indicate the start of quoted history.
# Everything from the first match onward is the previous email thread, not
# the customer's actual new message. We strip it before classification.
_EMAIL_QUOTE_PATTERNS = [
    re.compile(r"\r?\n\s*On\s+.+\s+wrote:\s*", re.I),   # "On <date> ... wrote:"
    re.compile(r"\r?\n\s*El\s+.+\s+escribi[oó]:\s*", re.I),  # Spanish
    re.compile(r"\r?\n-{2,}\s*Original Message\s*-{2,}", re.I),  # "--- Original Message ---"
    re.compile(r"\r?\n\s*From:.*\r?\n\s*(?:Sent|To|Subject):", re.I),  # "From: ... Sent: ..."
    re.compile(r"\r?\n>"),                          # line starting with ">" (quoted)
    re.compile(r"\r?\n\s*\|"),                     # line starting with "|" (some clients)
]


def _strip_email_quotes(text):
    """Remove quoted email reply history from a message body.

    Email clients append the previous conversation as quoted text (lines
    starting with '>', or 'On <date> ... wrote:' blocks). This quoted
    history is NOT the customer's actual new message — it pollutes
    classification with old conversation topics.

    We find the FIRST quote marker and keep only the text before it.
    If no quote marker is found, return the original text unchanged.
    """
    if not text or not isinstance(text, str):
        return text or ""
    earliest = None
    for pat in _EMAIL_QUOTE_PATTERNS:
        m = pat.search(text)
        if m:
            pos = m.start()
            if earliest is None or pos < earliest:
                earliest = pos
    if earliest is not None:
        return text[:earliest].strip()
    return text.strip()


def _latest_customer_text_and_subject(ctx_or_dict):
    """From a TicketContext (or its dict form / a webhook-ish dict) pull the
    latest customer (from_agent=False) message body and the ticket subject.

    Falls back across body_text / stripped_text and, if no customer message is
    found, uses the most recent message of any kind (conservative: better to
    classify on agent text than on nothing)."""
    # Duck-type: object with .messages/.ticket, or a dict.
    if hasattr(ctx_or_dict, "messages"):
        messages = getattr(ctx_or_dict, "messages", None) or []
        ticket = getattr(ctx_or_dict, "ticket", None) or {}
    elif isinstance(ctx_or_dict, dict):
        messages = ctx_or_dict.get("messages") or []
        ticket = ctx_or_dict.get("ticket") or {}
    else:
        return "", None

    subject = None
    if isinstance(ticket, dict):
        subject = ticket.get("subject")

    def _body(m):
        if not isinstance(m, dict):
            return ""
        return m.get("body_text") or m.get("stripped_text") or ""

    def _is_customer(m):
        if not isinstance(m, dict):
            return False
        fa = m.get("from_agent")
        if isinstance(fa, str):
            return fa.strip().lower() not in ("true", "1", "yes")
        return not bool(fa)

    # Prefer the most-recent customer message (messages are oldest-first).
    customer_msgs = [m for m in messages if _is_customer(m) and _body(m).strip()]
    if customer_msgs:
        latest = customer_msgs[-1]
        if subject is None:
            subject = latest.get("subject")
        # Strip email quotes to avoid classifying quoted history as current intent
        return _strip_email_quotes(_body(latest)), subject

    # No customer message with text — fall back to most recent message of any kind.
    any_with_text = [m for m in messages if _body(m).strip()]
    if any_with_text:
        latest = any_with_text[-1]
        if subject is None and isinstance(latest, dict):
            subject = latest.get("subject")
        return _strip_email_quotes(_body(latest)), subject

    return "", subject


def _coerce_input(ctx_or_text, subject):
    """Return (text, subject) from any accepted input type:
      * str                 -> (the string, subject arg) — with email quotes stripped
      * TicketContext       -> latest customer msg + ticket subject
      * dict with messages  -> same extraction as a TicketContext
      * dict {"text"/"body_text"/"message", "subject"} -> those fields
    """
    if ctx_or_text is None:
        return "", subject

    if isinstance(ctx_or_text, str):
        # Strip email quotes to avoid classifying quoted history as current intent
        return _strip_email_quotes(ctx_or_text), subject

    # TicketContext (object) or its dict form (has "messages")
    if hasattr(ctx_or_text, "messages") or (
        isinstance(ctx_or_text, dict) and "messages" in ctx_or_text
    ):
        text, subj = _latest_customer_text_and_subject(ctx_or_text)
        # An explicit subject= argument wins if provided.
        return text, (subject if subject is not None else subj)

    if isinstance(ctx_or_text, dict):
        text = (
            ctx_or_text.get("text")
            or ctx_or_text.get("body_text")
            or ctx_or_text.get("stripped_text")
            or ctx_or_text.get("message")
            or ""
        )
        # Strip email quotes to avoid classifying quoted history as current intent
        text = _strip_email_quotes(text)
        subj = subject if subject is not None else ctx_or_text.get("subject")
        return text, subj

    # Unknown type — stringify defensively (conservative: never crash).
    return _strip_email_quotes(str(ctx_or_text)), subject


# --------------------------------------------------------------------------- #
# Core deterministic rule engine
# --------------------------------------------------------------------------- #
def _apply_sensitive_rules(haystack, result):
    """Run the SENSITIVE rule set against the normalized text. Mutates `result`,
    only ever ESCALATING (sets sensitive/escalate True, raises urgency, appends
    reasons/keywords). Records the first sensitive category as the category."""
    first_category = None
    for category, urgency, patterns, reason in SENSITIVE_RULES:
        hits = []
        for pat in patterns:
            for m in pat.finditer(haystack):
                hits.append(m.group(0))
        if hits:
            result.sensitive = True
            result.escalate = True
            result.urgency = _max_urgency(result.urgency, urgency)
            if first_category is None:
                first_category = category
            if reason not in result.reasons:
                result.reasons.append(reason)
            for h in hits:
                if h not in result.matched_keywords:
                    result.matched_keywords.append(h)

    # Co-occurrence rules: fire only when EVERY trigger group matches somewhere
    # in the text. Same "escalate-only" contract as the contiguous rules.
    for category, urgency, groups, reason in SENSITIVE_COOCCUR_RULES:
        group_hits = []
        for pat in groups:
            ms = [m.group(0) for m in pat.finditer(haystack)]
            if not ms:
                group_hits = None
                break
            group_hits.extend(ms)
        if group_hits:
            result.sensitive = True
            result.escalate = True
            result.urgency = _max_urgency(result.urgency, urgency)
            if first_category is None:
                first_category = category
            if reason not in result.reasons:
                result.reasons.append(reason)
            for h in group_hits:
                if h not in result.matched_keywords:
                    result.matched_keywords.append(h)
    return first_category


def _apply_normal_rules(haystack, result):
    """Run the NON-sensitive labeling rules. Only sets a category/urgency for a
    benign ticket; never sets sensitive/escalate. Returns the first category."""
    first_category = None
    seen_categories = []  # local dedup; avoids relying on scratch attribute
    for category, urgency, patterns in NORMAL_RULES:
        hits = []
        for pat in patterns:
            for m in pat.finditer(haystack):
                hits.append(m.group(0))
        if hits:
            result.urgency = _max_urgency(result.urgency, urgency)
            if first_category is None:
                first_category = category
            if category not in seen_categories:
                seen_categories.append(category)
    return first_category


def _rule_classify(text, subject):
    """Pure-deterministic classification. NO LLM. This is the authoritative
    safety pass: whatever it flags sensitive/escalate stays that way."""
    norm_text = _normalize(text)
    norm_subject = _normalize(subject)
    # Subject is part of the haystack — refund/chargeback often live in subjects.
    haystack = (norm_subject + " " + norm_text).strip()

    result = Classification()

    # --- Empty / garbled input -> conservative: do NOT auto-draft. -----------
    # We require at least a couple of alphabetic chars of real content.
    alpha = re.sub(r"[^a-zA-Z]", "", haystack)
    if len(alpha) < 2:
        result.category = "unknown"
        result.urgency = URGENCY_HIGH  # needs a human glance, can't be auto-drafted
        result.escalate = True
        result.sensitive = False
        result.reasons.append(
            "Empty or unintelligible message — cannot safely auto-draft; "
            "routed to a human."
        )
        result.recompute_auto_draft()
        return result

    # --- 1) SENSITIVE rules (authoritative escalation) -----------------------
    sensitive_category = _apply_sensitive_rules(haystack, result)

    # --- 2) NON-sensitive labeling (only if not already sensitive-categorized)
    normal_category = _apply_normal_rules(haystack, result)

    # --- 2b) SOFT product-info caution (owner "Do not guess product info"). ---
    # NON-escalating: this never touches escalate/sensitive/auto_draft_allowed.
    # It only hints the draft engine to answer strictly from product data/notes
    # or say "we'll check" rather than guess sizing/fabric/measurements/etc.
    if _detect_product_info_caution(haystack):
        result.product_info_caution = True
        note = ("Product-info question (sizing/fit/measurements/fabric/sleeve/"
                "launch date) — answer ONLY from product data/notes or say "
                "\"we'll check\"; do not guess (soft, non-escalating).")
        if note not in result.reasons:
            result.reasons.append(note)

    # --- Decide the reported category ---------------------------------------
    if sensitive_category:
        result.category = sensitive_category
    elif normal_category:
        result.category = normal_category
    else:
        # No rule matched at all — a benign-looking but uncategorized message.
        # This is auto-draftable (KB search decides if we actually can answer),
        # but we keep urgency normal and category "general".
        result.category = "general"
        result.urgency = _max_urgency(result.urgency, URGENCY_NORMAL)

    result.recompute_auto_draft()
    return result


# --------------------------------------------------------------------------- #
# Optional LLM nuance pass — CAN ONLY ADD CAUTION
# --------------------------------------------------------------------------- #
def _llm_opinion(text, subject):
    """Ask the optional model_gateway for a nuance read. Returns a dict like
    {"escalate": bool, "sensitive": bool, "category": str|None,
     "urgency": str|None, "reason": str|None} or None if unavailable/failed.

    This is best-effort and MUST NOT raise into the caller — any error is
    swallowed and treated as 'no opinion' (conservative: rules stand)."""
    if not _HAVE_GATEWAY or model_gateway is None:
        return None
    try:
        # The gateway's exact API is owned by a teammate; we probe for a couple
        # of plausible entry points and bail quietly if neither exists.
        fn = getattr(model_gateway, "classify_opinion", None) or getattr(
            model_gateway, "classify", None
        )
        if fn is None:
            return None
        opinion = fn(text=text, subject=subject)
        if isinstance(opinion, dict):
            return opinion
    except Exception as e:  # pragma: no cover - depends on teammate's module
        log.warning("model_gateway opinion failed, ignoring (rules stand): %s", e)
    return None


def _merge_llm(result, opinion):
    """Fold an LLM opinion into the rule result under the SAFETY INVARIANT:

      * sensitive / escalate are OR-ed in: the LLM may RAISE them to True but can
        NEVER lower them. We never read `result.sensitive = opinion[...]`; we
        only do `result.sensitive = result.sensitive or bool(opinion[...])`.
      * urgency only ever increases (_max_urgency).
      * category is refined by the LLM ONLY when the ticket is NOT sensitive and
        the rules left it "general"/"unknown" (the LLM can relabel a benign
        ticket, never relabel a sensitive one into something draftable).
      * auto_draft_allowed is RE-DERIVED from the invariant afterwards, so the
        LLM cannot widen the gate even by accident.
    """
    if not opinion:
        return result

    # Escalation / sensitivity: monotonic increase only.
    if opinion.get("escalate"):
        if not result.escalate:
            result.reasons.append(
                "LLM nuance: flagged for escalation (rules did not)."
            )
        result.escalate = True
    if opinion.get("sensitive"):
        if not result.sensitive:
            result.reasons.append("LLM nuance: flagged as sensitive (rules did not).")
        result.sensitive = result.sensitive or True

    # Urgency: only upward.
    llm_urgency = opinion.get("urgency")
    if llm_urgency in _URGENCY_RANK:
        result.urgency = _max_urgency(result.urgency, llm_urgency)

    # Category refinement: ONLY for non-sensitive, still-vague tickets.
    if (not result.sensitive) and result.category in ("general", "unknown"):
        llm_cat = opinion.get("category")
        if isinstance(llm_cat, str) and llm_cat.strip():
            result.category = llm_cat.strip()

    if opinion.get("reason"):
        result.reasons.append("LLM nuance: " + str(opinion["reason"]))

    # SAFETY: re-derive the auto-draft gate from sensitive/escalate. Even if the
    # opinion tried to set auto_draft_allowed True, it cannot — we never read it.
    result.recompute_auto_draft()
    return result


# --------------------------------------------------------------------------- #
# PUBLIC API
# --------------------------------------------------------------------------- #
def classify(ctx_or_text, subject=None, use_llm=True):
    """Classify a ticket for priority + escalation.

    Args:
      ctx_or_text — a pipeline.TicketContext, a dict (TicketContext.to_dict(),
                    a webhook-ish payload, or {"text":..., "subject":...}), or a
                    raw customer-message string.
      subject     — optional subject line (used when ctx_or_text is a raw string,
                    or to override the extracted subject).
      use_llm     — if True AND model_gateway is importable, run the LLM nuance
                    pass (CAN ONLY ADD CAUTION). Defaults True; harmless when no
                    gateway/key is present (degrades to rules-only, offline).

    Returns:
      Classification. `auto_draft_allowed` is True ONLY when the ticket is
      neither sensitive nor escalate — the single gate Workflow A must check
      before drafting a customer-facing reply.
    """
    text, subj = _coerce_input(ctx_or_text, subject)

    # 1) Authoritative deterministic rules (offline, no key needed).
    result = _rule_classify(text, subj)

    # 2) Optional LLM nuance — can only ADD caution, never clear an escalation.
    if use_llm:
        opinion = _llm_opinion(text, subj)
        if opinion:
            _merge_llm(result, opinion)

    return result


# --------------------------------------------------------------------------- #
# Self-test — labeled examples + the safety-invariant proof.
# --------------------------------------------------------------------------- #
def _selftest():
    # (message, subject, expect_escalate, expect_auto_draft, expect_sensitive, label)
    CASES = [
        # ---- SENSITIVE: MUST escalate, MUST NOT auto-draft ------------------
        ("I want my money back for this order.", None, True, False, True, "refund/money-back"),
        ("Please process a refund immediately.", None, True, False, True, "refund word"),
        ("I'm filing a chargeback with my credit card company.", None, True, False, True, "chargeback"),
        ("where is my package it never came", None, True, False, True, "never arrived"),
        ("My order still hasn't arrived and tracking shows nothing.", None, True, False, True, "hasn't arrived"),
        ("cancel my order please", None, True, False, True, "cancel order"),
        ("I want to cancel my subscription.", None, True, False, True, "cancel subscription"),
        ("this is fraud, you stole my money", None, True, False, True, "fraud"),
        ("I'll dispute this with my bank.", None, True, False, True, "bank dispute"),
        ("I'm contacting a lawyer about this.", None, True, False, True, "lawyer"),
        ("I will file a BBB complaint if this isn't resolved.", None, True, False, True, "BBB complaint"),
        ("Let me speak to a manager right now.", None, True, False, True, "speak to a manager"),
        ("This is a scam and I demand a refund.", None, True, False, True, "scam + refund"),
        ("", "Refund for order 123", True, False, True, "refund in SUBJECT only"),
        # ---- OWNER CORE-RULE escalations (new): wrong/damaged/final-sale -----
        ("I received the wrong item, this isn't what I ordered.", None, True, False, True, "wrong item received"),
        ("you sent me the wrong size, not the one I picked", None, True, False, True, "sent the wrong size"),
        ("this is not what I ordered at all", None, True, False, True, "not what I ordered"),
        ("my order arrived damaged", None, True, False, True, "arrived damaged"),
        ("the dress came with a hole in it", None, True, False, True, "hole on arrival"),
        ("the item is defective", None, True, False, True, "defective item"),
        ("it arrived ripped and stained", None, True, False, True, "ripped/stained on arrival"),
        # ---- DAMAGE phrasings (review MAJOR fix): natural / non-contiguous ---
        ("my dress arrived with a big rip in it", None, True, False, True, "arrived with a big rip in it"),
        ("there's a rip in it", None, True, False, True, "there's a rip in it"),
        ("there is a rip in the dress", None, True, False, True, "there is a rip in the dress"),
        ("there's a tear in the fabric", None, True, False, True, "tear in the fabric"),
        ("it came with a tear", None, True, False, True, "came with a tear"),
        ("this came with a stain on it", None, True, False, True, "came with a stain on it"),
        ("arrived with a stain", None, True, False, True, "arrived with a stain"),
        ("it's broken", None, True, False, True, "it's broken"),
        ("the zipper is broken", None, True, False, True, "zipper is broken"),
        ("there's a hole in the sleeve", None, True, False, True, "hole in the sleeve"),
        ("came ripped", None, True, False, True, "came ripped"),
        ("this was a final sale item but can I get an exception?", None, True, False, True, "final sale exception (cooccur)"),
        ("I know it's final sale, but I'd like to return it", None, True, False, True, "final sale + return (cooccur)"),
        ("can I make an exception on this final sale exchange?", None, True, False, True, "final sale exchange exception"),
        # ---- BENIGN: must NOT escalate, MUST be auto-draftable --------------
        ("what size for a 2 year old?", None, False, True, False, "sizing question"),
        ("Where is my order? Has it shipped yet?", None, False, True, False, "shipping status"),
        ("Do you have this dress in blue?", None, False, True, False, "product question"),
        ("How do I return an item for a different size?", None, False, True, False, "returns/exchange"),
        ("Can I change the shipping address on my order?", None, False, True, False, "order change"),
        ("Thank you so much, the dress is adorable!", None, False, True, False, "thank-you/compliment"),
        ("What material is the romper made of?", None, False, True, False, "material question"),
        ("Does this top run true to size for a toddler?", None, False, True, False, "how-it-runs (soft caution, still draftable)"),
        ("Is this dress long sleeve or short sleeve?", None, False, True, False, "sleeve length (soft caution, still draftable)"),
        ("Can you tell me the measurements of this romper?", None, False, True, False, "measurements (soft caution, still draftable)"),
        ("When is the new brand launching?", None, False, True, False, "launch date (soft caution, still draftable)"),
        # ---- WORD-BOUNDARY for the NEW rules: must NOT false-trip -----------
        ("I damaged the box opening it but the dress is perfect, love it!", None, True, False, True, "'damaged' present -> conservatively escalate (acceptable)"),
        ("This was a final sale and I'm thrilled with it, thank you!", None, False, True, False, "'final sale' alone (no return/exception) must NOT escalate"),
        ("Can I exchange this for a different size?", None, False, True, False, "plain exchange (no 'final sale') must NOT escalate"),
        # damage-rule false-positive guards: 'rip'/'tear'/'broken' must NOT trip
        ("I took a trip to the store and loved it", None, False, True, False, "'trip' must not trip 'rip'"),
        ("Do you have a good grip strap for strollers?", None, False, True, False, "'grip' must not trip 'rip'"),
        ("Free shipping on orders over 50?", None, False, True, False, "'shipping' must not trip damage rule"),
        ("It arrived today and I love it, thank you!", None, False, True, False, "arrival context w/o damage noun must NOT escalate"),
        # ---- GARBLED / EMPTY: conservative -> no auto-draft ----------------
        ("", None, True, False, False, "empty message"),
        ("...", None, True, False, False, "punctuation only"),
        ("   \n\t  ", None, True, False, False, "whitespace only"),
        # ---- WORD-BOUNDARY: must NOT false-trip -----------------------------
        ("Please discard the old invoice, the new one is correct.", None, False, True, False, "'discard' must not trip 'card'"),
        ("I scanned the QR code on the package.", None, False, True, False, "'scanned' must not trip 'scam'/'cancel'"),
    ]

    failures = []
    for msg, subj, exp_esc, exp_draft, exp_sens, label in CASES:
        c = classify(msg, subject=subj)
        ok = (
            c.escalate == exp_esc
            and c.auto_draft_allowed == exp_draft
            and c.sensitive == exp_sens
        )
        # Safety cross-check: auto_draft_allowed must ALWAYS equal
        # (not sensitive and not escalate), no matter what.
        invariant_ok = c.auto_draft_allowed == ((not c.sensitive) and (not c.escalate))
        if not ok or not invariant_ok:
            failures.append(
                f"  FAIL [{label}] msg={msg!r} subj={subj!r}\n"
                f"       got escalate={c.escalate} auto_draft={c.auto_draft_allowed} "
                f"sensitive={c.sensitive} category={c.category!r} "
                f"urgency={c.urgency!r}\n"
                f"       want escalate={exp_esc} auto_draft={exp_draft} "
                f"sensitive={exp_sens}"
                + ("" if invariant_ok else "\n       *** INVARIANT VIOLATED ***")
            )

    # ---- CATEGORY checks for the new owner Core-Rule escalations. ----------
    # Confirm each new trigger reports its own dedicated category (not folded
    # into refund/returns), so the audit trail and routing are precise.
    CATEGORY_CASES = [
        ("I received the wrong item, not what I ordered.", "wrong_item"),
        ("my order arrived damaged with a hole in it", "damaged_item"),
        ("this item is defective", "damaged_item"),
        ("this was final sale but can I get an exception?", "final_sale_exception"),
    ]
    for msg, exp_cat in CATEGORY_CASES:
        c = classify(msg)
        if c.category != exp_cat:
            failures.append(
                f"  FAIL [category] msg={msg!r} got category={c.category!r} "
                f"want {exp_cat!r}"
            )
        if not (c.escalate and c.sensitive and not c.auto_draft_allowed):
            failures.append(
                f"  FAIL [category] msg={msg!r} must be sensitive+escalate, "
                f"not auto-draftable (got escalate={c.escalate} "
                f"sensitive={c.sensitive} auto_draft={c.auto_draft_allowed})"
            )

    # ---- SOFT product-info caution: sets the flag but stays auto-draftable. -
    # The owner's "Do not guess product information" rule is enforced softly:
    # the flag is raised for the draft engine, but the ticket is STILL drafted
    # (so a grounded "we'll check" reply can be produced — not a hard escalation).
    SOFT_CASES = [
        "Does this top run true to size?",
        "What are the measurements of this romper?",
        "Is this long sleeve or short sleeve?",
        "What fabric is this made of?",
        "When is the new brand launching?",
    ]
    for msg in SOFT_CASES:
        c = classify(msg)
        if not c.product_info_caution:
            failures.append(
                f"  FAIL [soft-caution] msg={msg!r} expected product_info_caution=True"
            )
        if c.escalate or c.sensitive or not c.auto_draft_allowed:
            failures.append(
                f"  FAIL [soft-caution] msg={msg!r} product-info caution must NOT "
                f"escalate (got escalate={c.escalate} sensitive={c.sensitive} "
                f"auto_draft={c.auto_draft_allowed})"
            )

    # ---- ADVERSARIAL: prove the LLM can NEVER un-escalate a chargeback. ----
    # Simulate a maximally-hostile/buggy LLM opinion that tries to clear the
    # flags and force an auto-draft on a chargeback ticket.
    chargeback_msg = "I'm filing a chargeback with my bank for this order."
    base = classify(chargeback_msg, use_llm=False)
    assert base.sensitive and base.escalate and not base.auto_draft_allowed, \
        "precondition: chargeback must be sensitive+escalate, not auto-draftable"

    hostile_opinion = {
        "escalate": False,            # tries to clear escalation
        "sensitive": False,           # tries to clear sensitivity
        "auto_draft_allowed": True,   # tries to force auto-draft
        "category": "general",        # tries to relabel as benign
        "urgency": "low",             # tries to de-escalate urgency
        "reason": "looks fine to me, just auto-draft it",
    }
    merged = classify(chargeback_msg, use_llm=False)  # fresh rule result
    _merge_llm(merged, hostile_opinion)               # apply hostile LLM opinion
    if not (merged.sensitive and merged.escalate and not merged.auto_draft_allowed):
        failures.append(
            "  FAIL [adversarial] hostile LLM opinion DOWNGRADED a chargeback!\n"
            f"       got sensitive={merged.sensitive} escalate={merged.escalate} "
            f"auto_draft={merged.auto_draft_allowed} urgency={merged.urgency!r}"
        )
    # urgency must not have dropped from immediate to low either
    if _URGENCY_RANK.get(merged.urgency, 1) < _URGENCY_RANK[URGENCY_IMMEDIATE]:
        failures.append(
            "  FAIL [adversarial] hostile LLM opinion DOWNGRADED urgency below "
            f"immediate (got {merged.urgency!r})"
        )

    # cases + category checks + soft-caution checks + 1 adversarial proof
    total = len(CASES) + len(CATEGORY_CASES) + len(SOFT_CASES) + 1
    if failures:
        print("CLASSIFIER SELF-TEST FAILED:")
        print("\n".join(failures))
        return False, total

    print(f"Ran {total} labeled checks (incl. 1 adversarial LLM-cannot-downgrade proof).")
    print("Sensitive set: refund, chargeback, dispute/bank-dispute, cancellation, "
          "not_delivered, legal, fraud, escalation_request, wrong_item, "
          "damaged_item, final_sale_exception — all -> escalate, "
          "auto_draft_allowed=False.")
    print("Owner Core-Rule additions: wrong_item / damaged_item / "
          "final_sale_exception now hard-escalate (Intents 12/15/16).")
    print("Soft product-info caution (sizing/fit/measurements/fabric/sleeve/"
          "launch): flag raised but ticket STAYS auto-draftable -> a grounded "
          "\"we'll check\" reply, not a hard escalation.")
    print("Adversarial proof: a hostile LLM opinion (escalate=False, sensitive=False, "
          "auto_draft_allowed=True) CANNOT downgrade a chargeback. PASSED.")
    print(f"CLASSIFIER SELF-TEST OK ({total} checks passed)")
    return True, total


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ok, _ = _selftest()
    sys.exit(0 if ok else 1)
