#!/usr/bin/env python3
"""
embeddings.py — the pluggable text-embedding seam for the KB semantic backend.

Stage 4, Task 12 (Buttons Bebe AI support agent). This is the single place the
rest of the system asks "turn this text into a vector". Callers (the ingestion
worker that fills pgvector, and later the pgvector kb_client backend that embeds
queries) import ONLY this module's small surface:

    from embeddings import embed_texts, embed_one, EMBED_DIM, to_pgvector_literal

so we can later swap the local fastembed model for an OpenAI-compatible endpoint
(e.g. Ollama Cloud) by editing THIS file / config alone — no caller changes.

Backend today: fastembed (ONNX, CPU) running `BAAI/bge-small-en-v1.5`, which
produces **384-dim, L2-normalized** vectors. Because the vectors are normalized,
cosine distance (the pgvector `<=>` operator on our `vector_cosine_ops` HNSW
index) is the right similarity metric and `similarity = 1 - (a <=> b)`.

  ┌──────────────────────────────────────────────────────────────────────────┐
  │ EMBED_DIM MUST EQUAL THE DB COLUMN `embedding vector(384)` (schema.sql).  │
  │ Changing the model to one with a different output dimension requires      │
  │ rebuilding that column and a FULL re-ingest of kb_chunks. Do not change   │
  │ the model dim without re-ingesting (ingestion_worker.py full).            │
  └──────────────────────────────────────────────────────────────────────────┘

Config: the model name comes from (first wins)
  1. env var EMBED_MODEL
  2. config.json  ->  {"embeddings": {"model": "..."}}
  3. default BAAI/bge-small-en-v1.5
The fastembed model is lazy-loaded once into a process-wide singleton (it takes
~0.4s to spin up from the on-disk cache) and reused for every call.

Run directly for a smoke test:
    .venv/bin/python embeddings.py
"""

import json
import logging
import math
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import dotenv_loader; dotenv_loader.load()
except ImportError:
    pass

log = logging.getLogger("embeddings")

# --------------------------------------------------------------------------- #
# Constants / config
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Output dimension of the active model. MUST match the DB `vector(N)` column
# (see schema.sql). If you change the model to a different dimension you MUST
# update schema.sql AND re-ingest every chunk.
EMBED_DIM = 384

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

# Where fastembed keeps the downloaded ONNX model. Honoured if already set
# (the model is pre-downloaded into /tmp/fastembed_cache on this host); we only
# provide a default so a cold run still finds / reuses one location.
_DEFAULT_CACHE_DIR = os.environ.get("FASTEMBED_CACHE_PATH", "/tmp/fastembed_cache")


def _resolve_model_name():
    """Model name from env EMBED_MODEL, else config.json embeddings.model, else default."""
    env_name = os.environ.get("EMBED_MODEL")
    if env_name:
        return env_name.strip()
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        emb = cfg.get("embeddings") or {}
        name = emb.get("model")
        if name:
            return str(name).strip()
    except (OSError, ValueError):
        # No config / unreadable / not JSON -> fall through to the default.
        pass
    return _DEFAULT_MODEL


# The active model name, resolved once at import (config is static at runtime).
MODEL_NAME = _resolve_model_name()


# --------------------------------------------------------------------------- #
# Lazy singleton model (loaded once, reused for every embed call)
# --------------------------------------------------------------------------- #
_MODEL = None
_MODEL_LOCK = threading.Lock()


def _get_model():
    """Return the process-wide fastembed model, loading it on first use.

    Loading is guarded by a lock so concurrent first-callers don't each spin up
    a model. ~0.4s from the on-disk cache; instant thereafter.
    """
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                # Imported lazily so merely importing this module (e.g. for
                # to_pgvector_literal) does not pull in onnxruntime.
                from fastembed import TextEmbedding

                log.info("Loading embedding model %s …", MODEL_NAME)
                _MODEL = TextEmbedding(
                    model_name=MODEL_NAME, cache_dir=_DEFAULT_CACHE_DIR
                )
                log.info("Embedding model %s ready.", MODEL_NAME)
    return _MODEL


# --------------------------------------------------------------------------- #
# Public API — the stable seam
# --------------------------------------------------------------------------- #
def embed_texts(texts):
    """Embed a list of strings -> list[list[float]] (one 384-float vector each).

    Order is preserved (vector i corresponds to texts[i]). Empty input -> [].
    Vectors are returned as plain Python lists of floats (JSON/DB friendly), not
    numpy arrays. The underlying model batches internally.
    """
    if not texts:
        return []
    model = _get_model()
    # fastembed yields numpy arrays in input order; tolist() -> JSON-safe floats.
    return [vec.tolist() for vec in model.embed(list(texts))]


def embed_one(text):
    """Embed a single string -> list[float] of length EMBED_DIM."""
    out = embed_texts([text])
    return out[0] if out else []


def to_pgvector_literal(vec):
    """Render a float vector as a pgvector text literal, e.g. "[0.1,0.2,...]".

    Insert/compare with an explicit cast: `'...'::vector`. Uses repr() so the
    full float precision survives the round-trip into the DB.
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# --------------------------------------------------------------------------- #
# Smoke test — only when run directly.
# --------------------------------------------------------------------------- #
def _smoke_test():
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    print(f"model: {MODEL_NAME}   EMBED_DIM: {EMBED_DIM}")
    vecs = embed_texts(["track my package", "what is your return policy"])
    assert len(vecs) == 2, f"expected 2 vectors, got {len(vecs)}"
    for i, v in enumerate(vecs):
        assert len(v) == EMBED_DIM, f"vec {i} dim {len(v)} != {EMBED_DIM}"
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 1e-3, f"vec {i} not unit-normalized: norm={norm}"
        print(f"  vec[{i}] dim={len(v)} norm={norm:.6f}")

    one = embed_one("hello")
    assert len(one) == EMBED_DIM
    lit = to_pgvector_literal(one)
    assert lit.startswith("[") and lit.endswith("]")
    print(f"  to_pgvector_literal len={len(lit)} head={lit[:40]}…")

    print("EMBEDDINGS OK")


if __name__ == "__main__":
    _smoke_test()
