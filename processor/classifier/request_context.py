"""Disambiguate routine service wording without hiding separate urgent claims."""
import re

_PORTAL = r'(?:gift[ -]return\s+|return\s+)?portal'
_BROKEN_PORTAL = re.compile(
    rf'\b(?:broken\s+{_PORTAL}|{_PORTAL}\s+(?:is|seems|appears)\s+broken)\b',
    re.IGNORECASE,
)
_PICKUP_NOTICE = re.compile(
    r"\b(?:have\s+not|haven['’]?t|did\s+not|didn['’]?t|never|not)\s+"
    r"(?:received|receive|gotten|got)\s+(?:(?:a|the|my)\s+)?"
    r"ready[ -]for[ -]pickup\s+(?:notice|notification|email|message)\b",
    re.IGNORECASE,
)


def service_issue_view(text: str) -> str:
    """Mask only the service noun's ambiguous predicate for keyword matching.

    The customer text, model prompt, and stored source are never changed.
    Additional defects/refunds, urgency and inherited sensitive intents still
    pass through the normal classifier. Explicit ongoing waits stay visible.
    """
    text = _BROKEN_PORTAL.sub('return portal unavailable', text)
    def notice(match):
        if re.search(r'\bstill\s*$', text[max(0,match.start()-12):match.start()],re.I):
            return match.group()
        return 'awaiting pickup readiness notification'
    return _PICKUP_NOTICE.sub(notice, text)
