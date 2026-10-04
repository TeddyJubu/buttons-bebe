"""Pure intake cleanup. No network, no provider writes, no archived-body fetches."""

from __future__ import annotations

from html.parser import HTMLParser
import re

CLEANUP_VERSION = "intake-1"
CONTRACT_KEYS = (
    "display_text",
    "current_text",
    "display_source",
    "current_source",
    "original_content",
    "original_field",
    "history_available",
    "source_truncated",
    "cleanup_version",
)

_FULL_FIELDS = ("body_text", "body_html")
_DISPLAY_FIELDS = ("body_text", "body_html", "text", "stripped_text", "stripped_html", "excerpt")
_CURRENT_FIELDS = ("stripped_text", "stripped_html", "body_text", "body_html", "text", "excerpt")
_ORIGINAL_FIELDS = ("body_text", "body_html", "text", "stripped_text", "stripped_html", "excerpt")

_HIDDEN_TAGS = frozenset({
    "script", "style", "head", "title", "meta", "link", "noscript",
    "template", "iframe", "object",
})
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "source", "track", "wbr",
})
_BLOCK_START = frozenset({"p", "div", "br", "li", "tr"})
_BLOCK_END = frozenset({"p", "div", "li", "tr"})

_HIDDEN_STYLE = re.compile(
    r"(?:display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all)",
    re.IGNORECASE,
)
_PREHEADER_CLASS = re.compile(
    r"(?:^|\s)(?:preheader|gmail[-_]?preheader)(?:\s|$)",
    re.IGNORECASE,
)
_HTML_TAG = re.compile(
    r"</?(?:html|body|div|p|br|table|tr|td|blockquote|span|a|ul|ol|li|"
    r"b|strong|em|i|pre|img|font|style|script|head|meta|link)\b",
    re.IGNORECASE,
)
_FONT_URL = re.compile(
    r"https?://(?:fonts\.googleapis\.com|fonts\.gstatic\.com|use\.typekit\.net|"
    r"fast\.fonts\.net|fonts\.shopifycdn\.com)/[^\s<>()]+"
    r"|https?://[^\s<>()]+?\.(?:woff2|woff|ttf|otf|eot)(?:\?[^\s<>()]*)?",
    re.IGNORECASE,
)
_CONTROLS = re.compile(
    r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b\ufeff\u00ad\u2060"
    r"\u202a-\u202e\u2066-\u2069]"
)
_PADDING_RUN = re.compile(
    r"(?:[\u00a0\u200b\u200c\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\ufeff\u2060]){8,}"
)

# Quote markers are mail-client artifacts. Generic From/To/Subject lines stay,
# because a customer can write those. A bottom-posted reply under a leading
# quote survives. An all-quoted body falls back to the whole text.
_SEPARATOR_RE = re.compile(
    r"-{2,}\s*(?:original message|forwarded message)\s*-{2,}"
    r"|begin\s+forwarded\s+message:",
    re.IGNORECASE,
)
_GLUED_MARKERS = re.compile(
    r"(-{2,}\s*(?:original message|forwarded message)\s*-{2,}"
    r"|begin\s+forwarded\s+message:)",
    re.IGNORECASE,
)
_SIGNATURE_RE = re.compile(
    r"(?:\n|^|(?<=[a-z0-9,.!?]))sent from my\s+\S[^\n]*(?:\n|$)",
    re.IGNORECASE,
)
_QUOTE_LINE_RE = re.compile(r"^\s*(?:>|\|)")
_QUOTE_HEADER_RE = re.compile(
    r"^\s*(?:"
    r"on\s.{0,200}\d.{0,160}\swrote:"
    r"|.{0,120}<[^>@\s]{1,64}@[^>\s]{1,64}>\s+wrote:"
    r")\s*$",
    re.IGNORECASE,
)
_URL_END = re.compile(r"(?:https?://|www\.)\S+$", re.IGNORECASE)
_URL_START = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_LIST_LINE = re.compile(r"(?:[-*•]|\d+[.)])\s")
_SOURCE_FIELDS = ("body_text", "body_html", "text", "stripped_text", "stripped_html", "excerpt")


def _empty_contract() -> dict:
    return {
        "display_text": "",
        "current_text": "",
        "display_source": None,
        "current_source": None,
        "original_content": "",
        "original_field": None,
        "history_available": False,
        "source_truncated": False,
        "cleanup_version": CLEANUP_VERSION,
    }


def _usable(message: dict, key: str) -> str | None:
    value = message.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _first(message: dict, keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    for key in keys:
        value = _usable(message, key)
        if value is not None:
            return key, value
    return None, None


def _attr(attrs, name: str) -> str:
    for key, value in attrs:
        if str(key).lower() == name:
            return value or ""
    return ""


def _tag_hidden(tag: str, attrs) -> bool:
    if tag in _HIDDEN_TAGS:
        return True
    for key, value in attrs:
        name = str(key).lower()
        if name == "hidden":
            return True
        if name == "style" and value and _HIDDEN_STYLE.search(value):
            return True
        if name == "class" and value and _PREHEADER_CLASS.search(value):
            return True
    return False


def _is_font_url(value: str) -> bool:
    return bool(_FONT_URL.search(value))


def _meaningful_href(attrs) -> str:
    href = _attr(attrs, "href").strip()
    if not href:
        return ""
    lowered = href.lower()
    if lowered.startswith(("javascript:", "data:", "vbscript:")):
        return ""
    if not lowered.startswith(("http://", "https://", "mailto:", "tel:")):
        return ""
    if _is_font_url(href):
        return ""
    return href


def _replace_padding(value: str) -> str:
    def repl(match: re.Match) -> str:
        start, end = match.span()
        left = value[start - 1] if start else ""
        right = value[end] if end < len(value) else ""
        if left and right and not left.isspace() and not right.isspace():
            return " "
        return ""

    return _PADDING_RUN.sub(repl, value)


def _polish(value: str) -> str:
    value = _FONT_URL.sub("", value)
    value = _CONTROLS.sub("", value)
    value = _replace_padding(value)
    value = value.replace("\u00a0", " ")
    return value


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
        self.in_quote = 0
        self.anchor: str | None = None
        self.anchor_at = 0

    def handle_starttag(self, tag, attrs):
        attrs = attrs or []
        if self.hidden:
            if tag not in _VOID_TAGS:
                self.hidden += 1
            return
        if _tag_hidden(tag, attrs):
            if tag not in _VOID_TAGS:
                self.hidden += 1
            return
        if tag == "blockquote":
            self.in_quote += 1
        elif tag in _BLOCK_START:
            self.parts.append("\n")
        if tag == "a":
            href = _meaningful_href(attrs)
            self.anchor = href or None
            self.anchor_at = len(self.parts)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag not in _VOID_TAGS:
                self.hidden -= 1
            return
        if tag == "a" and self.anchor:
            self._append_href(self.anchor)
            self.anchor = None
        if tag == "blockquote" and self.in_quote:
            self.in_quote -= 1
        elif tag in _BLOCK_END:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden or not data:
            return
        self._append_data(data)

    def _append_data(self, data: str) -> None:
        if not self.in_quote:
            self.parts.append(data)
            return
        for line in data.split("\n"):
            self.parts.append("\n> " + line)

    def _append_href(self, href: str) -> None:
        caption = "".join(self.parts[self.anchor_at:])
        visible = caption.strip()
        if not visible:
            self._append_data(href)
            return
        if href in caption or visible.lower().startswith(("http://", "https://", "mailto:", "tel:")):
            return
        self._append_data(f" ({href})")


def _visible_text(value: str) -> str:
    parser = _VisibleText()
    parser.feed(value)
    parser.close()
    text = _polish("".join(parser.parts))
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    return text.strip()


def _display_plain(value: str) -> str:
    text = _polish(value).strip()
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    return text.strip()


def strip_reply_artifacts(value: str) -> str:
    """Cut quoted history and mobile signatures from the current message."""
    value = value or ""
    if not value.strip():
        return value
    value = _GLUED_MARKERS.sub(r"\n\1", value)
    kept = []
    seen_content = False
    for line in value.splitlines():
        if _QUOTE_LINE_RE.match(line):
            continue
        if _SEPARATOR_RE.search(line):
            if seen_content:
                break
            continue
        if _QUOTE_HEADER_RE.match(line):
            if seen_content:
                break
            continue
        kept.append(line)
        if line.strip():
            seen_content = True
    joined = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "\n".join(kept))
    fresh = _SIGNATURE_RE.sub("\n", joined).strip()
    if fresh:
        return fresh
    if _SIGNATURE_RE.search(value):
        return ""
    return value.strip()


def _looks_html(value: str) -> bool:
    return bool(_HTML_TAG.search(value))


def _unwrap_stripped_text(value: str) -> str:
    """Join Gorgias stripped_text soft wraps. Lists, paragraphs, and URLs stay."""
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[str] = []
    previous = ""
    for line in lines:
        source_line = previous.rstrip()
        continuation = line.lstrip()
        soft_wrap = (
            bool(result)
            and 60 <= len(source_line) <= 90
            and bool(continuation)
            and continuation[0].islower()
            and source_line[-1] not in ".!?;:"
            and not line.startswith((" ", "\t"))
            and _URL_END.search(source_line) is None
            and _URL_START.match(continuation) is None
            and _LIST_LINE.match(continuation) is None
        )
        if soft_wrap:
            result[-1] = result[-1].rstrip() + " " + continuation
        else:
            result.append(line)
        previous = line
    text = "\n".join(result)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"[ \t]+(?=[.,!?;:])", "", text)


def _views(key: str, value: str) -> tuple[str, str]:
    if key == "stripped_text":
        value = _unwrap_stripped_text(value)
    if key.endswith("_html") or _looks_html(value):
        visible = _visible_text(value)
    else:
        visible = _display_plain(value)
    current = strip_reply_artifacts(visible) if visible else ""
    return visible, current


def _archived(message: dict) -> bool:
    url = message.get("body_url")
    return isinstance(url, str) and bool(url.strip())


def accepted_contract(message) -> dict | None:
    """Copy the nine curated keys when the version and value types match."""
    if not isinstance(message, dict) or message.get("cleanup_version") != CLEANUP_VERSION:
        return None
    if any(key not in message for key in CONTRACT_KEYS):
        return None
    for key in ("display_text", "current_text", "original_content"):
        if not isinstance(message.get(key), str):
            return None
    for key in ("display_source", "current_source", "original_field"):
        value = message.get(key)
        if value is not None and not isinstance(value, str):
            return None
    if type(message.get("history_available")) is not bool or type(message.get("source_truncated")) is not bool:
        return None
    return {key: message[key] for key in CONTRACT_KEYS}


def retained_sources(message) -> dict:
    """Provider source fields only. Preferred content is not a raw body."""
    if not isinstance(message, dict):
        return {}
    kept = {}
    for key in (*_SOURCE_FIELDS, "body_url"):
        if key in message:
            kept[key] = message[key]
    return kept


def intake_from_message(message) -> dict:
    """Use a valid curated contract; otherwise normalize retained sources."""
    saved = accepted_contract(message)
    if saved is not None:
        return saved
    if not isinstance(message, dict):
        return normalize_message(message)
    return normalize_message(retained_sources(message))


def normalize_message(message) -> dict:
    """Return display history, current AI text, and source provenance."""
    result = _empty_contract()
    if not isinstance(message, dict):
        return result
    history_key, _history_value = _first(message, _FULL_FIELDS)
    display_key, display_value = _first(message, _DISPLAY_FIELDS)
    current_key, current_value = _first(message, _CURRENT_FIELDS)
    original_key, original_value = _first(message, _ORIGINAL_FIELDS)
    result["history_available"] = history_key is not None
    result["display_source"] = display_key
    result["current_source"] = current_key
    result["original_field"] = original_key
    result["original_content"] = original_value or ""
    excerpt_only = display_key == "excerpt"
    result["source_truncated"] = (not result["history_available"]) and (
        excerpt_only or _archived(message)
    )
    if display_value is None:
        return result
    if current_value is not None and current_key != display_key:
        display_text, _display_current = _views(display_key, display_value)
        _current_display, current_text = _views(current_key, current_value)
    else:
        display_text, current_text = _views(display_key or "text", display_value)
    result["display_text"] = display_text
    result["current_text"] = current_text
    return result
