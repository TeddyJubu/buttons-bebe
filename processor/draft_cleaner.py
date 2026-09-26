"""Last-mile AI-draft cleaning and customer acknowledgement gating.

This standard-library-only module can shorten or suppress a draft, never send
or lengthen one. See ADR-015 §2.4 for the safety and bounded-work rationale.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ADR-015 §2.4 — anchored self-talk may only shorten or suppress; tails stay visible.
_SELF_TALK_MARKERS = [
    # "The response above was complete..."  (QA #01/#04/#10)
    r"the response above was complete",
    r"the (?:previous|prior) (?:response|reply|draft) (?:was|is) (?:already )?complete",
    # "The above response addresses the question."
    r"the above (?:response|reply|draft) ",
    # "This reply is complete." / "This response above is complete."
    r"this (?:response|reply|draft) (?:above )?(?:is|was) (?:now )?complete",
    # "I have completed the response." / "I have now finished this draft."
    r"i have (?:now )?(?:completed|finished) (?:the|this|my) (?:response|reply|draft)",
    # Internal notes leave the sendable body but are returned to the reviewer.
    r"notes? (?:to|for) (?:the )?(?:reviewer|agent|human)\b",
    r"^internal\b[:\s]",
    r"for internal use\b",
    r"confidence:\s",
    # ADR-015 §2.4 — the shared prefix handles dashes; overlapping classes do not.
    r"\[?end of (?:the\s+)?(?:response|reply|draft)\]?",
    # Completion variants observed in model output.
    r"(?:the\s+)?(?:response|reply|draft)\s+above\s+(?:is|was)\s+"
    r"(?:already\s+|now\s+)?complete",
    # "Draft complete." / "[Draft complete]"
    r"\[?(?:draft|response|reply)\s+complete\b",
    # "I've completed the draft." / "I have written the response above."
    r"i(?:'ve| have)\s+(?:completed|written|finished)\s+(?:the|this|my)\s+"
    r"(?:response|reply|draft)",
    # An internal agent note is not customer-facing text.
    r"agent[\s-]note\b",
    r"\(internal[:\s]",
    # Model outputs sometimes wrap these labels in brackets. They are
    # reviewer material, never customer-facing prose.
    r"suggested(?:\s+customer[- ]facing)?\s+reply\b",
    r"recommended(?:\s+customer[- ]facing)?\s+reply\b",
    r"recommended(?:\s+customer[- ]facing)?\s+handling\b",
    r"action\s+needed\b",
    r"policy\s+basis\b",
    r"human\s+review\b",
    r"no\s+customer[- ]facing\s+reply\b",
    r"\[internal\s+note\b",
    r"\[suggested(?:\s+customer[- ]facing)?\s+reply\b",
    r"\[recommended(?:\s+customer[- ]facing)?\s+handling\b",
    r"\[action\s+needed\b",
    r"\[policy\s+basis\b",
    r"\[no\s+customer[- ]facing\s+reply\b",
    # "As an AI, I cannot ..." style refusals leaking into a draft.
    r"as an ai\b.*\bi (?:cannot|can't|am unable)",
]
_MARKER_RE = re.compile(
    r"^[\s>*#\-]*(?:" + "|".join(_SELF_TALK_MARKERS) + r")",
    re.IGNORECASE,
)

# Hard bounds on what should_draft() will scan. See the note in that function:
# these are a second line of defence behind the patterns themselves, because
# this gate runs synchronously on a single-process pipeline.
_MAX_GATE_SUBJECT = 2_000
_MAX_GATE_MESSAGE = 20_000

# A repeated block must be at least this many normalised characters before we
# treat it as a genuine duplication. Keeps short, legitimately-repeated content
# (e.g. "Yes.\n\nYes.") from being collapsed.
_MIN_DUP_CHARS = 40

# The model prompt asks for 4 sentences for routine work and 5 for sensitive
# work. This is a last-mile guard: an overlong draft is shortened at a complete
# sentence boundary rather than reaching the console unchanged.
_MAX_NORMAL_SENTENCES = 4
_MAX_SENSITIVE_SENTENCES = 5
SENSITIVE_DRAFT_PREFIX = "[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]"
_SENSITIVE_HEADER_RE = re.compile(
    r"^\s*(" + re.escape(SENSITIVE_DRAFT_PREFIX) + r")"
    r"\s*(?:\n+|$)",
    re.IGNORECASE,
)
_SENTENCE_END_RE = re.compile(r"[.!?](?:[\"')\]]+)?(?=\s|$)")
_COMMON_ABBREVIATION_RE = re.compile(
    r"\b(?:e\.g|i\.e|u\.s|u\.k|mr|mrs|ms|dr|vs|etc)\.",
    re.IGNORECASE,
)

_OPERATION_VERBS = (
    r"switch|change|cancel|replace|ship|refund|credit|issue|process|create|"
    r"invoice|prioritize|hold|flag|contact|notify|remove|add|apply|arrange|"
    r"leave|correct|resolve"
)
_OPERATION_PARTICIPLES = (
    r"switched|updated|changed|cancelled|canceled|replaced|shipped|sent|"
    r"issued|processed|refunded|credited|created|prioritized|held|flagged|"
    r"contacted|notified|removed|added|applied|arranged|left|corrected|resolved"
)
_ORDER_MUTATION_PARTICIPLES = (
    r"updated|changed|cancelled|canceled|replaced|issued|processed|refunded|"
    r"credited|created|prioritized|held|flagged|contacted|notified|removed|"
    r"added|applied|arranged|left|corrected|resolved"
)
_OPERATION_OBJECTS = (
    r"label|refund|credit|replacement|invoice|warehouse|order|shipment|"
    r"package|address"
)
_UPDATE_OPERATION = r"update(?!\s+(?:you|yourself|us|the\s+customer)\b)"

# Hermes is read-only. These patterns target first-person operational claims,
# including unsupported review/follow-up commitments. A match fails closed so
# the customer never sees a
# claim that the store has performed or committed to an external action.
_ACTION_CLAIM_RE = re.compile(
    r"(?:"
    r"\b(?:we|i|our team|the team|the store|store|warehouse|a human|human)\s+(?:can|could|will|would|have|has|"
    r"already|just|are going to|is going to)\s+"
    rf"(?:{_OPERATION_VERBS}|{_UPDATE_OPERATION})\b"
    r"|\b(?:we|i)\s*['\u2019]re going to\s+"
    rf"(?:{_OPERATION_VERBS}|{_UPDATE_OPERATION})\b"
    r"|\b(?:we|i)\s*['\u2019]m going to\s+"
    rf"(?:{_OPERATION_VERBS}|{_UPDATE_OPERATION})\b"
    r"|\b(?:we|i)\s*['\u2019](?:ll|ve)\s+"
    rf"(?:{_OPERATION_VERBS}|{_UPDATE_OPERATION})\b"
    r"|\b(?:we|i)\s*['\u2019]ll\s+(?:get|have)\s+"
    r"(?:it|this|that|your(?:\s+\w+){0,3}|the(?:\s+\w+){0,3})\s+"
    rf"(?:{_OPERATION_PARTICIPLES})\b"
    r"|\b(?:we|i)\s*['\u2019]ll\s+take\s+care\s+of\s+"
    r"(?:it|this|that|your\s+order)\b"
    r"|\b(?:we|i)\s*['\u2019]ll\s+(?:work\s+on\s+)?getting\s+"
    r"(?:your|the)\b[^.!?\n]{0,80}\b(?:sent|shipped|replaced|updated|"
    r"switched|corrected)\b"
    r"|\b(?:we|i|our team|the team)\s+(?:have|has|just|already)\s+"
    rf"(?:{_OPERATION_PARTICIPLES})\b"
    r"|\b(?:we|i|our team|the team|the store|store|warehouse|a human|human)\s+(?:will|would|are going to|is going to)\s+"
    r"send\b[^.!?\n]{0,80}\b(?:prepaid\s+|return\s+)?"
    rf"(?:{_OPERATION_OBJECTS})\b"
    r"|\b(?:we|i|our team|the team|the store|store|warehouse|a human|human)\s+(?:will|would|are going to|is going to)\s+"
    r"provide\b[^.!?\n]{0,80}\b(?:prepaid\s+|return\s+)?"
    rf"(?:{_OPERATION_OBJECTS})\b"
    r"|\b(?:we|i)\s*['\u2019]ll\s+(?:send|provide)\b"
    r"[^.!?\n]{0,80}\b(?:prepaid\s+|return\s+)?"
    rf"(?:{_OPERATION_OBJECTS})\b"
    r"|\b(?:we|i)\s*['\u2019]re going to\s+(?:send|provide)\b"
    r"[^.!?\n]{0,80}\b(?:prepaid\s+|return\s+)?"
    rf"(?:{_OPERATION_OBJECTS})\b"
    r"|\b(?:we|i)\s*['\u2019]m going to\s+(?:send|provide)\b"
    r"[^.!?\n]{0,80}\b(?:prepaid\s+|return\s+)?"
    rf"(?:{_OPERATION_OBJECTS})\b"
    r"|\b(?:we|i)\s*['\u2019]ve\s+"
    rf"(?:{_OPERATION_PARTICIPLES})\b[^.!?\n]{{0,80}}\b(?:prepaid\s+|return\s+)?"
    r"(?:label|refund|credit|replacement|invoice|warehouse|address|return)\b"
    r"|\b(?:your|the|a|an)\s+(?:prepaid\s+return\s+label|return\s+label|"
    r"item|replacement|refund|credit|label|invoice|shipment|package|return|"
    r"address|warehouse)\s+(?:has|have)(?:\s+been)?\s+"
    rf"(?:{_OPERATION_PARTICIPLES})\b"
    r"|\b(?:your|the|a|an)\s+(?:prepaid\s+return\s+label|return\s+label|"
    r"item|replacement|refund|credit|label|invoice|shipment|package|return|"
    r"address|warehouse)\s+(?:is|are|was|were|will)\s+(?:being\s+|be\s+)?"
    rf"(?:{_OPERATION_PARTICIPLES})\b"
    r"|\b(?:your|the|order(?:\s+#?\w+)?)\s+(?:has|have)(?:\s+been)?\s+"
    rf"(?:{_ORDER_MUTATION_PARTICIPLES})\b"
    r"|\b(?:your|the|order(?:\s+#?\w+)?)\s+(?:is|are|was|were|will)\s+"
    r"(?:being\s+|be\s+)?"
    rf"(?:{_OPERATION_PARTICIPLES})\b"
    r")",
    re.IGNORECASE,
)
_PENDING_ACTION_RE = re.compile(
    r"\b(?:reviewing|checking|confirming|verifying)\b[^.!?\n]{0,50}"
    r"\b(?:whether\s+)?(?:we|i|our team|the team)\s+(?:can|could|may|might)\s+"
    rf"(?:{_OPERATION_VERBS}|update)\b",
    re.IGNORECASE,
)
_UNCONFIRMED_ACTION_RE = re.compile(
    r"\b(?:i|we)\s+(?:cannot|can['’]t)\s+confirm\s+(?:that|whether|if)\s+"
    r"[^.!?\n;,:—–]{0,180}\Z", re.IGNORECASE,
)

# No tool evidence is available to the cleaner. First-person work commitments
# cannot be authenticated here; preserve factual policy and customer questions.
_REVIEW_COMMITMENT_RE = re.compile(
    r"\b(?:we|i|our team|the team)\s*(?:['\u2019](?:re|m)|are|am|is)\s+"
    r"(?:currently\s+)?(?:reviewing|checking|investigating|looking into|working on)\b"
    r"|\b(?:we|i|our team|the team)\s*(?:['\u2019]ll|will)\s+"
    r"(?:review|check|investigate|look into|get back|follow up|update you|send (?:you )?an update|make it right)\b",
    re.IGNORECASE,
)
# Observed Spanish first-person work claims only; this is not a language-wide
# safety detector. Do not replace these with an English customer-facing fallback.
_SPANISH_REVIEW_COMMITMENT_RE = re.compile(
    r"\b(?:estamos|estoy)\s+(?:actualmente\s+)?"
    r"(?:revisando|comprobando|investigando)\b"
    r"|\b(?:revisaremos|comprobaremos|investigaremos)\b",
    re.IGNORECASE,
)
# Confirmed return-packing guidance describes why the customer identifies each
# item/order. It does not promise an individual return or financial outcome.
_RETURN_IDENTIFICATION_INSTRUCTION_RE = re.compile(
    r"\A\s*please\s+include\s+a\s+note\s+(?:inside\s+)?(?:(?:the|your)\s+package\s+)?"
    r"(?:identifying|listing)\s+each\s+item\s+and\s+its\s+order\s+number\s+"
    r"so\s+the\s+warehouse\s+can\s+process\s+(?:each\s+return|them)\s+correctly[.!]?\s*\Z",
    re.IGNORECASE,
)

@dataclass
class CleanResult:
    text: str
    no_draft: bool = False
    reasons: list[str] = field(default_factory=list)
    # The cut tail, returned without semantic filtering so warnings reach review.
    removed_note: str = ""


@dataclass
class ShouldDraft:
    ok: bool
    reason: str = ""


def _cut_self_talk(text: str) -> tuple[str, str]:
    """Cut from the first self-talk line and return body plus removed tail.

    ADR-015 §2.4 explains why the tail is surfaced instead of keyword-filtered.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not _MARKER_RE.match(stripped):
            continue
        return "\n".join(lines[:i]).rstrip(), "\n".join(lines[i:]).strip()
    return text, ""


def _normalise(block: str) -> str:
    """Whitespace-flatten + lowercase, so two copies that differ only in spacing
    or newlines compare equal."""
    return re.sub(r"\s+", " ", block).strip().lower()


def _repeated_unit(norm: str, k: int) -> str | None:
    """If `norm` (already whitespace-normalised) is exactly ``k`` copies of a base
    string — optionally single-space-joined, because normalisation turns any
    separator (blank line, newline, spaces) between the copies into one space —
    return that base string; otherwise None.

    Only meaningful (>= _MIN_DUP_CHARS) bases count, so we never collapse a tiny
    accidental repeat.
    """
    n = len(norm)
    # Prefer the "joined by one space" reading (the usual case), then the
    # "no separator at all" reading.
    for sep in (1, 0):
        if (n - sep * (k - 1)) % k != 0:
            continue
        unit_len = (n - sep * (k - 1)) // k
        if unit_len < _MIN_DUP_CHARS:
            continue
        base = norm[:unit_len]
        if norm == (" " * sep).join([base] * k):
            return base
    return None


def _raw_prefix_for_norm(raw: str, base: str) -> str | None:
    """Return the shortest prefix of `raw` whose normalisation equals `base`.

    We rebuild the normalised form one character at a time and stop at the exact
    raw offset where the first copy ends. That keeps the first copy in its
    ORIGINAL formatting (capitalisation, line breaks) instead of the flattened
    lower-cased form.
    """
    out: list[str] = []
    prev_space = True  # leading whitespace is dropped by _normalise
    for idx, ch in enumerate(raw):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                prev_space = True
        else:
            out.append(ch.lower())
            prev_space = False
            # compare only once we could plausibly have reached `base`
            if len(out) >= len(base):
                cur = "".join(out).rstrip()
                if cur == base:
                    return raw[: idx + 1]
                if len(cur) > len(base):
                    return None
    return None


def _dedupe_repeats(text: str) -> tuple[str, bool]:
    """If the whole draft is the same content repeated at least twice, keep one.

    Works no matter how the copies are separated (blank line, single newline, or
    a single space) because it compares the whitespace-normalised whole text.
    Returns (possibly-shortened text, whether a duplicate was removed).
    """
    stripped = text.strip()
    norm = _normalise(stripped)
    if len(norm) < _MIN_DUP_CHARS:
        return stripped, False

    # ADR-015 §2.4 — test every bounded copy count, largest first.
    max_k = max(2, len(norm) // _MIN_DUP_CHARS)
    for k in range(max_k, 1, -1):
        base = _repeated_unit(norm, k)
        if base is None:
            continue
        raw_first = _raw_prefix_for_norm(stripped, base)
        if raw_first is not None:
            return raw_first.strip(), True
        # Fallback (should be rare): split on blank lines and keep the first
        # 1/k of the paragraphs when they divide evenly.
        paras = [p for p in re.split(r"\n\s*\n", stripped) if p.strip()]
        if paras and len(paras) % k == 0:
            keep = paras[: len(paras) // k]
            return "\n\n".join(keep).strip(), True

    return stripped, False


def _sentence_count(text: str) -> int:
    """Count conservative sentence boundaries in customer-facing text."""

    masked = _COMMON_ABBREVIATION_RE.sub(
        lambda match: match.group(0).replace(".", " "),
        text,
    )
    return len(list(_SENTENCE_END_RE.finditer(masked)))


def _shorten_to_sentence_limit(text: str) -> tuple[str, str]:
    """Keep only complete sentences within the prompt's concise-output cap."""

    header = _SENSITIVE_HEADER_RE.match(text)
    body = text[header.end():] if header else text
    limit = _MAX_SENSITIVE_SENTENCES if header else _MAX_NORMAL_SENTENCES
    masked = _COMMON_ABBREVIATION_RE.sub(
        lambda match: match.group(0).replace(".", " "),
        body,
    )
    boundaries = list(_SENTENCE_END_RE.finditer(masked))
    if len(boundaries) <= limit:
        return text, ""
    end = boundaries[limit - 1].end()
    shortened_body = body[:end].rstrip()
    removed_tail = body[end:].strip()
    if header:
        shortened = f"{header.group(1)}\n\n{shortened_body}"
    else:
        shortened = shortened_body
    return shortened, removed_tail


def _exceeds_sentence_limit(text: str) -> bool:
    """Return whether a draft exceeds the normal or sensitive sentence cap."""

    header = _SENSITIVE_HEADER_RE.match(text)
    body = text[header.end():] if header else text
    limit = _MAX_SENSITIVE_SENTENCES if header else _MAX_NORMAL_SENTENCES
    return _sentence_count(body) > limit


def _find_action_claim(text: str) -> str:
    """Return the first unsupported operational claim, if any."""

    commitment = _REVIEW_COMMITMENT_RE.search(text)
    if commitment:
        return " ".join(commitment.group(0).split())[:240]
    for match in _ACTION_CLAIM_RE.finditer(text):
        sentence_start = max(
            text.rfind(".", 0, match.start()),
            text.rfind("!", 0, match.start()),
            text.rfind("?", 0, match.start()),
            text.rfind("\n", 0, match.start()),
        ) + 1
        endings = [pos for char in ".!?\n" if (pos := text.find(char, match.end())) >= 0]
        sentence_end = min(endings) + 1 if endings else len(text)
        full_sentence = text[sentence_start:sentence_end]
        if (len(full_sentence) <= 250
                and _RETURN_IDENTIFICATION_INSTRUCTION_RE.fullmatch(full_sentence)):
            continue
        sentence = text[sentence_start:match.end()]
        uncertainty = _UNCONFIRMED_ACTION_RE.search(sentence)
        # Only an explicitly uncertain passive outcome is exempt. A subsequent
        # independent promise, contrast, punctuation or first-person action is
        # still checked. Never remove an arbitrary negated prefix from a draft.
        if (uncertainty and re.match(r'(?:your|the|a|an|order)\b',match.group(),re.I)
                and not re.search(r'\b(?:but|however|yet|instead|then)\b',uncertainty.group(),re.I)):
            continue
        pending_action = _PENDING_ACTION_RE.search(sentence)
        if pending_action and pending_action.end() == match.end() - sentence_start:
            continue
        # Keep the reviewer diagnostic bounded and on one line. The draft
        # itself is rejected, so this text can never reach the customer-facing
        # composer.
        return " ".join(match.group(0).split())[:240]
    return ""


# Short internal output markers are review instructions, never customer replies.
_INTERNAL_NO_REPLY_RE = re.compile(
    r"(?:no\s+(?:reply|response|draft)\s+(?:is\s+)?(?:needed|required|necessary))"
    r"(?:[.!]?|\s*(?:[—–:-]|because)\s*[^\r\n]{1,360}[.!]?)",
    re.IGNORECASE,
)


def clean_draft(text: str) -> CleanResult:
    """Clean an AI draft before it is shown to a human / posted anywhere."""
    if text is None or not str(text).strip():
        return CleanResult(text="", no_draft=True, reasons=["empty draft"])

    out = str(text)
    if len(out) <= 420 and _INTERNAL_NO_REPLY_RE.fullmatch(out.strip()):
        return CleanResult(text="", no_draft=True,
                           reasons=["internal no-reply instruction is not a customer draft"])
    reasons: list[str] = []
    removed: list[str] = []

    # Four shortening-only passes reach a bounded fixed point.
    for _ in range(4):
        out, cut = _cut_self_talk(out)
        if cut:
            removed.append(cut)
            if "stripped model self-commentary" not in reasons:
                reasons.append("stripped model self-commentary")

        out, deduped = _dedupe_repeats(out)
        if deduped and "removed duplicated draft body" not in reasons:
            reasons.append("removed duplicated draft body")

        if not (cut or deduped):
            break

    out = out.strip()
    note = "\n".join(removed).strip()
    if not out:
        return CleanResult(
            text="", no_draft=True,
            reasons=reasons + ["nothing left after cleaning"],
            removed_note=note,
        )
    if _SPANISH_REVIEW_COMMITMENT_RE.search(out):
        return CleanResult(
            text="", no_draft=True,
            reasons=reasons + ["unsupported Spanish review commitment requires a human draft"],
            removed_note=note,
        )
    shortened, removed_tail = _shorten_to_sentence_limit(out)
    if shortened != out:
        out = shortened
        reasons.append(
            "shortened draft to concise-output sentence limit"
        )
        if removed_tail:
            removed.append(removed_tail)
            note = "\n".join(removed).strip()

    action_claim = _find_action_claim(out)
    if action_claim:
        return CleanResult(
            text="",
            no_draft=True,
            reasons=reasons + [
                "rejected unsupported operational promise"
            ],
            removed_note="\n".join(part for part in (note, action_claim) if part),
        )
    return CleanResult(text=out, no_draft=False, reasons=reasons,
                       removed_note=note)


# ADR-015 §2.4 — acknowledgement suppression is a bounded token allow-list.

# A genuine "this conversation is finished" signal. One of these must be
# present before anything is suppressed.
_ACK_ANCHORS = frozenset({
    "thanks", "thank", "thanx", "thx", "ty", "tysm", "cheers",
    "appreciate", "appreciated", "appreciation",
    "ok", "okay", "kk", "noted", "understood", "received", "got",
    "perfect", "great", "awesome", "excellent", "brilliant", "amazing",
})

# Deliberately short: any word that could carry a complaint or decision stays out.
_ACK_FILLER = frozenset({
    "a", "again", "all", "and", "at", "bye", "dear", "everyone", "folks",
    "for", "from", "guys", "hello", "hey", "hi", "in", "it", "its", "lot",
    "lots", "love", "loved", "lovely", "me", "much", "my", "night", "nice",
    "of", "oh", "on", "regards", "so", "super", "team", "that", "the",
    "this", "to", "too", "u", "very", "we", "weekend", "with", "you",
    "your", "yours", "x", "xx", "xxx",
})

# The subset that can stand alone as a whole message and mean nothing but
# "we are done here".
_GRATITUDE_ANCHORS = frozenset({
    "thanks", "thank", "thanx", "thx", "ty", "tysm", "cheers",
    "appreciated", "appreciation",
})

# Words that ANSWER a question rather than close a conversation. If the agent
# last asked "shall I cancel order #10234 before it ships?", every one of
# these is a yes - and suppressing the reply stores an empty console card with
# no action controls while the order ships.
_DECISION_ANCHORS = frozenset({
    "ok", "okay", "kk", "noted", "understood", "received", "got",
})

_ACK_ALLOWED = _ACK_ANCHORS | _ACK_FILLER

# Word characters, Unicode-aware. A Latin-only [0-9a-z]+ found NO tokens in
# Chinese, Cyrillic, Hebrew or Arabic, so "ok 我要退款" ("ok, I want a refund")
# looked like a bare "ok" and was suppressed.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Emoji that mean "we are done here". A message made only of these is an ack.
# Anything NOT on this list — every angry or confused emoji — is content.
_SAFE_EMOJI = (
    "\U0001F44D", "\U0001F64F", "\u2764", "\U0001F60A", "\U0001F642",
    "\U0001F600", "\U0001F601", "\U0001F970", "\U0001F60D", "\U0001F44C",
    "\u2705", "\U0001F389", "\U0001F495", "\U0001F49B", "\U0001F44F",
    "\U0001F929", "\u2b50", "\U0001F31F", "\U0001F338", "\U0001F49C",
)

# Skin-tone modifiers, variation selectors and zero-width characters. Stripped
# before anything else: "\U0001F44D\U0001F3FD" (a thumbs-up with a skin tone) is
# still a thumbs-up, and a zero-width space is still an empty message.
_DECORATION = (
    "".join(chr(c) for c in range(0x1F3FB, 0x1F400))   # skin tones
    + "\ufe0e\ufe0f"                                   # variation selectors
    + "\u200b\u200c\u200d\ufeff"                       # zero-width
)

# Punctuation that carries no meaning on its own. "?" is deliberately absent —
# a question mark is always content, wherever it appears.
_INERT_PUNCT = " \t\r\n.,~-\u2013\u2014\u2026'\"()[]:;*_/\\"


def _strip_decoration(text: str) -> str:
    for ch in _DECORATION:
        if ch in text:
            text = text.replace(ch, "")
    for emoji in _SAFE_EMOJI:
        if emoji in text:
            text = text.replace(emoji, "")
    return _HAPPY_EMOTICON_RE.sub(" ", text)


# Sad ASCII emoticons are content; (?!/) keeps URL schemes out of the match.
_EMOTICON_RE = re.compile(r"[:;=8][-'~^]?(?:[(\[|\\<]|/(?!/))")

# ...and the happy ones are stripped like an emoji, so their mouth character
# does not survive as a stray token ("thank you :D" tokenised a bare "d").
_HAPPY_EMOTICON_RE = re.compile(r"[:;=8][-'~^]?[)\]>DdPpOo3*]+")


# ADR-015 §2.4 — subject noise uses one whitespace class to stay linear.
_SUBJECT_NOISE_RE = re.compile(
    r"^\s*((re|fw|fwd|aw|sv)\s*:\s*)+|"
    r"\border[\s#]*\d+|#\s*\d+|"
    r"\b(no\s+subject|message\s+from\s+(the\s+)?contact\s+form|"
    r"contact\s+form|order\s+confirmation|your\s+order|"
    r"buttons\s+bebe|customer\s+(service|support)|support\s+request|"
    r"new\s+message|website\s+enquiry|enquiry|update)\b|"
    r"\b(your|our|my|the|a|an|order|orders|ticket|case|ref|reference)\b",
    re.IGNORECASE,
)


def _carries_no_content(value: str | None) -> bool:
    """True when this ONE piece of text has nothing in it to answer.

    Evaluated per field. should_draft() calls it separately on the message and
    on the subject, and suppresses only when BOTH are empty — concatenating
    them was a token union, so an ack subject could supply the missing anchor
    and silently suppress a real question in the body.
    """
    if value is None:
        return True
    text = _strip_decoration(str(value))
    if not text.strip():
        return True

    # A question mark is content, full stop. The token scan drops punctuation,
    # so "Have you received it?" used to read as bare filler plus the anchor
    # "received" and was suppressed.
    if "?" in text:
        return False

    # A sad or frustrated face is content, exactly like an angry emoji.
    if _EMOTICON_RE.search(text):
        return False

    tokens = _TOKEN_RE.findall(text.lower())

    # Anything left after removing words and inert punctuation is a symbol the
    # customer chose deliberately — an angry emoji, a currency sign. "!" only
    # counts as inert when there are words around it, so a bare "!" is content.
    residue = _TOKEN_RE.sub(" ", text)
    inert = _INERT_PUNCT + ("!" if tokens else "")
    if any(ch not in inert and not ch.isspace() for ch in residue):
        return False

    if not tokens:
        return True

    if not (all(t in _ACK_ALLOWED for t in tokens)
            and any(t in _ACK_ANCHORS for t in tokens)):
        return False

    # A decision word remains content regardless of surrounding gratitude.
    if any(t in _DECISION_ANCHORS for t in tokens):
        return False

    # ...and a very short reply still needs an unambiguous gratitude word,
    # so a bare "perfect" or "great" (equally an answer to a question) drafts.
    if len(tokens) <= 2 and not any(t in _GRATITUDE_ANCHORS for t in tokens):
        return False

    return True


def should_draft(message: str, subject: str = "") -> ShouldDraft:
    """Return ok=False only when there is genuinely nothing to answer.

    A nonempty pure acknowledgment takes precedence over inherited subjects.
    An email with a blank body and a real subject line
    ("Do you have this in 6-9 months?") is a real question; equally, a thread
    whose subject is "Thanks" must not silence a body that asks something.

    Runs in linear time on the length of the input. Do not reintroduce a
    whole-message regex here — see the note above.
    """
    # ADR-015 §2.4 — collapse padding first; over-limit input drafts, never truncates.
    message = " ".join(str(message or "").split())
    if len(message) > _MAX_GATE_MESSAGE:
        return ShouldDraft(True)
    # Subject and body share the same fail-open-on-length rule.
    subject = " ".join(str(subject or "").split())
    if len(subject) > _MAX_GATE_SUBJECT:
        return ShouldDraft(True)

    if not _carries_no_content(message):
        return ShouldDraft(True)
    # A reply-thread subject is inherited context, not a new request. Keep
    # blank-body subject questions and decision words ("yes", "okay") alive.
    if message.strip():
        return ShouldDraft(False, "no question to answer (thanks/ack only)")
    if not _carries_no_content(_SUBJECT_NOISE_RE.sub(" ", subject)):
        return ShouldDraft(True)

    combined = f"{subject or ''} {message or ''}"
    if not _strip_decoration(combined).strip():
        return ShouldDraft(False, "empty message")
    return ShouldDraft(False, "no question to answer (thanks/ack only)")
