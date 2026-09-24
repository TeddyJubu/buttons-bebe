#!/usr/bin/env python3
"""
kb_client.py — the THIN, PURE-STDLIB "plug" between the draft engine and the KB.

This is the single, stable retrieval seam the rest of the system plugs into:

    import kb_client
    hits = kb_client.search("where is my order", top_k=5)
    if not hits:
        ...  # KB gap -> caller escalates / asks the owner

ISOLATION (the black-box design). The heavy semantic backend (fastembed model +
pgvector / pg8000) NO LONGER runs in this process. It lives in a separate,
standalone localhost service — kb_service.py — that owns the model and the DB.
This module is now a THIN CLIENT to that service over HTTP, with a pure-stdlib
LOCAL FALLBACK. Concretely, search() has two layers:

  * PRIMARY — the KB SERVICE: POST {question, top_k, min_score} to kb_service's
    /search over urllib with a STRICT TIMEOUT (config kb_service.timeout / env
    KB_SERVICE_TIMEOUT, default ~3s). The service returns ranked pgvector
    (semantic) results as JSON, which we rebuild into list[KBChunk]. Because it
    is a separate process, it CANNOT block or crash this one (and vice-versa):
    a slow/dead/broken service can never hang the webhook beyond the timeout.
  * FALLBACK — local "files" BM25: if the service is unreachable, times out,
    returns a non-2xx, or returns malformed JSON, we log a WARNING and fall back
    to the IN-PROCESS, pure-stdlib lexical search (_search_files, BM25-lite over
    kb/*.md). This is the "can't be hampered" guarantee. search() NEVER raises
    and NEVER blocks longer than the timeout.

    A 2xx response with an EMPTY result list is a GENUINE KB GAP (no confident
    semantic match) — we return [] and do NOT fall back (an empty result is not
    an error). Only a transport/protocol failure triggers the file fallback.

  * EMBEDDED/OFFLINE MODE: if the service is disabled (config kb_service.enabled
    false / env KB_SERVICE_ENABLED=0) or not configured, search() skips the HTTP
    hop entirely and uses the local file backend directly — a clean, dependency-
    free offline mode (e.g. for tests or a host without the service running).

Because this module imports NOTHING heavy (no fastembed, no pg8000 — those moved
to kb_service.py), merely importing kb_client (and therefore importing the
webhook, which pulls it in via draft_engine) is PURE STDLIB again: no model
load, no DB driver, no risk of the KB layer dragging the webhook down.

Everything downstream of search() — the draft engine, the KB-gap escalation
logic — is untouched: the signature and the list[KBChunk] return shape do not
change. has_answer() and reload() keep working (reload also pings the service).

The markdown parser seam (iter_chunks / parse_file / load_chunks) and the
_search_files BM25 backend stay HERE — they are stdlib, the fallback needs them,
and ingestion_worker.py imports the parser from this module.

THE CONTRACT we parse (kb/CONVENTIONS.md, authoritative):
  * Every KB file = a YAML front-matter block delimited by `---` lines
    (fields: title, category, status, optional source, optional tags list),
    followed by content.
  * Content is split into retrievable chunks by `##` (level-2) headings. Each
    `##` heading + its body (up to the next `##` or EOF) is ONE chunk.
  * `###` (level-3) does NOT start a new chunk — it stays inside its parent.
  * The optional intro/preamble before the first `##` (incl. the DRAFT banner)
    is treated as a file-level "intro" chunk (heading=None), lower-weighted.
  * The stable doc id is the repo-relative file path (e.g.
    "kb/policies/shipping-policy.md").

Robustness: files with malformed/missing front-matter are skipped with a logged
warning (kb/README.md and kb/CONVENTIONS.md have none — they are correctly
ignored). Files with no `##` headings index their whole body as one chunk. The
kb/ root is resolved relative to THIS module (override with the KB_ROOT env var
or the kb_root= argument), never hardcoded. Empty kb/learned/ is fine. Unicode
is handled (files read as UTF-8).

Stdlib only. No pip dependencies. Importing this module does NOT scan the KB or
run the smoke test — loading is lazy on the first search()/has_answer() call.

Run directly for a smoke test against the seeded KB:
    python3 kb_client.py
"""

import json
import logging
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("kb_client")

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# kb/ lives next to this module. Overridable via KB_ROOT env var or kb_root= arg.
# Resolved relative to the module file, never a hardcoded absolute path.
DEFAULT_KB_ROOT = os.environ.get("KB_ROOT", os.path.join(SCRIPT_DIR, "kb"))

# Default relevance floor for the FILE/BM25 backend. Tuned against the seeded
# corpus so real questions clear it and pure-nonsense queries fall below it ([]).
# This is the module-level default exposed to callers (draft_engine passes it).
DEFAULT_MIN_SCORE = 1.0

# Cosine relevance floor used by the PGVECTOR (semantic) backend — which now
# lives in kb_service.py, NOT here. BM25 scores (~0..12, unbounded) and cosine
# similarities (0..1) are on COMPLETELY DIFFERENT scales. The thin client passes
# min_score=None to the service so the service applies ITS OWN cosine floor
# (kb_service.PGVECTOR_MIN_SCORE, kept equal to this value); a caller's explicit
# non-default min_score is forwarded verbatim. Retained here for back-compat /
# reference (some callers still import it). 0.62 cleanly passes every real query
# and returns [] on nonsense (the measured separation: real ~0.67–0.89, nonsense
# ~0.57).
PGVECTOR_MIN_SCORE = 0.62

_CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

# --------------------------------------------------------------------------- #
# KB SERVICE (the black box) — connection config for the thin client.
# --------------------------------------------------------------------------- #
# The PRIMARY backend is the standalone kb_service.py over localhost HTTP. These
# defaults match kb_service's own defaults; overridable per-call via config.json
# {"kb_service": {...}} and env. Resolved PER-CALL (not cached) so a runtime
# config/env change takes effect on the next search() without reimporting.
_DEFAULT_SERVICE_URL = "http://127.0.0.1:8899"
_DEFAULT_SERVICE_TIMEOUT = 3.0  # seconds — the hard cap on the PRIMARY path
_DEFAULT_SERVICE_ENABLED = True


def _resolve_service_config():
    """Resolve (url, timeout, enabled) for the KB service.

    Precedence (first wins) per field: env override > config.json kb_service.* >
    built-in default. Never raises; an unreadable/missing config silently uses
    the defaults. `enabled=False` (env KB_SERVICE_ENABLED in {0,false,no,off})
    means "embedded/offline mode" — search() skips the HTTP hop and uses the
    local file backend directly.
    """
    url = _DEFAULT_SERVICE_URL
    timeout = _DEFAULT_SERVICE_TIMEOUT
    enabled = _DEFAULT_SERVICE_ENABLED

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            svc = json.load(fh).get("kb_service") or {}
        if svc.get("url"):
            url = str(svc["url"]).strip()
        if svc.get("timeout") is not None:
            timeout = float(svc["timeout"])
        if svc.get("enabled") is not None:
            enabled = bool(svc["enabled"])
    except (OSError, ValueError, TypeError):
        pass  # no/unreadable/invalid config -> defaults

    env_url = os.environ.get("KB_SERVICE_URL")
    if env_url:
        url = env_url.strip()
    env_timeout = os.environ.get("KB_SERVICE_TIMEOUT")
    if env_timeout:
        try:
            timeout = float(env_timeout)
        except ValueError:
            pass
    env_enabled = os.environ.get("KB_SERVICE_ENABLED")
    if env_enabled is not None:
        enabled = env_enabled.strip().lower() not in ("0", "false", "no", "off", "")

    if timeout <= 0:
        timeout = _DEFAULT_SERVICE_TIMEOUT
    return url.rstrip("/"), timeout, enabled


# --------------------------------------------------------------------------- #
# Public result type — the stable shape a Supermemory client must also return
# --------------------------------------------------------------------------- #
@dataclass
class KBChunk:
    """One retrievable knowledge chunk and its relevance to a query.

    This is the contract the rest of the system depends on. The Stage 4
    Supermemory-backed search() must return a list of objects with these same
    fields (a Supermemory result maps cleanly: result text -> text, its source
    metadata -> source/title/category/heading, its relevance score -> score).
    """

    source: str             # repo-relative file path, e.g. "kb/policies/shipping-policy.md"
    title: str              # front-matter `title`
    category: str           # front-matter `category` (policies|faq|tickets|learned)
    heading: str            # the `##` section heading, or None for the intro/preamble
    text: str               # the chunk body (heading line not included)
    score: float            # relevance, higher = better (0.0 for a non-search load)
    status: str = ""        # front-matter `status` (DRAFT|confirmed) — useful to the caller
    tags: list = field(default_factory=list)  # front-matter `tags`

    def as_dict(self):
        """Plain-dict view (handy for logging / JSON / prompt assembly)."""
        return {
            "source": self.source,
            "title": self.title,
            "category": self.category,
            "heading": self.heading,
            "text": self.text,
            "score": self.score,
            "status": self.status,
            "tags": list(self.tags),
        }


# --------------------------------------------------------------------------- #
# Tokenization (shared by indexing and querying so they match)
# --------------------------------------------------------------------------- #
# Small, deliberately conservative English stopword set. Kept short on purpose:
# we want to drop noise words ("the", "is") without dropping support-domain
# signal words ("order", "size", "return", "ship").
#
# NOTE: WH-question words (where/when/what/how/why/which/who) are intentionally
# NOT stopwords. In support queries they carry real intent — "WHERE is my order"
# is a tracking question, "HOW do I return" is a returns question, "WHAT size" is
# a sizing question — and the KB headings echo those words ("Where is my order /
# has it shipped?"). They are rare across the corpus (high IDF), so keeping them
# is what lets the right chunk win.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "could", "did", "do", "does", "for", "from", "get", "got", "had", "has",
    "have", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of",
    "on", "or", "our", "out", "should", "so", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "to", "up", "was", "we", "were",
    "will", "with", "would", "you", "your", "yours",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem(token):
    """Very light suffix stemmer so "orders"/"ordering" ~ "order".

    Intentionally crude (no Porter dependency, stdlib only). Good enough to make
    "tracking"/"track", "returns"/"return", "shipped"/"shipping" collide on a
    shared root for lexical matching.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _tokenize(text):
    """Lowercase, split to alphanumeric tokens, drop stopwords, light-stem."""
    if not text:
        return []
    tokens = _TOKEN_RE.findall(text.lower())
    out = []
    for tok in tokens:
        if tok in _STOPWORDS:
            continue
        out.append(_stem(tok))
    return out


# --------------------------------------------------------------------------- #
# Parsing the KB contract (front-matter + `##` chunking)
# --------------------------------------------------------------------------- #
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n", re.DOTALL)


def _parse_front_matter(raw):
    """Parse a leading `---`..`---` YAML-ish block.

    Returns (meta_dict, body_text), or (None, raw) if the file does not begin
    with a front-matter block (caller skips those — they are not KB files).

    We do NOT pull in a YAML lib (stdlib only). The seed format is simple
    `key: value` lines plus a `tags: [a, b]` inline list, which we parse by hand.
    """
    m = _FRONT_MATTER_RE.match(raw)
    if not m:
        return None, raw

    block = m.group(1)
    body = raw[m.end():]
    meta = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "tags":
            meta[key] = _parse_inline_list(value)
        else:
            # strip optional surrounding quotes
            meta[key] = value.strip("'\"")
    return meta, body


def _parse_inline_list(value):
    """Parse a `[a, b, c]` inline YAML list into a list of strings."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    items = [v.strip().strip("'\"") for v in value.split(",")]
    return [v for v in items if v]


# Matches a level-2 heading line: exactly two leading hashes, then a space.
# Level-1 (`# `) and level-3 (`### `) are deliberately NOT matched here.
_H2_RE = re.compile(r"^##[ \t]+(.*\S)\s*$")


def _split_chunks(body):
    """Split a file body into (heading, text) chunks per the contract.

    - Text before the first `##` is the intro/preamble chunk -> heading=None.
    - Each `##` heading starts a new chunk that runs to the next `##` or EOF.
    - `###` lines are left untouched inside whatever `##` chunk they fall in.

    Returns a list of (heading_or_None, body_text). Chunks whose body is empty
    after stripping are dropped, except we always keep at least the intro if the
    file has no headings at all (whole body becomes one chunk).
    """
    lines = body.splitlines()
    chunks = []
    current_heading = None
    current_lines = []

    def flush():
        text = "\n".join(current_lines).strip()
        if text or current_heading is not None:
            chunks.append((current_heading, text))

    for line in lines:
        m = _H2_RE.match(line)
        if m:
            flush()
            current_heading = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()

    # Drop empty-bodied chunks (e.g. a heading with no text), but if NOTHING
    # survived (rare), keep the whole body as a single intro chunk.
    non_empty = [(h, t) for (h, t) in chunks if t]
    if non_empty:
        return non_empty
    whole = body.strip()
    return [(None, whole)] if whole else []


# --------------------------------------------------------------------------- #
# The in-memory index (built once, lazily; rebuildable via reload())
# --------------------------------------------------------------------------- #
class _Index:
    """Holds parsed chunks plus a BM25-lite scorer over them.

    Why BM25-lite (and not difflib SequenceMatcher): SequenceMatcher compares
    raw character sequences, so a short question barely overlaps a long chunk and
    word order dominates — weak for "where is my order" vs a tracking section.
    A term-frequency model with inverse-document-frequency weighting and length
    normalization is the standard, robust choice and is a handful of lines of
    stdlib math. It also mirrors what Supermemory does internally (keyword side
    of its hybrid search), so swapping in Supermemory in Stage 4 is behavior-
    compatible, just better (it adds semantic + graph signal).
    """

    # BM25 free parameters (standard defaults).
    _K1 = 1.5   # term-frequency saturation
    _B = 0.75   # length-normalization strength

    # Field boosts: a query term matching a heading/title/tag is worth more than
    # one matching deep in the body. Applied by repeating those tokens.
    _TITLE_BOOST = 2
    _HEADING_BOOST = 3
    _TAG_BOOST = 2

    def __init__(self, kb_root):
        self.kb_root = kb_root
        self.chunks = []            # list[KBChunk] (score=0.0 until a search)
        self._doc_tokens = []       # list[list[str]] parallel to self.chunks
        self._doc_freqs = []        # list[dict[str,int]] term frequencies per chunk
        self._df = {}               # document frequency per term
        self._idf = {}              # inverse document frequency per term
        self._avg_len = 0.0
        self._n_docs = 0

    # -- building ---------------------------------------------------------- #
    def build(self):
        """Scan kb_root, parse every .md file, and build the BM25 statistics."""
        self.chunks = []
        self._doc_tokens = []
        self._doc_freqs = []
        self._df = {}
        self._idf = {}
        self._avg_len = 0.0
        self._n_docs = 0

        if not os.path.isdir(self.kb_root):
            log.warning("KB root not found: %s — search will return [].", self.kb_root)
            return

        files = self._discover_files()
        for path in files:
            self._index_file(path)

        self._finalize_stats()
        log.info(
            "KB indexed: %d chunks from %d file(s) under %s",
            len(self.chunks), len(files), self.kb_root,
        )

    def _discover_files(self):
        """All .md files under kb_root, sorted for stable, deterministic order."""
        found = []
        for dirpath, _dirnames, filenames in os.walk(self.kb_root):
            for name in filenames:
                if name.lower().endswith(".md"):
                    found.append(os.path.join(dirpath, name))
        found.sort()
        return found

    def _rel_path(self, abs_path):
        """Repo-relative doc id, e.g. 'kb/policies/shipping-policy.md'.

        Relative to the repo root (the parent of kb_root) so the id matches the
        Supermemory doc id described in CONVENTIONS.md §7. Uses forward slashes.
        """
        repo_root = os.path.dirname(os.path.abspath(self.kb_root))
        rel = os.path.relpath(abs_path, repo_root)
        return rel.replace(os.sep, "/")

    def _index_file(self, abs_path):
        rel = self._rel_path(abs_path)
        try:
            with open(abs_path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("Skipping unreadable KB file %s: %s", rel, exc)
            return

        meta, body = _parse_front_matter(raw)
        if meta is None:
            # No front-matter -> not a KB content file (e.g. README/CONVENTIONS).
            log.warning("Skipping %s: no YAML front-matter (not a KB chunk file).", rel)
            return
        if "title" not in meta or "category" not in meta:
            # Contract requires title+category. Skip but log so it gets fixed.
            log.warning(
                "Skipping %s: front-matter missing required title/category.", rel
            )
            return

        title = meta.get("title", "")
        category = meta.get("category", "")
        status = meta.get("status", "")
        tags = meta.get("tags", []) or []

        for heading, text in _split_chunks(body):
            chunk = KBChunk(
                source=rel,
                title=title,
                category=category,
                heading=heading,
                text=text,
                score=0.0,
                status=status,
                tags=list(tags),
            )
            self._add_chunk(chunk, title, heading, tags)

    def _add_chunk(self, chunk, title, heading, tags):
        # Build the searchable token bag: body tokens, plus boosted copies of
        # heading/title/tag tokens so a match there ranks higher.
        tokens = list(_tokenize(chunk.text))
        tokens += _tokenize(title) * self._TITLE_BOOST
        if heading:
            tokens += _tokenize(heading) * self._HEADING_BOOST
        for tag in tags:
            # tags are hyphenated; underscores/hyphens already split by tokenizer
            tokens += _tokenize(tag.replace("-", " ")) * self._TAG_BOOST

        freqs = {}
        for tok in tokens:
            freqs[tok] = freqs.get(tok, 0) + 1

        self.chunks.append(chunk)
        self._doc_tokens.append(tokens)
        self._doc_freqs.append(freqs)
        for term in freqs:
            self._df[term] = self._df.get(term, 0) + 1

    def _finalize_stats(self):
        self._n_docs = len(self.chunks)
        if self._n_docs == 0:
            self._avg_len = 0.0
            return
        total_len = sum(len(toks) for toks in self._doc_tokens)
        self._avg_len = total_len / self._n_docs
        # Standard BM25 idf with the +1 inside the log to keep it non-negative.
        for term, df in self._df.items():
            self._idf[term] = math.log(1 + (self._n_docs - df + 0.5) / (df + 0.5))

    # -- querying ---------------------------------------------------------- #
    def score(self, query):
        """Return all chunks with a BM25 score for `query`, best first.

        Chunks scoring 0 (no query term overlap) are omitted entirely.
        """
        if self._n_docs == 0:
            return []
        q_terms = _tokenize(query)
        if not q_terms:
            return []
        q_set = set(q_terms)

        results = []
        for i, chunk in enumerate(self.chunks):
            freqs = self._doc_freqs[i]
            doc_len = len(self._doc_tokens[i]) or 1
            s = 0.0
            for term in q_set:
                tf = freqs.get(term, 0)
                if not tf:
                    continue
                idf = self._idf.get(term, 0.0)
                denom = tf + self._K1 * (
                    1 - self._B + self._B * (doc_len / (self._avg_len or 1))
                )
                s += idf * (tf * (self._K1 + 1)) / denom
            if s > 0:
                scored = KBChunk(
                    source=chunk.source,
                    title=chunk.title,
                    category=chunk.category,
                    heading=chunk.heading,
                    text=chunk.text,
                    score=round(s, 4),
                    status=chunk.status,
                    tags=list(chunk.tags),
                )
                results.append(scored)

        # Sort by score desc; tie-break deterministically by source+heading.
        results.sort(key=lambda c: (-c.score, c.source, c.heading or ""))
        return results


# --------------------------------------------------------------------------- #
# Public parser seam — reused by ingestion_worker.py (Stage 4, Task 12)
# --------------------------------------------------------------------------- #
# The pgvector ingestion worker MUST produce exactly the same chunks the file
# BM25 backend indexes, so it parses through THESE functions (the same
# _parse_front_matter / _split_chunks / discovery path), never its own parser.
# A returned KBChunk has score=0.0 (no search was run); the worker only reads
# source/title/category/heading/status/tags/text.
def _parse_file_to_chunks(abs_path, rel):
    """Parse one .md file into KBChunk objects (score=0.0), or [] if not a KB file.

    Same logic as _Index._index_file but without touching the BM25 stats — a
    pure (path -> chunks) function the ingestion worker can call per file. Files
    with no/invalid front-matter or missing title/category return [] (logged),
    matching the file backend's skip behavior exactly.
    """
    try:
        with open(abs_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("Skipping unreadable KB file %s: %s", rel, exc)
        return []

    meta, body = _parse_front_matter(raw)
    if meta is None:
        log.warning("Skipping %s: no YAML front-matter (not a KB chunk file).", rel)
        return []
    if "title" not in meta or "category" not in meta:
        log.warning("Skipping %s: front-matter missing required title/category.", rel)
        return []

    title = meta.get("title", "")
    category = meta.get("category", "")
    status = meta.get("status", "")
    tags = meta.get("tags", []) or []

    out = []
    for heading, text in _split_chunks(body):
        out.append(
            KBChunk(
                source=rel,
                title=title,
                category=category,
                heading=heading,
                text=text,
                score=0.0,
                status=status,
                tags=list(tags),
            )
        )
    return out


def parse_file(abs_path, kb_root=None):
    """Parse a single KB file at `abs_path` into KBChunk(s); [] if not a KB file.

    The `source` of each chunk is the repo-relative path (same id the BM25
    backend uses). Used by the ingestion worker's incremental git-diff sync to
    (re)chunk just the files that changed.
    """
    root = os.path.abspath(kb_root or DEFAULT_KB_ROOT)
    repo_root = os.path.dirname(root)
    rel = os.path.relpath(os.path.abspath(abs_path), repo_root).replace(os.sep, "/")
    return _parse_file_to_chunks(os.path.abspath(abs_path), rel)


def iter_chunks(kb_root=None):
    """Yield every KBChunk in the KB, file by file, in stable sorted order.

    The corpus-wide parser seam for full ingestion. Walks kb_root exactly like
    the BM25 index (_discover_files sorts for determinism) and parses each file
    through the shared parser, so the chunk set is identical to what search()
    indexes. score is 0.0 on each chunk (no query was run).
    """
    root = os.path.abspath(kb_root or DEFAULT_KB_ROOT)
    idx = _Index(root)
    for abs_path in idx._discover_files():
        rel = idx._rel_path(abs_path)
        for chunk in _parse_file_to_chunks(abs_path, rel):
            yield chunk


def load_chunks(kb_root=None):
    """Eager list[KBChunk] of the whole KB (convenience over iter_chunks)."""
    return list(iter_chunks(kb_root))


# --------------------------------------------------------------------------- #
# Module-level lazy singleton + public API
# --------------------------------------------------------------------------- #
_INDEX = None
_INDEX_ROOT = None


def _get_index(kb_root=None):
    """Return the cached index, building it on first use (lazy load).

    Rebuilds automatically if a different kb_root is requested than the one
    currently cached, so passing an explicit root always does the right thing.
    """
    global _INDEX, _INDEX_ROOT
    root = os.path.abspath(kb_root or DEFAULT_KB_ROOT)
    if _INDEX is None or _INDEX_ROOT != root:
        idx = _Index(root)
        idx.build()
        _INDEX = idx
        _INDEX_ROOT = root
    return _INDEX


def reload(kb_root=None):
    """Force a fresh scan + re-index of the LOCAL file backend, and (best-effort)
    ping the KB service's /reload so its caches/DB connection reset too.

    Returns the number of chunks now indexed in the local file backend. The
    service ping is fire-and-forget: a failure is logged at debug and never
    raises (the service owns its own reload semantics; this is just a nudge).
    """
    global _INDEX, _INDEX_ROOT
    root = os.path.abspath(kb_root or DEFAULT_KB_ROOT)
    idx = _Index(root)
    idx.build()
    _INDEX = idx
    _INDEX_ROOT = root

    # Best-effort: nudge the service to reset its caches (no-op if disabled/down).
    url, timeout, enabled = _resolve_service_config()
    if enabled:
        try:
            req = urllib.request.Request(url + "/reload", data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()
        except Exception as exc:
            log.debug("KB service /reload ping failed (non-fatal): %s", exc)

    return len(idx.chunks)


# --------------------------------------------------------------------------- #
# Backend: files (BM25-lite). The Stage-1 logic, unchanged, just behind a name.
# --------------------------------------------------------------------------- #
def _search_files(question, top_k, min_score, kb_root=None):
    """BM25-lite lexical search over kb/*.md (the original Stage-1 backend).

    `min_score` is on the BM25 scale (DEFAULT_MIN_SCORE ~1.0). Returns up to
    top_k KBChunk, best first; [] on no confident match or a missing/empty kb/.
    Never raises (a build failure is logged and treated as a gap).
    """
    try:
        index = _get_index(kb_root)
    except Exception as exc:  # never let retrieval crash the agent
        log.warning("KB index build failed (%s) — treating as KB gap.", exc)
        return []

    ranked = index.score(question)
    hits = [c for c in ranked if c.score > min_score]
    return hits[:top_k]


# --------------------------------------------------------------------------- #
# PRIMARY backend: the KB SERVICE (thin HTTP client over urllib). The black box.
# --------------------------------------------------------------------------- #
# We POST {question, top_k, min_score} to kb_service /search with a STRICT
# TIMEOUT and rebuild the JSON results into KBChunk. The heavy semantic work
# (embedding + pgvector) happens in the SERVICE process — never here. This call
# distinguishes three outcomes for the caller:
#   * a list[KBChunk] (possibly EMPTY) on a clean 2xx  -> use it as-is; an empty
#     list is a GENUINE KB GAP, NOT a fallback trigger;
#   * raises _ServiceUnavailable on any transport/protocol failure (unreachable,
#     timeout, non-2xx, malformed JSON) -> search() logs + falls back to files.

class _ServiceUnavailable(Exception):
    """Raised when the KB service can't give a usable answer (-> file fallback)."""


def _service_health(url, timeout):
    """GET <url>/health -> parsed dict, or None on any failure. Never raises.

    Used by reload()/diagnostics; the hot search path does not call this.
    """
    try:
        req = urllib.request.Request(url + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        return json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return None


def _search_service(question, top_k, min_score, url, timeout):
    """Query the KB service /search. Returns list[KBChunk] (maybe []), or RAISES
    _ServiceUnavailable on any transport/protocol failure so search() falls back.

    A clean 2xx with {"results": []} returns [] (a genuine KB gap — NOT an
    error). The STRICT `timeout` bounds the whole round-trip: a dead or slow
    service can never block the caller longer than this.

    `min_score`: pass None to let the SERVICE apply its own tuned cosine floor;
    an explicit float is forwarded so a caller can override it.
    """
    payload = {"question": question, "top_k": int(top_k), "min_score": min_score}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url + "/search",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # Non-2xx (e.g. the service's own 503 = its backend is down). The caller
        # must fall back. A 4xx (bad request) is also unusable -> fall back too.
        raise _ServiceUnavailable(f"service HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # Unreachable / connection refused / DNS / read timeout. urllib raises
        # socket.timeout (a subclass of OSError/TimeoutError) on a slow service.
        raise _ServiceUnavailable(f"service unreachable: {exc}") from exc

    try:
        body = json.loads(raw.decode("utf-8", errors="replace"))
        results = body["results"]
        if not isinstance(results, list):
            raise ValueError("'results' is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        # Malformed / unexpected JSON -> treat as a service failure, fall back.
        raise _ServiceUnavailable(f"malformed service response: {exc}") from exc

    hits = []
    for r in results:
        if not isinstance(r, dict):
            continue
        tags = r.get("tags") or []
        hits.append(
            KBChunk(
                source=r.get("source", ""),
                title=r.get("title") or "",
                category=r.get("category") or "",
                heading=r.get("heading"),
                text=r.get("text") or "",
                score=float(r.get("score") or 0.0),
                status=r.get("status") or "",
                tags=list(tags) if isinstance(tags, list) else [],
            )
        )
    return hits[: int(top_k)]


# --------------------------------------------------------------------------- #
# The stable seam: PRIMARY (service) -> timeout/failure -> FALLBACK (files)
# --------------------------------------------------------------------------- #
def _service_min_score(min_score):
    """Translate the caller's min_score into the value sent to the KB SERVICE.

    THE min_score SCALE PROBLEM: BM25 scores (~0..12) and the service's cosine
    similarities (0..1) live on different scales. draft_engine (and has_answer)
    call search() WITHOUT an explicit floor — they pass the module default
    DEFAULT_MIN_SCORE (1.0, a BM25 number). Forwarding 1.0 to the cosine backend
    would filter EVERYTHING (no similarity > 1.0) and every real query would look
    like a KB gap.

    So: a caller who left min_score at the module default (or passed None) is
    sent None, which tells the SERVICE to apply ITS OWN tuned cosine floor
    (kb_service.PGVECTOR_MIN_SCORE ~0.62). A caller who passed any OTHER explicit
    value is taken at their word and it is forwarded verbatim. This keeps the
    service hop invisible to existing callers.
    """
    if min_score is None or min_score == DEFAULT_MIN_SCORE:
        return None
    return min_score


def _file_min_score(min_score):
    """Effective BM25 floor for the LOCAL FILE fallback (the original scale).

    The module default / None -> DEFAULT_MIN_SCORE (the tuned BM25 floor); any
    explicit value is honored as-is.
    """
    if min_score is None:
        return DEFAULT_MIN_SCORE
    return min_score


def search(question, top_k=5, min_score=DEFAULT_MIN_SCORE, kb_root=None):
    """Return up to `top_k` KB chunks relevant to `question`, best first.

    THE STABLE SEAM (unchanged signature + list[KBChunk] return). Flow:

      1. PRIMARY — the KB SERVICE (kb_service.py over localhost HTTP) with a
         STRICT timeout. A clean response (even an EMPTY list) is used as-is.
      2. FALLBACK — if the service is disabled, unreachable, times out, returns a
         non-2xx, or returns malformed JSON, log a WARNING and use the in-process
         pure-stdlib file BM25 backend (_search_files).

    KEY INVARIANTS:
      * NEVER raises. NEVER blocks longer than the service timeout (then falls
        back, which is local + fast).
      * An EMPTY list from a HEALTHY service is a GENUINE KB GAP -> returns []
        and does NOT fall back (empty != error).
      * Only a transport/protocol FAILURE triggers the file fallback.

    Args:
        question:  the customer's (normalized) message / query string.
        top_k:     max number of chunks to return.
        min_score: relevance floor. Left at the module default (DEFAULT_MIN_SCORE)
                   or None, the SERVICE uses its own tuned cosine floor and the
                   FILE fallback uses the tuned BM25 floor. Pass an explicit value
                   to override (forwarded to whichever backend answers).
        kb_root:   optional override of the kb/ directory (file backend only).

    Returns:
        list[KBChunk] sorted by score descending. **An empty list is a valid,
        meaningful result** meaning "no confident KB match" — i.e. a KB gap; the
        caller escalates / asks the owner.
    """
    if not question or not question.strip():
        return []

    url, timeout, enabled = _resolve_service_config()

    # Embedded/offline mode: no service -> straight to the local file backend.
    if not enabled:
        return _search_files(question, top_k, _file_min_score(min_score), kb_root)

    try:
        return _search_service(
            question, top_k, _service_min_score(min_score), url, timeout
        )
    except _ServiceUnavailable as exc:
        # Service is disabled-at-runtime / down / slow / broken: degrade
        # gracefully to the always-available, pure-stdlib file backend. This is
        # the "can't be hampered" guarantee. (An empty result NEVER reaches here
        # — _search_service returns [] cleanly on a healthy no-match.)
        log.warning(
            "KB service unavailable (%s) — falling back to local file BM25 backend.",
            exc,
        )
        return _search_files(question, top_k, _file_min_score(min_score), kb_root)
    except Exception as exc:  # absolute backstop — retrieval must never crash.
        log.warning(
            "KB service call errored unexpectedly (%s) — falling back to file backend.",
            exc,
        )
        return _search_files(question, top_k, _file_min_score(min_score), kb_root)


def has_answer(question, min_score=DEFAULT_MIN_SCORE, kb_root=None):
    """True if the KB confidently answers `question`, False on a KB gap.

    Thin convenience wrapper over search() for callers that only need the
    yes/no gap signal. Equivalent to `bool(search(question, top_k=1, ...))`.
    """
    return bool(search(question, top_k=1, min_score=min_score, kb_root=kb_root))


# --------------------------------------------------------------------------- #
# Smoke test — only runs when executed directly, never on import.
# --------------------------------------------------------------------------- #
def _smoke_test():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    url, timeout, enabled = _resolve_service_config()
    if enabled:
        health = _service_health(url, timeout)
        if health:
            print(f"KB service: {url}  health={health}\n")
        else:
            print(f"KB service: {url} UNREACHABLE — search() will use the file "
                  f"fallback below.\n")
    else:
        print("KB service: DISABLED (embedded/offline mode — file backend).\n")
    # Always show the local file index size (the fallback the client guarantees).
    n = reload()
    print(f"Local file backend indexed {n} chunks from {_INDEX_ROOT}\n")

    real_queries = [
        "where is my order",
        "how do I return an item",
        "what size should I get",
        "can I cancel my order",
        "do you offer price adjustments",
    ]
    nonsense_query = "purple platypus quantum tax accordion lawnmower"

    print("=== Real queries (expect a confident top hit) ===")
    top_hits = {}
    for q in real_queries:
        hits = search(q)
        if hits:
            top = hits[0]
            top_hits[q] = top
            print(f"\nQ: {q!r}")
            print(f"   -> {top.source}  ##{top.heading}  (score={top.score})")
            print(f"      title: {top.title} [{top.category}/{top.status}]")
            # show the next hit too, for context
            for h in hits[1:3]:
                print(f"      also: {h.source}  ##{h.heading}  (score={h.score})")
        else:
            top_hits[q] = None
            print(f"\nQ: {q!r}\n   -> (no hits — KB GAP)")

    print("\n=== KB-gap query (expect NOTHING) ===")
    gap_hits = search(nonsense_query)
    print(f"Q: {nonsense_query!r}")
    print(f"   -> {len(gap_hits)} hits  (has_answer={has_answer(nonsense_query)})")
    if gap_hits:
        for h in gap_hits:
            print(f"      UNEXPECTED: {h.source} ##{h.heading} score={h.score}")

    # ----- sanity assertions ----- #
    print("\n=== Sanity assertions ===")

    order = top_hits.get("where is my order")
    assert order is not None, "order-status query returned no hits"
    assert ("shipping" in order.source or "tracking" in order.source.lower()
            or "shipping" in (order.heading or "").lower()
            or "tracking" in (order.heading or "").lower()), \
        f"order-status top hit not a shipping/tracking source: {order.source} ##{order.heading}"
    print(f"OK  'where is my order' -> {order.source} ##{order.heading}")

    ret = top_hits.get("how do I return an item")
    # The right answer is any returns/exchange chunk. Several valid ones exist
    # (the returns FAQ, the return policy, the wrong-item exemplar that issues a
    # return label) — accept any of them by checking the whole chunk + tags.
    ret_blob = (ret.source + " " + (ret.heading or "") + " " + ret.text + " "
                + " ".join(ret.tags)).lower() if ret else ""
    assert ret is not None and ("return" in ret_blob or "exchange" in ret_blob
                                or "send something back" in ret_blob), \
        f"return query top hit not a returns chunk: {ret and ret.source} ##{ret and ret.heading}"
    print(f"OK  'how do I return an item' -> {ret.source} ##{ret.heading}")

    size = top_hits.get("what size should I get")
    assert size is not None and ("siz" in (size.source + (size.heading or "")).lower()), \
        f"sizing query top hit unexpected: {size and size.source}"
    print(f"OK  'what size should I get' -> {size.source} ##{size.heading}")

    cancel = top_hits.get("can I cancel my order")
    assert cancel is not None and "cancel" in (cancel.source + (cancel.heading or "")).lower(), \
        f"cancel query top hit unexpected: {cancel and cancel.source}"
    print(f"OK  'can I cancel my order' -> {cancel.source} ##{cancel.heading}")

    price = top_hits.get("do you offer price adjustments")
    assert price is not None and "price" in (price.source + (price.heading or "")).lower(), \
        f"price-adjustment query top hit unexpected: {price and price.source}"
    print(f"OK  'do you offer price adjustments' -> {price.source} ##{price.heading}")

    assert gap_hits == [], f"nonsense query should return [] but got {len(gap_hits)} hits"
    assert has_answer(nonsense_query) is False, "has_answer() should be False on a KB gap"
    print("OK  nonsense query -> [] (KB gap detected cleanly)")

    print("\nKB_CLIENT SMOKE TEST OK")


if __name__ == "__main__":
    _smoke_test()
