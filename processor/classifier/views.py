"""Text views and structural signals used by the classifier."""

from __future__ import annotations

import re

from . import data as _data
from . import matching as _matching
from . import patterns as _patterns  # ponytail: guards folded here (3.3)


# ADR-014 §2.2 — bound/fold/filter the port view; main keeps raw ticket text.
_MAX_SCAN_CHARS = 60_000
_TRUNCATION_SENTINEL = "zqxtruncatedzqx"
_SMART_QUOTES = {
    "\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u00b4": "'",
    "\u201c": '"', "\u201d": '"',
}
_EXCLAIM_RE = re.compile(r"!{3,}")
_POSITIVE_RE = re.compile(
    r"\b(thank|thanks|thankyou|thx|love|loved|loving|adorable|perfect|beautiful|"
    r"gorgeous|cute|amazing|wonderful|excellent|obsessed|delighted|thrilled|"
    r"pleased|happy|brilliant|fantastic|lovely|great|awesome|best|nice|super|"
    r"fab|fabulous|appreciate|appreciated|recommend|quick|fast|impressed|"
    r"worth|favourite|favorite|soft|softer|softest|comfy|cosy|cozy|snug|"
    r"cutest|recommended|glad|happier|happiest|adore|adored|stunning|"
    r"well\s+spent|every\s+penny|perfect\w*|beautiful\w*|gorgeous\w*|"
    r"adorab\w*|amazing\w*|wonderful\w*|brilliant\w*|fantastic\w*|"
    r"excellent\w*|delight\w*|impress\w*|appreciat\w*|stunning\w*|"
    r"chuffed|spot\s+on|top\s+notch|bang\s+on|smashing|quality|made\s+up|"
    r"over\s+the\s+moon|well\s+made|well\s+packaged|cannot\s+fault)\b",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"\b(never\s+(?:received|receive|arrived|arrive|came|come|got|get|"
    r"turned\s+up|show(?:ed|n)\s+up|delivered|deliver|shipped|ship|sent|send|"
    r"replied|reply|responded|respond|answered|answer|heard|resolved|"
    r"refunded|works?|worked|shopping|buying|ordering|using)|"
    r"never\s+(?:\w+\s+){0,3}again|refund|damaged|broken|wrong|missing|late|"
    r"angry|furious|unacceptable|terrible|awful|worst|horrible|disappointed|"
    r"disappointing|ridiculous|useless|rubbish|scam|fraud|cancel|complaint|"
    r"complain|nobody|noone|ignored|ignoring|refused|refusing|disgusting|"
    r"disgrace|appalling|pathetic|unhappy|fed\s+up|sick\s+of|had\s+enough|"
    r"n[o']?t\s+worth|waste|wasted|poor\s+quality|bad\s+quality|"
    r"cheap\s+quality|falling\s+apart|n[o']?t\s+(?:impress\w*|"
    r"acceptable|good\s+enough)|shocking|shockingly|disgraceful|"
    r"frustrat\w+|livid|fuming|seething|appalled|outrage\w*|"
    r"no\s+(?:reply|response|answer|word|update)|chasing|wits\s+end|"
    r"joke|shambles|fiasco|third\s+time|3rd\s+time)\b",
    re.IGNORECASE,
)
_QUOTE_LINE_RE = re.compile(r"^\s*(?:>|\|)")
_QUOTE_HEADER_RE = re.compile(
    r"^\s*(?:"
    r"on\s.{0,200}\d.{0,160}\swrote:"
    r"|.{0,120}<[^>@\s]{1,64}@[^>\s]{1,64}>\s+wrote:"
    r"|-{2,}\s*(?:original message|forwarded message)"
    r"|begin\s+forwarded\s+message:"
    r"|_{5,}\s*$"
    r"|(?:from|sent|to|subject):\s.{0,200}$"
    r")",
    re.IGNORECASE,
)
_CAPS_WORD_RE = re.compile(r"\b[A-Z]{2,}\b")
_WORD_RE = re.compile(r"\b[A-Za-z]{2,}\b")

_SHOUT_MIN_WORDS = 2
_SHOUT_MIN_RATIO = 0.6
_SUSTAINED_MIN_CAPS = 8
_SUSTAINED_MIN_RATIO = 0.9
_SUSTAINED_MIN_GRAMMAR = 2

_STORE_BOILERPLATE_RE = re.compile(
    r"(reply\s+to\s+this\s+email|do\s+not\s+reply|unsubscribe|"
    r"view\s+this\s+email|this\s+email\s+was\s+sent|"
    r"thanks\s+for\s+(reaching\s+out|getting\s+in\s+touch|your\s+patience)|"
    r"we\s+(are|'re)\s+(looking\s+into|sorry\s+to\s+hear|so\s+sorry)|"
    r"we\s+will\s+(get\s+back|be\s+in\s+touch|look\s+into)|"
    r"working\s+days|%\s*off|flash\s+sale|shop\s+now|"
    r"(just|simply|hit)\s+reply|let\s+us\s+know|get\s+in\s+touch\s+with\s+us|"
    r"our\s+returns?\s+policy|terms\s+and\s+conditions|all\s+rights\s+reserved|"
    r"you\s+are\s+receiving\s+this|"
    r"our\s+(customer\s+)?(care|support)\s+team)",
    re.IGNORECASE,
)


def _bound(text: str) -> str:
    if len(text) <= _MAX_SCAN_CHARS:
        return text
    head = _MAX_SCAN_CHARS * 3 // 4
    tail = _MAX_SCAN_CHARS - head - len(_TRUNCATION_SENTINEL)
    return text[:head] + _TRUNCATION_SENTINEL + text[-tail:]


def _fold_smart_quotes(text: str) -> str:
    for fancy, plain in _SMART_QUOTES.items():
        if fancy in text:
            text = text.replace(fancy, plain)
    return text


def _normalise_text(value: str) -> str:
    return _fold_smart_quotes(_bound(str(value or "")))


def _drop_store_boilerplate(text: str) -> str:
    paragraphs = re.split(r"\n\s*\n", text or "")
    kept = [paragraph for paragraph in paragraphs
            if not _STORE_BOILERPLATE_RE.search(paragraph)]
    result = "\n\n".join(kept).strip()
    return result or (text or "")


def _strip_quoted_history(message_text: str) -> str:
    kept: list[str] = []
    seen_content = False
    for line in (message_text or "").splitlines():
        if _QUOTE_LINE_RE.match(line):
            continue
        if _QUOTE_HEADER_RE.match(line):
            if seen_content:
                break
            continue
        kept.append(line)
        if line.strip():
            seen_content = True
    fresh = _drop_store_boilerplate("\n".join(kept).strip())
    return fresh or (message_text or "")


def _is_shouting(message_text: str) -> bool:
    fresh = _strip_quoted_history(message_text or "")
    if not fresh:
        return False
    positive_only = bool(_POSITIVE_RE.search(fresh)) and not _NEGATIVE_RE.search(fresh)
    all_caps = _CAPS_WORD_RE.findall(fresh)
    all_words = _WORD_RE.findall(fresh)
    if not all_words:
        return False
    caps = [word for word in all_caps if word not in _data._CAPS_STOPWORDS]
    words = [word for word in all_words if word.upper() not in _data._CAPS_STOPWORDS]
    if (len(caps) >= _SHOUT_MIN_WORDS and words
            and len(caps) / len(words) >= _SHOUT_MIN_RATIO
            and any(word in _data._SHOUT_ANCHORS for word in caps)):
        if not positive_only or any(word in _data._SHOUT_HARD_ANCHORS for word in caps):
            return True
    if (not positive_only and len(all_caps) >= _SUSTAINED_MIN_CAPS
            and len(all_caps) / len(all_words) >= _SUSTAINED_MIN_RATIO
            and sum(word in _data._SHOUT_GRAMMAR for word in all_caps)
            >= _SUSTAINED_MIN_GRAMMAR
            and any(word in _data._SHOUT_COMPLAINT_VERBS for word in all_caps)):
        return True
    return False


def _is_exclaiming(message_text: str) -> bool:
    fresh = _strip_quoted_history(message_text or "")
    if not _EXCLAIM_RE.search(fresh):
        return False
    return not (_POSITIVE_RE.search(fresh) and not _NEGATIVE_RE.search(fresh))


def _weak_matches(text: str) -> list[str]:
    if not _patterns._ORDER_CONTEXT_RE.search(text):
        return []
    found = _matching._find_matches(text, _data._WEAK_UNGUARDED)
    browsing = (_patterns._BROWSING_QUESTION_RE.search(text)
                and not _patterns._PROBLEM_CONTEXT_RE.search(text))
    if not browsing:
        for table in (_data._WEAK_DAMAGE, _data._WEAK_OMISSION):
            found.extend(match for match in _matching._find_matches(text, table)
                         if match not in found)
    return found


__all__ = [
    "_MAX_SCAN_CHARS", "_TRUNCATION_SENTINEL", "_SMART_QUOTES",
    "_EXCLAIM_RE", "_POSITIVE_RE", "_NEGATIVE_RE", "_QUOTE_LINE_RE",
    "_QUOTE_HEADER_RE", "_CAPS_WORD_RE", "_WORD_RE", "_SHOUT_MIN_WORDS",
    "_SHOUT_MIN_RATIO", "_SUSTAINED_MIN_CAPS", "_SUSTAINED_MIN_RATIO",
    "_SUSTAINED_MIN_GRAMMAR", "_STORE_BOILERPLATE_RE", "_bound",
    "_fold_smart_quotes", "_normalise_text", "_drop_store_boilerplate",
    "_strip_quoted_history", "_is_shouting", "_is_exclaiming", "_weak_matches",
]
