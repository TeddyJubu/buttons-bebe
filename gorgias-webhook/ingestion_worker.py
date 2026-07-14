#!/usr/bin/env python3
"""
ingestion_worker.py — keep the pgvector KB index in sync with the git kb/ repo.

Stage 4, Task 12 (Buttons Bebe AI support agent). This worker is the writer for
our OWN pgvector backend (we rejected Supermemory). It:

  * parses kb/*.md into chunks via kb_client's PUBLIC parser seam
    (kb_client.iter_chunks / kb_client.parse_file) — so the ingested chunks are
    IDENTICAL to what the file-BM25 backend indexes (no re-implemented chunking);
  * embeds each chunk's text with embeddings.embed_texts (BAAI/bge-small,
    384-dim, normalized);
  * UPSERTs one row per chunk into kb_chunks (schema in infra/pgvector/schema.sql),
    skipping the (expensive) embed when a row's content_hash is unchanged;
  * propagates deletions (a removed file/chunk -> its rows are deleted);
  * supports incremental `sync` driven by `git diff` between the last ingested
    commit and HEAD, so a routine cron only touches what changed.

Connection: localhost only — 127.0.0.1:5433, db kb, user kb. The password is read
from /root/.env (POSTGRES_PASSWORD) or env PGVECTOR_PASSWORD / a full PGVECTOR_DSN.
NEVER printed, NEVER committed.
get_conn() / DSN are exported so the Task 13 pgvector kb_client backend can reuse
the same connection config.

CLI:
    .venv/bin/python ingestion_worker.py full     # re-embed + upsert everything, prune stale
    .venv/bin/python ingestion_worker.py sync      # incremental via git diff since last_sha
    .venv/bin/python ingestion_worker.py status     # row count, distinct sources, last_sha

Robustness: a single file failing to parse or embed is logged and skipped — it
never aborts the whole run. Use .venv/bin/python (needs pg8000 + fastembed).
"""

import hashlib
import json
import logging
import os
import subprocess
import sys
import unicodedata

import pg8000.native

import kb_client
from embeddings import EMBED_DIM, MODEL_NAME, embed_texts, to_pgvector_literal

try:
    import dotenv_loader
    dotenv_loader.load()
except ImportError:
    pass

log = logging.getLogger("ingestion_worker")

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = SCRIPT_DIR  # the git repo root (kb/ lives directly under it)
ENV_FILE = "/root/.env"

# --------------------------------------------------------------------------- #
# DB connection — ONE place; reusable by the Task 13 kb_client backend.
# --------------------------------------------------------------------------- #
DB_HOST = os.environ.get("PGVECTOR_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("PGVECTOR_PORT", "5433"))
DB_NAME = os.environ.get("PGVECTOR_DB", "kb")
DB_USER = os.environ.get("PGVECTOR_USER", "kb")


def _read_env_password():
    """Return POSTGRES_PASSWORD from /root/.env, or None if absent."""
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == "POSTGRES_PASSWORD":
                    return val.strip().strip("'\"")
    except OSError:
        pass
    return None


def _db_password():
    """Resolve the DB password: env vars first, then /root/.env."""
    return (
        os.environ.get("PGVECTOR_PASSWORD")
        or os.environ.get("POSTGRES_PASSWORD")
        or _read_env_password()
    )


# A human-readable DSN string (no password) for logging / Task 13 reuse.
DSN = f"postgresql://{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def get_conn():
    """Open a pg8000 connection to the localhost pgvector DB.

    Honors a full PGVECTOR_DSN override (postgresql://user:pass@host:port/db) so
    ops can point elsewhere without code changes; otherwise builds from the
    host/port/db/user constants + the resolved password. Caller closes it.
    """
    dsn = os.environ.get("PGVECTOR_DSN")
    if dsn:
        from urllib.parse import urlparse

        u = urlparse(dsn)
        return pg8000.native.Connection(
            user=u.username or DB_USER,
            password=u.password,
            host=u.hostname or DB_HOST,
            port=u.port or DB_PORT,
            database=(u.path or "/").lstrip("/") or DB_NAME,
        )
    password = _db_password()
    if not password:
        raise RuntimeError(
            "No DB password: set PGVECTOR_PASSWORD or POSTGRES_PASSWORD in "
            "infra/pgvector/.env"
        )
    return pg8000.native.Connection(
        user=DB_USER, password=password, host=DB_HOST, port=DB_PORT, database=DB_NAME
    )


# --------------------------------------------------------------------------- #
# ingest_state — tiny key/value table tracking the last ingested commit sha.
# --------------------------------------------------------------------------- #
_STATE_DDL = (
    "CREATE TABLE IF NOT EXISTS ingest_state ("
    "key text PRIMARY KEY, value text)"
)
_LAST_SHA_KEY = "last_sha"


def ensure_state_table(conn):
    conn.run(_STATE_DDL)


def get_state(conn, key):
    rows = conn.run("SELECT value FROM ingest_state WHERE key = :k", k=key)
    return rows[0][0] if rows else None


def set_state(conn, key, value):
    conn.run(
        "INSERT INTO ingest_state(key, value) VALUES (:k, :v) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        k=key,
        v=value,
    )


# --------------------------------------------------------------------------- #
# chunk_key / content_hash
# --------------------------------------------------------------------------- #
def _normalize_text(text):
    """Normalize chunk text for hashing: NFC unicode + strip + collapse CRLF.

    Whitespace-insensitive at the edges so a trivial trailing-newline edit does
    not force a re-embed, but internal text changes still flip the hash.
    """
    if text is None:
        text = ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def content_hash(text):
    """sha256 hex of the normalized chunk text."""
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()


def _chunk_keys_for_file(chunks):
    """Assign a deterministic, stable chunk_key to each chunk OF ONE FILE.

    Base key = f"{source}#{heading or ''}". Because a file may have several
    chunks that share a heading (e.g. multiple null-heading intro chunks, or two
    `##` sections with the same title), we append an occurrence index `~N` to
    the 2nd+ collision IN PARSE ORDER. Parse order is stable across runs
    (kb_client walks files deterministically and _split_chunks preserves order),
    so the same chunk always gets the same key -> idempotent upserts.

    Returns list[(chunk_key, chunk)] parallel to `chunks`.
    """
    seen = {}
    out = []
    for c in chunks:
        base = f"{c.source}#{c.heading or ''}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        key = base if n == 0 else f"{base}~{n}"
        out.append((key, c))
    return out


# --------------------------------------------------------------------------- #
# Upsert / delete primitives
# --------------------------------------------------------------------------- #
def _existing_rows_for_source(conn, source):
    """Map chunk_key -> stored row snapshot (hash + metadata) for `source`.

    Returns the content_hash AND the metadata columns so the caller can detect a
    metadata-only drift (e.g. front-matter status DRAFT -> confirmed with the
    chunk text unchanged) and sync it WITHOUT a re-embed.
    """
    rows = conn.run(
        "SELECT chunk_key, content_hash, title, category, heading, status, tags "
        "FROM kb_chunks WHERE source = :s",
        s=source,
    )
    return {
        r[0]: {
            "content_hash": r[1],
            "title": r[2],
            "category": r[3],
            "heading": r[4],
            "status": r[5],
            "tags": r[6],
        }
        for r in rows
    }


def _upsert_chunk(conn, chunk_key, chunk, c_hash, vec):
    """UPSERT one chunk row (embedding passed as a float list -> ::vector)."""
    conn.run(
        "INSERT INTO kb_chunks "
        "(chunk_key, source, title, category, heading, status, tags, "
        " chunk_text, content_hash, embedding, updated_at) "
        "VALUES (:chunk_key, :source, :title, :category, :heading, :status, "
        " :tags, :chunk_text, :content_hash, CAST(:embedding AS vector), now()) "
        "ON CONFLICT (chunk_key) DO UPDATE SET "
        " source = EXCLUDED.source, title = EXCLUDED.title, "
        " category = EXCLUDED.category, heading = EXCLUDED.heading, "
        " status = EXCLUDED.status, tags = EXCLUDED.tags, "
        " chunk_text = EXCLUDED.chunk_text, content_hash = EXCLUDED.content_hash, "
        " embedding = EXCLUDED.embedding, updated_at = now()",
        chunk_key=chunk_key,
        source=chunk.source,
        title=chunk.title,
        category=chunk.category,
        heading=chunk.heading,
        status=chunk.status,
        tags=json.dumps(list(chunk.tags)),
        chunk_text=chunk.text,
        content_hash=c_hash,
        embedding=to_pgvector_literal(vec),
    )


def _update_metadata(conn, chunk_key, chunk):
    """Sync ONLY the metadata columns for an existing chunk_key (no re-embed).

    Used when a chunk's text (and thus content_hash + embedding) is unchanged but
    its front-matter drifted — e.g. status DRAFT -> confirmed, or a retitled /
    re-tagged file. Cheap (no model call); keeps the served metadata in lock-step
    with the git KB. Touches updated_at so the change is observable.
    """
    conn.run(
        "UPDATE kb_chunks SET "
        " source = :source, title = :title, category = :category, "
        " heading = :heading, status = :status, tags = :tags, updated_at = now() "
        "WHERE chunk_key = :chunk_key",
        chunk_key=chunk_key,
        source=chunk.source,
        title=chunk.title,
        category=chunk.category,
        heading=chunk.heading,
        status=chunk.status,
        tags=json.dumps(list(chunk.tags)),
    )


def _delete_source(conn, source):
    """Delete all rows for a source path; return the number deleted."""
    rows = conn.run("DELETE FROM kb_chunks WHERE source = :s RETURNING 1", s=source)
    return len(rows or [])


def _delete_keys(conn, source, keep_keys):
    """Delete rows for `source` whose chunk_key is NOT in keep_keys; return count."""
    rows = conn.run(
        "SELECT chunk_key FROM kb_chunks WHERE source = :s", s=source
    )
    stale = [r[0] for r in rows if r[0] not in keep_keys]
    if stale:
        placeholders = ",".join(f":k{i}" for i in range(len(stale)))
        params = {f"k{i}": k for i, k in enumerate(stale)}
        params["s"] = source
        conn.run(
            f"DELETE FROM kb_chunks WHERE source = :s AND chunk_key IN ({placeholders})",
            **params,
        )
    return len(stale)


# --------------------------------------------------------------------------- #
# Per-file ingest (shared by full + sync). Returns a small stats dict.
# --------------------------------------------------------------------------- #
def _ingest_chunks_for_source(conn, source, chunks):
    """Embed (only what changed) + upsert all `chunks` of one source, prune stale.

    `chunks` is the COMPLETE current chunk list for `source` (may be empty if the
    file no longer parses, in which case all its rows are pruned). Dedup: a chunk
    whose stored content_hash already matches is NOT re-embedded — BUT its
    metadata columns (status/title/category/heading/tags) are still synced if the
    front-matter drifted (e.g. status DRAFT -> confirmed with the text unchanged),
    so the served metadata never goes stale. This keeps the no-re-embed perf win
    while staying idempotent (a true no-op when nothing changed).
    """
    stats = {
        "source": source, "upserted": 0, "skipped": 0,
        "deleted": 0, "embedded": 0, "meta_synced": 0,
    }
    keyed = _chunk_keys_for_file(chunks)
    existing = _existing_rows_for_source(conn, source)

    # Decide which chunks actually need a (re)embed.
    to_embed = []          # list[(chunk_key, chunk, c_hash)]
    unchanged_keys = set()
    pending_meta = []      # list[(chunk_key, chunk)] — applied AFTER embed succeeds
    for chunk_key, chunk in keyed:
        c_hash = content_hash(chunk.text)
        prev = existing.get(chunk_key)
        if prev is not None and prev["content_hash"] == c_hash:
            # Text unchanged -> no re-embed. Queue metadata sync for after embed.
            unchanged_keys.add(chunk_key)
            stats["skipped"] += 1
            new_meta = {
                "title": chunk.title,
                "category": chunk.category,
                "heading": chunk.heading,
                "status": chunk.status,
                "tags": json.dumps(list(chunk.tags)),
            }
            if any(prev.get(k) != v for k, v in new_meta.items()):
                pending_meta.append((chunk_key, chunk))
            continue
        to_embed.append((chunk_key, chunk, c_hash))

    # Batch-embed everything that changed (one model call per source).
    if to_embed:
        vectors = embed_texts([c.text for (_k, c, _h) in to_embed])
        if len(vectors) != len(to_embed):
            raise ValueError(
                f"embed_texts returned {len(vectors)} vectors for {len(to_embed)} chunks — "
                "partial batch failure; refusing to silently drop chunks."
            )
        stats["embedded"] = len(vectors)

    # All DB writes for this source are wrapped in a single transaction so the
    # chunk set is never left partially updated if an error occurs mid-ingest.
    conn.run("BEGIN")
    try:
        if to_embed:
            for (chunk_key, chunk, c_hash), vec in zip(to_embed, vectors):
                if len(vec) != EMBED_DIM:
                    raise ValueError(
                        f"embedding dim {len(vec)} != {EMBED_DIM} for {chunk_key}"
                    )
                _upsert_chunk(conn, chunk_key, chunk, c_hash, vec)
                stats["upserted"] += 1

        # Only apply queued metadata updates once embedding has succeeded.
        for chunk_key, chunk in pending_meta:
            _update_metadata(conn, chunk_key, chunk)
            stats["meta_synced"] += 1

        # Prune any stored rows for this source that are no longer present.
        keep = unchanged_keys | {k for (k, _c, _h) in to_embed}
        stats["deleted"] = _delete_keys(conn, source, keep)
        conn.run("COMMIT")
    except Exception:
        try:
            conn.run("ROLLBACK")
        except Exception:
            pass
        raise

    return stats


# --------------------------------------------------------------------------- #
# Git helpers (for incremental sync)
# --------------------------------------------------------------------------- #
def _git(*args):
    """Run a git command in the repo root; return stdout (stripped)."""
    out = subprocess.run(
        ["git", "-C", REPO_ROOT, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _git_head():
    return _git("rev-parse", "HEAD")


def _git_diff_kb(last_sha, head):
    """Return list[(status, path, old_path|None)] for kb/ changes last_sha..head.

    status is git's name-status code: A/M/D, or Rxxx (rename) / Cxxx (copy). For
    renames/copies git emits old<TAB>new, captured as (old_path, path).
    """
    raw = _git(
        "diff", "--name-status", "-M", f"{last_sha}", f"{head}", "--", "kb/"
    )
    changes = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        code = parts[0]
        if code[0] in ("R", "C") and len(parts) >= 3:
            changes.append((code, parts[2], parts[1]))  # (status, new, old)
        elif len(parts) >= 2:
            changes.append((code, parts[1], None))
    return changes


def _abs(rel_path):
    return os.path.join(REPO_ROOT, rel_path)


def _is_kb_md(path):
    return path.startswith("kb/") and path.lower().endswith(".md")


# --------------------------------------------------------------------------- #
# Public operations
# --------------------------------------------------------------------------- #
def full_ingest():
    """Parse ALL kb/*.md, embed + upsert every chunk, prune everything stale.

    Idempotent: unchanged chunks are skipped (not re-embedded). Sources/chunks
    that no longer exist are deleted. Updates last_sha to current HEAD so a
    subsequent `sync` is a no-op until the next kb/ commit.
    """
    conn = get_conn()
    try:
        ensure_state_table(conn)

        # Group all current chunks by source so we ingest a file at a time.
        by_source = {}
        for chunk in kb_client.iter_chunks():
            by_source.setdefault(chunk.source, []).append(chunk)

        totals = {"sources": 0, "upserted": 0, "skipped": 0, "deleted": 0,
                  "meta_synced": 0, "failed": 0}
        for source, chunks in sorted(by_source.items()):
            try:
                s = _ingest_chunks_for_source(conn, source, chunks)
                totals["sources"] += 1
                totals["upserted"] += s["upserted"]
                totals["skipped"] += s["skipped"]
                totals["deleted"] += s["deleted"]
                totals["meta_synced"] += s["meta_synced"]
                log.info(
                    "ingested %s: +%d upsert, %d skip (%d meta-synced), -%d stale",
                    source, s["upserted"], s["skipped"], s["meta_synced"], s["deleted"],
                )
            except Exception as exc:  # one bad file must not abort the run
                totals["failed"] += 1
                log.error("FAILED to ingest %s: %s", source, exc)

        # Delete rows whose SOURCE FILE no longer exists in the KB at all.
        live_sources = set(by_source)
        db_sources = {r[0] for r in conn.run("SELECT DISTINCT source FROM kb_chunks")}
        for gone in db_sources - live_sources:
            n = _delete_source(conn, gone)
            totals["deleted"] += n
            log.info("removed stale source %s (-%d rows)", gone, n)

        # Anchor last_sha to HEAD only when no sources failed (mirrors sync()).
        if totals["failed"] == 0:
            try:
                set_state(conn, _LAST_SHA_KEY, _git_head())
            except Exception as exc:
                log.warning("could not record last_sha after full ingest: %s", exc)
        else:
            log.warning(
                "Skipping last_sha advance: %d source(s) failed ingest. "
                "Next run will re-ingest from scratch.",
                totals["failed"],
            )

        log.info(
            "FULL INGEST done: %d sources, +%d upserts, %d skipped "
            "(%d meta-synced), -%d deleted, %d failed",
            totals["sources"], totals["upserted"], totals["skipped"],
            totals["meta_synced"], totals["deleted"], totals["failed"],
        )
        return totals
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sync():
    """Incremental ingest: apply only kb/ changes between last_sha and HEAD.

    If no last_sha is recorded (first ever run), falls back to a full_ingest.
    A/M -> re-chunk + upsert that file (+ prune that file's stale chunks).
    D    -> delete all chunks for that source.
    R    -> delete the old source + upsert the new path's chunks.
    Each file is handled independently; a failure on one is logged and skipped.
    Updates last_sha to HEAD at the end.
    """
    conn = get_conn()
    try:
        ensure_state_table(conn)
        last_sha = get_state(conn, _LAST_SHA_KEY)
        head = _git_head()

        if not last_sha:
            log.info("no last_sha recorded -> running full_ingest()")
            conn.close()
            return full_ingest()

        if last_sha == head:
            log.info("sync: already at HEAD %s — nothing to do", head[:8])
            return {"changed": 0, "upserted": 0, "deleted": 0,
                    "meta_synced": 0, "failed": 0}

        changes = _git_diff_kb(last_sha, head)
        totals = {"changed": 0, "upserted": 0, "deleted": 0,
                  "meta_synced": 0, "failed": 0}
        for code, path, old_path in changes:
            try:
                status = code[0]
                if status == "D":
                    if _is_kb_md(path):
                        n = _delete_source(conn, path)
                        totals["deleted"] += n
                        log.info("sync D %s (-%d rows)", path, n)
                elif status in ("R", "C"):
                    # Rename/copy: upsert new path FIRST, then delete old.
                    # (Delete-first is non-atomic: if the upsert fails the source
                    # disappears from the KB with no replacement.)
                    if _is_kb_md(path):
                        chunks = kb_client.parse_file(_abs(path))
                        s = _ingest_chunks_for_source(conn, path, chunks)
                        totals["upserted"] += s["upserted"]
                        totals["deleted"] += s["deleted"]
                        totals["meta_synced"] += s["meta_synced"]
                        log.info(
                            "sync %s %s: +%d upsert, -%d stale",
                            status, path, s["upserted"], s["deleted"],
                        )
                    if status == "R" and old_path and _is_kb_md(old_path):
                        n = _delete_source(conn, old_path)
                        totals["deleted"] += n
                        log.info("sync R old %s (-%d rows)", old_path, n)
                else:  # A or M (or T/typechange) -> (re)ingest the file
                    if _is_kb_md(path):
                        chunks = kb_client.parse_file(_abs(path))
                        s = _ingest_chunks_for_source(conn, path, chunks)
                        totals["upserted"] += s["upserted"]
                        totals["deleted"] += s["deleted"]
                        totals["meta_synced"] += s["meta_synced"]
                        log.info(
                            "sync %s %s: +%d upsert, %d skip (%d meta-synced), -%d stale",
                            status, path, s["upserted"], s["skipped"],
                            s["meta_synced"], s["deleted"],
                        )
                totals["changed"] += 1
            except Exception as exc:
                totals["failed"] += 1
                log.error("sync FAILED on %s %s: %s", code, path, exc)

        if totals["failed"] == 0:
            try:
                set_state(conn, _LAST_SHA_KEY, head)
            except Exception as exc:
                log.warning("could not record last_sha after sync: %s", exc)
        else:
            log.warning(
                "sync %d file(s) FAILED — last_sha NOT advanced to %s so the "
                "next sync will retry the failed files.",
                totals["failed"], head[:8],
            )
        log.info(
            "SYNC done %s..%s: %d files, +%d upserts, %d meta-synced, "
            "-%d deleted, %d failed",
            last_sha[:8], head[:8], totals["changed"], totals["upserted"],
            totals["meta_synced"], totals["deleted"], totals["failed"],
        )
        return totals
    finally:
        try:
            conn.close()
        except Exception:
            pass


def status():
    """Print row count, distinct sources, and the last ingested sha."""
    conn = get_conn()
    try:
        ensure_state_table(conn)
        n_rows = conn.run("SELECT count(*) FROM kb_chunks")[0][0]
        srcs = conn.run("SELECT source, count(*) FROM kb_chunks GROUP BY source ORDER BY source")
        last_sha = get_state(conn, _LAST_SHA_KEY)
        print(f"model:        {MODEL_NAME}  (dim {EMBED_DIM})")
        print(f"db:           {DSN}")
        print(f"rows:         {n_rows}")
        print(f"sources:      {len(srcs)} distinct")
        print(f"last_sha:     {last_sha}")
        for source, n in srcs:
            print(f"   {n:3d}  {source}")
        return {"rows": n_rows, "sources": len(srcs), "last_sha": last_sha}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv):
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "full":
        full_ingest()
        print()
        status()
    elif cmd == "sync":
        sync()
        print()
        status()
    elif cmd == "status":
        status()
    else:
        print(f"usage: {os.path.basename(argv[0])} full|sync|status", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
