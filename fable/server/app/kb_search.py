"""Keyword knowledge-base search (feature F4).

Gives Fable drafts the same policy / FAQ / intent facts the VPS agent grounds on.
This is the simple, dependency-free first version: plain keyword scoring over the
markdown files in the repo's ``kb/`` folder. (A LanceDB-backed semantic version can
replace this later behind the same ``search()`` signature.)

What it searches
----------------
Only the *policy-bearing* folders a drafted reply may cite:

    kb/policies/   kb/faq/   kb/intents/

It deliberately does **not** search ``kb/learned/`` (raw, unmasked human replies)
or ``kb/tickets/`` (exemplar transcripts) — those are not customer-facing policy.

How it works
------------
* Each markdown file is split into sections by its ``##`` headings; the section is
  the unit that gets scored and returned (so a snippet points at a real heading).
* Scoring is term overlap between the query and the section, with a boost when a
  query term appears in the heading itself.
* Files are read once and cached in memory on first use — there is no index build
  step and nothing is written to disk.

Returns the top ``limit`` sections as dicts::

    {"file": "policies/shipping-policy.md",
     "title": "Shipping Policy",
     "heading": "International shipping",
     "text": "<~400 char snippet>",
     "score": 7.0}
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import List, Optional

from . import config

# Only these subfolders of kb/ are searched (policy / FAQ / intent content).
_SEARCH_DIRS = ("policies", "faq", "intents")

# Tiny stop-word list so a question like "do you ship to canada" scores on the
# words that matter ("ship", "canada") rather than the glue words.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "is", "are", "am", "do",
    "does", "did", "you", "your", "yours", "i", "my", "me", "we", "our", "us",
    "it", "its", "this", "that", "these", "those", "for", "on", "at", "with",
    "can", "could", "would", "should", "will", "if", "how", "what", "when",
    "where", "why", "who", "be", "been", "being", "have", "has", "had", "from",
    "was", "were", "get", "got", "please", "hi", "hello", "hey", "thanks",
    "thank", "there", "so", "but", "just", "about",
}

# A query term found in a section's heading is worth this much more than in body.
_TITLE_BOOST = 3.0
_SNIPPET_CHARS = 400

_cache_lock = threading.Lock()
# resolved-kb-dir string -> parsed section list
_cache: dict[str, list[dict]] = {}


# --- tokenising -------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, dropping single characters."""
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1]


def _content_terms(text: str) -> list[str]:
    """Tokens with stop-words removed — the words worth matching on."""
    return [t for t in _tokens(text) if t not in _STOPWORDS]


# --- parsing ----------------------------------------------------------------
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_HEADING_SPLIT_RE = re.compile(r"^#{2,}\s+(.*)$", re.MULTILINE)


def _parse_file(path: Path, rel: str) -> list[dict]:
    """Split one markdown file into scored-ready section dicts."""
    raw = path.read_text(encoding="utf-8", errors="ignore")

    title = rel
    body = raw
    m = _FRONTMATTER_RE.match(raw)
    if m:
        frontmatter, body = m.group(1), m.group(2)
        tm = re.search(r"^title:\s*(.+)$", frontmatter, re.MULTILINE)
        if tm:
            title = tm.group(1).strip()

    # re.split with a capturing group yields: [intro, heading1, text1, heading2, ...]
    parts = _HEADING_SPLIT_RE.split(body)
    raw_sections: list[tuple[str, str]] = []
    intro = parts[0].strip()
    if intro:
        raw_sections.append((title, intro))
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if heading or text:
            raw_sections.append((heading, text))

    sections: list[dict] = []
    for heading, text in raw_sections:
        # Score against heading + body together; keep the heading tokens separately
        # so we can give them extra weight.
        body_terms = _content_terms(text + " " + heading)
        heading_terms = set(_content_terms(heading))
        sections.append({
            "file": rel,
            "title": title,
            "heading": heading,
            "text": text,
            "_body_terms": body_terms,
            "_heading_terms": heading_terms,
        })
    return sections


def _kb_dir(kb_dir: Optional[str | Path]) -> Path:
    if kb_dir is not None:
        return Path(kb_dir)
    configured = config.get("FABLE_KB_DIR")
    if configured:
        p = Path(configured)
        return p if p.is_absolute() else (config.REPO_ROOT / p)
    return config.REPO_ROOT / "kb"


def _load(kb_dir: Path) -> list[dict]:
    sections: list[dict] = []
    for sub in _SEARCH_DIRS:
        d = kb_dir / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            try:
                sections.extend(_parse_file(path, f"{sub}/{path.name}"))
            except Exception:
                # A single unreadable file must never break KB search.
                continue
    return sections


def _get_sections(kb_dir: Optional[str | Path]) -> list[dict]:
    d = _kb_dir(kb_dir)
    key = str(d.resolve())
    with _cache_lock:
        cached = _cache.get(key)
        if cached is None:
            cached = _load(d)
            _cache[key] = cached
        return cached


def reset_cache() -> None:
    """Drop the in-memory cache (tests / after the KB changes on disk)."""
    with _cache_lock:
        _cache.clear()


# --- search -----------------------------------------------------------------
def search(query: str, kb_dir: Optional[str | Path] = None, limit: int = 3) -> List[dict]:
    """Return up to ``limit`` best-matching KB section snippets for ``query``.

    Empty query or no keyword overlap → ``[]`` (the caller then drafts with no KB
    grounding rather than citing something irrelevant).
    """
    q_terms = set(_content_terms(query))
    if not q_terms:
        return []

    sections = _get_sections(kb_dir)
    scored: list[tuple[float, dict]] = []
    for sec in sections:
        body_terms = sec["_body_terms"]
        if not body_terms:
            continue
        score = 0.0
        matched = 0
        for term in q_terms:
            count = body_terms.count(term)
            if count:
                matched += 1
                # First hit is worth the most; extra hits add a little.
                score += 1.0 + 0.2 * (count - 1)
            if term in sec["_heading_terms"]:
                score += _TITLE_BOOST
        if matched == 0:
            continue
        # Reward breadth: matching more distinct query terms beats one repeated hit.
        score += matched
        scored.append((score, sec))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    results: list[dict] = []
    for score, sec in scored[:limit]:
        results.append({
            "file": sec["file"],
            "title": sec["title"],
            "heading": sec["heading"],
            "text": _snippet(sec["text"]),
            "score": round(score, 3),
        })
    return results


def _snippet(text: str, limit: int = _SNIPPET_CHARS) -> str:
    """Collapse whitespace and trim to ~``limit`` chars at a word boundary."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0].rstrip()
    return (cut or t[:limit]) + "…"
