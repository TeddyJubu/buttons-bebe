"""kb_lib.py -- shared helpers for the Buttons Bebe knowledge base.

Everything the indexer and the search tool both need lives here, in one place,
so the rest of the code stays short and easy to read.

It follows the file format described in CONVENTIONS.md:
  - each file is YAML front-matter + `##` sections
  - each `##` section becomes one searchable "chunk"
  - content lives in intents/, faq/, policies/, tickets/
  - the learned/ folder is review-only and is NOT indexed

In plain terms, this file knows how to:
  - find the content files (and skip templates, READMEs, and learned/)
  - split each file into `##` section chunks
  - turn text into vectors ("embeddings") with a small local model
    (no internet call, no API key, works in many languages incl. Hebrew)
  - describe the shape of one row stored in the LanceDB search index
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import frontmatter
from lancedb.pydantic import LanceModel, Vector
from fastembed import TextEmbedding

# ---- where things live --------------------------------------------------
KB_DIR = Path(__file__).resolve().parent.parent   # the KB/ folder
DB_DIR = KB_DIR / "lancedb"                         # the search index (auto-built)
TABLE = "kb"

# Which content folders get indexed, in order of trust (highest first).
# "products" is auto-synced from Shopify (see scripts/sync_products.py).
CONTENT_FOLDERS = ["intents", "faq", "policies", "tickets", "products"]
# learned/ is deliberately excluded until a human promotes a file out of it.

# Tags that mean "this topic is sensitive -> escalate, don't auto-draft".
SENSITIVE_TAGS = {"sensitive", "escalation", "refund", "chargeback", "dispute"}

# ---- the local language model ------------------------------------------
# Small, multilingual (50+ languages incl. Hebrew), runs on CPU, ~0.2 GB,
# downloaded once. No API key, nothing leaves the server.
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
VECTOR_DIM = 384
_model: TextEmbedding | None = None


def _get_model() -> TextEmbedding:
    global _model
    if _model is None:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # keep the output clean
            _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Turn stored content into vectors."""
    model = _get_model()
    return [vec.tolist() for vec in model.embed(list(texts))]


def embed_query(text: str) -> list[float]:
    """Turn a question into a vector."""
    model = _get_model()
    vec = next(iter(model.embed([text])))
    return vec.tolist()


# ---- one row (one `##` chunk) in the search index ----------------------
class KBChunk(LanceModel):
    id: str            # stable id: "<relative/path.md>::<n>"
    file: str          # repo-relative path, e.g. policies/shipping-policy.md
    title: str         # the file's title (front-matter)
    category: str      # intents | faq | policies | tickets
    status: str        # confirmed | DRAFT
    source: str        # provenance, e.g. derived-from-tickets (may be empty)
    tags: str          # comma-joined tags
    heading: str       # the `##` heading of this chunk
    sensitive: bool    # True if tags mark it escalate-only
    text: str          # the text that actually gets searched
    vector: Vector(VECTOR_DIM)


# ---- reading + chunking the markdown -----------------------------------
def _chunks_by_heading(body: str) -> list[tuple[str, str]]:
    """Split a file into (heading, text) pairs, one per `##` section.
    Text before the first `##` is treated as preamble and ignored (per
    CONVENTIONS.md). If a file has no `##` at all, the whole body is one chunk."""
    sections: list[tuple[str, str]] = []
    heading: str | None = None
    buf: list[str] = []
    started = False
    for line in body.splitlines():
        if re.match(r"^##\s", line):        # a level-2 heading (not ### )
            if started:
                sections.append((heading or "", "\n".join(buf).strip()))
            heading = line.lstrip("#").strip()
            buf = []
            started = True
        elif started:
            buf.append(line)
    if started:
        sections.append((heading or "", "\n".join(buf).strip()))
    if not sections:
        whole = body.strip()
        if whole:
            sections.append(("", whole))
    return [(h, t) for h, t in sections if t]


def _iter_content_files():
    for folder in CONTENT_FOLDERS:
        base = KB_DIR / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            if path.name.startswith(("_", ".")):
                continue                     # templates, hidden files
            if path.name.lower() == "readme.md":
                continue                     # folder READMEs have no front-matter
            yield path


def load_rows() -> list[dict]:
    """Read every content file and return one dict per `##` chunk."""
    rows: list[dict] = []
    for path in _iter_content_files():
        rel = path.relative_to(KB_DIR)
        post = frontmatter.load(path)
        meta = post.metadata or {}
        title = str(meta.get("title", path.stem))
        category = str(meta.get("category", rel.parts[0]))
        status = str(meta.get("status", "confirmed"))
        source = str(meta.get("source", ""))
        tags_list = meta.get("tags", []) or []
        tags_list = [str(t).lower() for t in tags_list] if isinstance(tags_list, list) else [str(tags_list)]
        tags = ", ".join(tags_list)
        sensitive = bool(set(tags_list) & SENSITIVE_TAGS)

        for i, (heading, chunk) in enumerate(_chunks_by_heading(post.content)):
            uid = hashlib.sha1(f"{rel}::{i}".encode()).hexdigest()[:16]
            # include title + heading in the searchable text so keyword and
            # meaning search both have the topic words to match on
            searchable = f"{title} -- {heading}\n\n{chunk}" if heading else f"{title}\n\n{chunk}"
            rows.append(
                dict(id=uid, file=str(rel), title=title, category=category,
                     status=status, source=source, tags=tags, heading=heading,
                     sensitive=sensitive, text=searchable)
            )
    return rows
