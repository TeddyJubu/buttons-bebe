"""Compiled patterns that coordinate the classifier's rule families."""

from __future__ import annotations

import re


_TRADE_ENQUIRY_RE = re.compile(
    r"\b(wholesale|stockist|stock\s+your|stocking\s+your|trade\s+(?:account|"
    r"price|pricing|enquiry|inquiry)|reseller|retail\s+account|bulk\s+order|"
    r"collab|collaboration|brand\s+partnership|press\s+(?:sample|enquiry|"
    r"inquiry|pack)|pr\s+(?:sample|enquiry|inquiry)|influencer|"
    r"gifting\s+opportunity|media\s+pack)\b",
    re.IGNORECASE,
)
_ESCALATE_RE = re.compile(r"\bescalate\s+(?:this|my)\b", re.IGNORECASE)
_ESCALATE_NEGATED_RE = re.compile(
    r"\b(?:do\s?n'?t|do\s+not|no\s+need\s+to|not?\s+need|rather\s+not|"
    r"please\s+do\s?n'?t)\s+(?:\w+\s+){0,2}escalate\b",
    re.IGNORECASE,
)
_FOLLOWUP_PATTERN = re.compile(
    r"(following\s+up|follow\s?up|still\s+(no|waiting)|yet\s+again|"
    r"any\s+(update|response|reply|answer)|"
    r"(2nd|3rd|second|third|fourth)\s+(time|attempt|message|email|follow))",
    re.IGNORECASE,
)
_MAIN_HIGH_SENSITIVE_PATTERN = re.compile(
    r"\b(final\s+sale|change\s+(?:my\s+)?(?:shipping\s+)?address|"
    r"wrong\s+address|update\s+(?:my\s+)?address|new\s+address|"
    r"cancel(?:lation)?(?:\s+(?:my\s+)?(?:order|item|purchase))?)\b",
    re.IGNORECASE,
)
_HIGH_SENSITIVE_PATTERN = re.compile(
    r"\b(final\s+sale|change\s+(?:my\s+)?(?:shipping\s+)?address|"
    r"wrong\s+address|update\s+(?:my\s+)?address|new\s+address|"
    r"cancel(?:lation)?(?:\s+(?:my\s+)?(?:order|item|purchase))?|"
    r"(?:has\s?n'?t|has\s+not|have\s+not|have\s?n'?t)\s+"
    r"(?:arrived|turned\s+up|shown\s+up)|"
    r"still\s+(?:has\s?n'?t|have\s?n'?t|not)\s+(?:arrived|come|received)|"
    r"nothing\s+(?:has\s+|had\s+)?(?:arrived|come|turned\s+up|"
    r"showed\s+up|been\s+delivered)|not\s+(?:been\s+)?delivered)\b",
    re.IGNORECASE,
)


__all__ = [
    "_TRADE_ENQUIRY_RE", "_ESCALATE_RE", "_ESCALATE_NEGATED_RE",
    "_FOLLOWUP_PATTERN", "_MAIN_HIGH_SENSITIVE_PATTERN",
    "_HIGH_SENSITIVE_PATTERN",
    "_BROWSING_QUESTION_RE", "_ORDER_CONTEXT_RE", "_PROBLEM_CONTEXT_RE",
]


# --- Input-shape guards (folded from guards/ 3.3; single-pattern modules) ---
_BROWSING_QUESTION_RE = re.compile(
    r"\b(can\s+i|could\s+i|do\s+you|does\s+the|does\s+it|is\s+there|are\s+there|"
    r"am\s+i|how\s+do\s+i|how\s+can\s+i|how\s+to|how\s+would\s+i|"
    r"what'?s?\s+the\s+best\s+way|any\s+tips|is\s+it\s+possible|would\s+it\s+be|"
    r"can\s+you|could\s+you|would\s+you|will\s+you|do\s+i\s+need|"
    r"where\s+do\s+i|what\s+do\s+i|which\s+(?:size|one|colour|color)|"
    r"is\s+(?:the|this|that|it)\b|are\s+(?:the|these|those|they)\b|"
    r"was\s+(?:the|this|that|it)\b|were\s+(?:the|these|those|they)\b|"
    r"did\s+(?:the|this|that|it|you|they)\b)",
    re.IGNORECASE,
)
_ORDER_CONTEXT_RE = re.compile(
    r"\b("
    r"arrived|delivered|delivery|received|came\s+(?:in|today|yesterday)|"
    r"parcel|parcels|package|packages|shipment|the\s+box|my\s+box|in\s+the\s+box|"
    r"tracking|courier|dispatched|"
    r"my\s+(?:order|purchase|item|items|delivery|parcel|package)|"
    r"order[\s#]*\d|#\s*\d{3,}"
    r")\b",
    re.IGNORECASE,
)
_PROBLEM_CONTEXT_RE = re.compile(
    r"\b((?:arrived|came|turned\s+up|delivered|showed\s+up)\s+"
    r"(?:(?:completely|totally|badly|slightly|already|all|absolutely)\s+)?"
    r"(?:ripped|stained|torn|damaged|open|soaked|filthy|dirty)|"
    r"(?:a|an|the|one|another)\s+(?:ripped|stained|torn|damaged)\s+\w|"
    r"(?:tracking|courier|carrier|parcel|package)\s+"
    r"(?:has\s?n'?t|have\s?n'?t|has\s+not|is\s+stuck|stopped|shows\s+nothing|"
    r"says\s+nothing|never|is\s+lost|went\s+missing|has\s+vanished)|"
    r"no\s+tracking|tracking\s+number\s+does\s?n'?t|"
    r"still\s+waiting|still\s+not|no\s+update|"
    r"has\s?n'?t\s+moved|has\s+not\s+moved|"
    r"where\s+is|where\s+are|chasing|chase\s+this|follow(?:ing)?\s?up|"
    r"never\s+(?:came|arrived|turned\s+up)|why\b|"
    r"still\s+(?:has\s?n'?t|have\s?n'?t|no|nothing)|"
    r"(?:waiting|waited)\s+(?:for\s+)?(?:over\s+)?(?:(?:\d+|a|two|three)\s*)?"
    r"(?:days?|weeks?|months?)|"
    r"been\s+(?:over\s+)?(?:\d+|two|three|four)\s*(?:days?|weeks?|months?))",
    re.IGNORECASE,
)
