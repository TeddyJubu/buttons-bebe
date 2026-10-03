"""Small validation helpers shared by manual operations and export imports."""
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from uuid import uuid4

from .policy import Invalid

STATUSES = ("open", "waiting_customer", "waiting_team", "snoozed", "closed")
PRIORITIES = ("low", "normal", "high", "critical")


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def uid():
    return str(uuid4())


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def text(value, field, limit=500, required=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise Invalid(f"{field}: expected text, at most {limit} characters.")
    value = value.strip()
    if required and not value:
        raise Invalid(f"{field}: cannot be empty.")
    return value


def external_id(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise Invalid(f"{field}: a stable source ID is required.")
    return text(str(value), field, 128, True)


def timestamp(value, field):
    raw = text(value, field, 64, True)
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError as exc:
        raise Invalid(f"{field}: expected an ISO timestamp with a timezone.") from exc


def choice(value, options, field):
    if value not in options:
        raise Invalid(f"{field}: unsupported value.")
    return value


def email(value):
    value = text(value, "email", 320)
    if value and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
        raise Invalid("email: enter a valid address or leave it empty.")
    return value


class PlainHTML(HTMLParser):
    """Extract display text only; never return markup or resolve URLs."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_html(value):
    parser = PlainHTML()
    parser.feed(text(value, "body_html", 2_000_000))
    return "".join(parser.parts).strip()
