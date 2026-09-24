-- KB semantic-search schema for the Buttons Bebe support agent (Stage 4).
-- One row per KB chunk (a `##` section of a kb/*.md file, per kb/CONVENTIONS.md).
-- Embeddings are 384-dim (BAAI/bge-small-en-v1.5, normalized) — if the embedding
-- model changes dimension, this column and all rows must be rebuilt (re-ingest).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_key    TEXT PRIMARY KEY,            -- deterministic "<source>#<heading|index>"; upsert key
    source       TEXT NOT NULL,              -- repo-relative path, e.g. kb/policies/shipping-policy.md
    title        TEXT,
    category     TEXT,                        -- policies|faq|tickets|learned
    heading      TEXT,                        -- the ## section heading (NULL/'' for the intro chunk)
    status       TEXT,                        -- DRAFT|confirmed (from front-matter)
    tags         TEXT,                        -- JSON array string
    chunk_text   TEXT NOT NULL,
    content_hash TEXT NOT NULL,               -- hash of normalized chunk_text (skip re-embed if unchanged)
    embedding    vector(384) NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kb_chunks_source ON kb_chunks (source);

-- HNSW cosine index (no training step, good recall for small/medium corpora).
CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding
    ON kb_chunks USING hnsw (embedding vector_cosine_ops);
