"""
search_kb.py — finds relevant knowledge base files for a customer message.

Uses the OKF format: YAML frontmatter (type, title, tags, links) + markdown body.
No vector database. No embeddings. Just files, YAML, and keyword scoring.

Scoring:
  tag match   = 3 points each  (explicit keywords the author set)
  body match  = 1 point each   (4+ char words from message found in file body)

After finding top files, follows their `links:` to pull in related context
(e.g. returns.md links to exchanges.md → both are included automatically).
"""

import logging
import os
import re
from pathlib import Path

import yaml

log = logging.getLogger('teddy.search_kb')

_RE_WORDS_4PLUS = re.compile(r'[a-z]{4,}')
_RE_WORDS_3PLUS = re.compile(r'[a-z]{3,}')

_STOPWORDS = {
    'the', 'and', 'that', 'this', 'with', 'have', 'from', 'they',
    'will', 'what', 'your', 'when', 'been', 'their', 'there', 'were',
    'would', 'could', 'should', 'about', 'which', 'more', 'also', 'into',
    'some', 'just', 'then', 'than', 'like', 'very', 'still', 'here',
}


def _parse_okf(path: Path) -> dict:
    """Parse a single OKF markdown file. Returns {frontmatter, body, path}."""
    try:
        raw = path.read_text(encoding='utf-8')
    except Exception:
        return {}

    frontmatter = {}
    body = raw

    if raw.startswith('---'):
        parts = raw.split('---', 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                frontmatter = {}
            body = parts[2]

    return {'frontmatter': frontmatter, 'body': body, 'path': path}


def _meaningful_words(text: str) -> set:
    """Extract meaningful words (4+ chars, not stopwords) from text."""
    return {w for w in _RE_WORDS_4PLUS.findall(text.lower()) if w not in _STOPWORDS}


def _word_overlap(a: str, b: str) -> bool:
    """True if a and b share a common root: exact match or one is a prefix of the other (4+ chars)."""
    if a == b:
        return True
    min_len = min(len(a), len(b))
    if min_len >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return False


def _score_file(doc: dict, query_words: set, query_tags: set) -> int:
    """Score a KB file against query words and tags."""
    fm = doc.get('frontmatter', {})
    body = doc.get('body', '').lower()

    # Tag score (3 points each) — allow prefix matches so "sizes" matches "size"
    file_tags = {str(t).lower() for t in (fm.get('tags') or [])}
    tag_hits = {q for q in query_tags for t in file_tags if _word_overlap(q, t)}
    tag_score = len(tag_hits) * 3

    # Body score (1 point each) — allow prefix matches for inflections
    body_words = _meaningful_words(body)
    body_hits = {q for q in query_words for bw in body_words if _word_overlap(q, bw)}
    body_score = len(body_hits)

    return tag_score + body_score


def search_kb(message: str, kb_dir: str) -> dict:
    """
    search_kb(message, kb_dir) -> {
        "context": str,          # combined text of top KB files
        "files_used": [str],     # relative paths of files included
        "confidence": str,       # "HIGH" | "MEDIUM" | "LOW" | "NONE"
    }
    """
    kb_path = Path(kb_dir)
    if not kb_path.exists():
        log.warning("KB directory not found: %s", kb_dir)
        return {'context': '', 'files_used': [], 'confidence': 'NONE'}

    # Find all .md files recursively (skip index.md and log.md — meta files)
    md_files = [
        p for p in kb_path.rglob('*.md')
        if p.name not in ('index.md', 'log.md')
    ]

    if not md_files:
        return {'context': '', 'files_used': [], 'confidence': 'NONE'}

    # Parse all files
    docs = [d for d in (_parse_okf(p) for p in md_files) if d]

    # Build query signals from message
    query_words = _meaningful_words(message)
    query_tags = set(_RE_WORDS_3PLUS.findall(message.lower()))

    # Score every file
    scored = [(doc, _score_file(doc, query_words, query_tags)) for doc in docs]
    scored = [(doc, s) for doc, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)

    if not scored:
        return {'context': '', 'files_used': [], 'confidence': 'NONE'}

    top_score = scored[0][1]
    if top_score >= 6:
        confidence = 'HIGH'
    elif top_score >= 3:
        confidence = 'MEDIUM'
    else:
        confidence = 'LOW'

    # Take top 3 files
    top_docs = [doc for doc, _ in scored[:3]]
    included_paths = {str(doc['path']) for doc in top_docs}

    # Follow links from top files (OKF graph traversal)
    for doc in list(top_docs):
        links = doc.get('frontmatter', {}).get('links') or []
        for link in links:
            # links are relative paths like "kb/policies/shipping.md"
            link_path = kb_path.parent / link
            if not link_path.exists():
                # Try relative to kb_dir
                link_path = kb_path / link
            if link_path.exists() and str(link_path) not in included_paths:
                linked_doc = _parse_okf(link_path)
                if linked_doc:
                    top_docs.append(linked_doc)
                    included_paths.add(str(link_path))

    # Build context block
    sections = []
    files_used = []
    for doc in top_docs:
        fm = doc.get('frontmatter', {})
        title = fm.get('title', doc['path'].stem)
        body = doc['body'].strip()
        sections.append(f"### {title}\n{body}")
        files_used.append(str(doc['path'].relative_to(kb_path.parent)
                              if kb_path.parent in doc['path'].parents
                              else doc['path']))

    context = '\n\n---\n\n'.join(sections)
    return {'context': context, 'files_used': files_used, 'confidence': confidence}
