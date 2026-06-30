"""
semantic_search.py — embedding-based KB search using OpenAI text-embedding-3-small.

Embeddings are cached in kb/.embed_cache.json, keyed by sha256 of each file's
content. Only changed/new files are re-embedded on each request.

Interface: identical to search_kb.search_kb() so kb_router.py can use it as a
drop-in fallback. Returns {context, files_used, confidence: HIGH|MEDIUM|LOW|NONE}.

Gracefully returns NONE confidence if the OpenAI client is unavailable or the
embedding call fails — caller receives a safe no-result dict, never an exception.
"""

import hashlib
import json
import logging
from pathlib import Path

import yaml

log = logging.getLogger('teddy.semantic_search')

_EMBED_MODEL  = 'text-embedding-3-small'
_CACHE_FILE   = '.embed_cache.json'
_TOP_N        = 3

# Cosine similarity thresholds for confidence levels
_THRESH_HIGH   = 0.75
_THRESH_MEDIUM = 0.55
_THRESH_LOW    = 0.40

_EMPTY = {'context': '', 'files_used': [], 'confidence': 'NONE'}


# ── Maths ──────────────────────────────────────────────────────────────────────

def _cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if not mag_a or not mag_b:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def _save_cache(cache_path: Path, cache: dict):
    try:
        cache_path.write_text(json.dumps(cache), encoding='utf-8')
    except Exception as e:
        log.warning("Embed cache write failed: %s", e)


# ── OKF file parsing ──────────────────────────────────────────────────────────

def _text_for_embedding(path: Path) -> str:
    """Return title + body text from an OKF file (stripped of YAML fences)."""
    try:
        raw = path.read_text(encoding='utf-8')
    except Exception:
        return ''
    if raw.startswith('---'):
        parts = raw.split('---', 2)
        if len(parts) >= 3:
            title = ''
            try:
                fm = yaml.safe_load(parts[1]) or {}
                title = fm.get('title', '')
            except Exception:
                pass
            body = parts[2].strip()
            return f"{title}\n\n{body}" if title else body
    return raw.strip()


# ── Main entry ────────────────────────────────────────────────────────────────

def search(message: str, kb_dir: str, llm_client) -> dict:
    """
    Embed message + all KB files, rank by cosine similarity.

    llm_client: an openai.OpenAI instance (passed in to avoid import-time
                side-effects and to share the module-level singleton).
    Returns {context, files_used, confidence} — same shape as search_kb.search_kb().
    """
    if not llm_client:
        return _EMPTY

    kb_path = Path(kb_dir)
    if not kb_path.exists():
        return _EMPTY

    md_files = [
        p for p in kb_path.rglob('*.md')
        if p.name not in ('index.md', 'log.md')
    ]
    if not md_files:
        return _EMPTY

    cache_path = kb_path / _CACHE_FILE
    cache = _load_cache(cache_path)
    cache_dirty = False

    # Build embedding for every KB file (use cache when file unchanged)
    docs = []
    for p in md_files:
        try:
            fhash = _file_hash(p)
        except Exception:
            continue
        if fhash in cache:
            emb = cache[fhash]
        else:
            text = _text_for_embedding(p)
            if not text.strip():
                continue
            try:
                resp = llm_client.embeddings.create(model=_EMBED_MODEL, input=text[:2000])
                emb = resp.data[0].embedding
                cache[fhash] = emb
                cache_dirty = True
            except Exception as e:
                log.warning("Could not embed %s: %s", p.name, e)
                continue
        docs.append({'path': p, 'embedding': emb})

    if cache_dirty:
        _save_cache(cache_path, cache)

    if not docs:
        return _EMPTY

    # Embed the query
    try:
        resp = llm_client.embeddings.create(model=_EMBED_MODEL, input=message[:1000])
        query_emb = resp.data[0].embedding
    except Exception as e:
        log.warning("Could not embed query: %s", e)
        return _EMPTY

    # Rank by cosine similarity
    scored = sorted(
        ((doc, _cosine(query_emb, doc['embedding'])) for doc in docs),
        key=lambda x: x[1],
        reverse=True,
    )

    top_score = scored[0][1] if scored else 0.0
    if top_score >= _THRESH_HIGH:
        confidence = 'HIGH'
    elif top_score >= _THRESH_MEDIUM:
        confidence = 'MEDIUM'
    elif top_score >= _THRESH_LOW:
        confidence = 'LOW'
    else:
        return _EMPTY

    # Collect top N docs above the LOW threshold
    top_docs = [doc for doc, sim in scored[:_TOP_N] if sim >= _THRESH_LOW]

    sections, files_used = [], []
    for doc in top_docs:
        p = doc['path']
        try:
            raw = p.read_text(encoding='utf-8')
        except Exception:
            continue
        if raw.startswith('---'):
            parts = raw.split('---', 2)
            fm = {}
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except Exception:
                pass
            body  = parts[2].strip() if len(parts) >= 3 else raw
            title = fm.get('title', p.stem)
        else:
            body  = raw.strip()
            title = p.stem
        sections.append(f"### {title}\n{body}")
        rel = str(
            p.relative_to(kb_path.parent) if kb_path.parent in p.parents else p
        )
        files_used.append(rel)

    return {
        'context':    '\n\n---\n\n'.join(sections),
        'files_used': files_used,
        'confidence': confidence,
    }
