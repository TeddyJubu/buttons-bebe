"""Read retained Gorgias content without following archived body URLs."""
from html.parser import HTMLParser
import re

class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[];self.hidden=0;self.in_quote=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        elif tag=='blockquote':self.in_quote+=1
        elif not self.hidden and tag in ('p','div','br','li','tr'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style') and self.hidden:self.hidden-=1
        elif tag=='blockquote' and self.in_quote:self.in_quote-=1
        elif not self.hidden and tag in ('p','div','li','tr'):self.parts.append('\n')
    def handle_data(self,data):
        if self.hidden:return
        if not self.in_quote:
            # cubic: a literal newline in the customer's own text node is
            # their line structure, not word-glue — keep the data intact.
            self.parts.append(data)
            return
        # #43: blockquote content is quoted history — prefix it so the
        # stripper's quote-line rule catches it even with no header lines.
        for line in data.split('\n'):
            self.parts.append('\n> '+line)


# Issue #43: raw bodies carry mobile signatures and the whole quoted previous
# message, sometimes glued into one run-on line ("...please edit orderSent
# from my Galaxy -------- Original message --------From: ..."). The markers
# are unambiguous mail-client artifacts; nothing else in prose matches them.
# Safety rules mirror processor/classifier/views.py, whose tests prove them:
# a bottom-posted rant under a quote survives, "wrote:" mid-sentence is the
# customer talking, sign-offs like "Thanks, Jane" are never eaten, and an
# all-quoted message falls back to the whole text. The verbatim original
# stays in webhook_events.raw_payload for every message.
#
# cubic review: separators match with NO preceding space (glued to the last
# word), "Begin forwarded message:" counts, and generic From:/To:/Subject:
# lines alone are NOT quote headers — only the real mail separators cut.
_SEPARATOR_RE = re.compile(
    r'-{2,}\s*(?:original message|forwarded message)\s*-{2,}'
    r'|begin\s+forwarded\s+message:',
    re.IGNORECASE)
_GLUED_MARKERS = re.compile(
    r'(-{2,}\s*(?:original message|forwarded message)\s*-{2,}'
    r'|begin\s+forwarded\s+message:)',
    re.IGNORECASE)
# Line-anchored: only a footer line "Sent from my …" is a signature, never
# prose like "It was sent from my store". A glued client can concatenate it
# straight onto the last word ("...edit orderSent from my Galaxy") — that
# shape has a lowercase/punctuation char with NO space before "Sent", which
# prose after a normal word ("was sent") never has.
_SIGNATURE_RE = re.compile(
    r'(?:\n|^|(?<=[a-z0-9,.!?]))sent from my\s+\S[^\n]*(?:\n|$)',
    re.IGNORECASE)
_QUOTE_LINE_RE = re.compile(r'^\s*(?:>|\|)')
# A real mail-client quote header: "On … wrote:" with a date, or the
# explicit separators. Generic From:/To:/Subject: lines stay out — a
# customer can legitimately write those.
_QUOTE_HEADER_RE = re.compile(
    r'^\s*(?:'
    r'on\s.{0,200}\d.{0,160}\swrote:'
    r'|.{0,120}<[^>@\s]{1,64}@[^>\s]{1,64}>\s+wrote:'
    r')\s*$',
    re.IGNORECASE)


def strip_reply_artifacts(value):
    """Cut quoted history and mobile signatures from a message body."""
    value = value or ''
    if not value.strip():
        return value
    # Glued clients sent no line breaks; anchor breaks to the separator
    # strings themselves so line-based matching can fire. Only the exact
    # separator family gets a break — a generic prose heuristic would
    # corrupt words.
    value = _GLUED_MARKERS.sub(r'\n\1', value)
    kept = []
    seen_content = False
    for line in value.splitlines():
        if _QUOTE_LINE_RE.match(line):
            continue
        if _SEPARATOR_RE.search(line):
            # The mail separator cuts everything after it once the customer's
            # own words are seen; on top (top-posted) it is just skipped,
            # like a quote header — the fresh words below must survive.
            if seen_content:
                break
            continue
        if _QUOTE_HEADER_RE.match(line):
            # A "On … wrote:" header cuts only after the customer's own
            # words; on top (top-posted) it is just skipped.
            if seen_content:
                break
            continue
        kept.append(line)
        if line.strip():
            seen_content = True
    # cubic: dropped quote lines leave their boundaries behind; squeeze the
    # blank runs so quoted history widens no gap beyond one blank line.
    joined = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', '\n'.join(kept))
    fresh = _SIGNATURE_RE.sub('\n', joined).strip()
    if fresh:
        return fresh
    # All-quoted: fall back to the whole text so nothing is lost — but a
    # signature-only or signature+quote body is itself an artifact.
    if _SIGNATURE_RE.search(value):
        return ''
    return value.strip()


def message_text(message):
    """Prefer retained stripped text/HTML, then full bodies, then legacy text.

    #43: every branch is cleaned of quoted history and signature footers —
    Gorgias's own stripped text can keep the mobile signature, and the raw
    fallbacks keep the whole quoted thread. The original stays in raw_payload.
    """
    for key in ('stripped_text','stripped_html','body_text','body_html','text'):
        value=message.get(key)
        if not isinstance(value,str) or not value.strip():continue
        if key.endswith('_html'):
            parser=_VisibleText();parser.feed(value);parser.close()
            value=re.sub(r'\n[ \t]*\n+', '\n\n',''.join(parser.parts))
        if value.strip():return strip_reply_artifacts(value.strip())
    return ''
