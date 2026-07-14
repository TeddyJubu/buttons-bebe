"""index_kb.py -- (re)build the search index from your markdown files.

Run this whenever you add or change content:   ./update.sh

It reads the indexed content folders, builds a complete sibling staging database,
then promotes it under a non-blocking process lock. The last known-good index is
restored if staging or promotion fails.
"""
import os
import fcntl
import pathlib
import shutil
import sys
import tempfile
import warnings
from contextlib import contextmanager

# make sure we can import kb_lib no matter where this is run from
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lancedb
from kb_lib import DB_DIR, TABLE, KBChunk, load_rows, embed_passages

LOCK_PATH = DB_DIR.parent / ".index_kb.lock"
CONTENT_FIELDS = (
    "id",
    "file",
    "title",
    "category",
    "status",
    "source",
    "tags",
    "heading",
    "sensitive",
    "text",
)


@contextmanager
def _index_lock():
    """Fail fast when another rebuild already owns the KB index lock."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("index rebuild already running")
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                # Unlock cleanup must not turn a completed promotion into a
                # reported rebuild failure.
                pass


@contextmanager
def _promotion_lock():
    """Keep readers off the index only for the brief directory swap."""
    path = DB_DIR.parent / ".index_kb.promote.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _promote(staged_db: pathlib.Path) -> None:
    """Swap a completed staged database into place, restoring the old one on error."""
    with _promotion_lock():
        backup_db: pathlib.Path | None = None
        if DB_DIR.exists():
            backup_db = pathlib.Path(tempfile.mkdtemp(prefix=".lancedb-backup-", dir=DB_DIR.parent))
            backup_db.rmdir()
            os.replace(DB_DIR, backup_db)
        try:
            os.replace(staged_db, DB_DIR)
        except Exception:
            if backup_db is not None and backup_db.exists() and not DB_DIR.exists():
                os.replace(backup_db, DB_DIR)
            raise
        else:
            if backup_db is not None and backup_db.exists():
                # os.replace above is the commit point. Backup cleanup is best
                # effort so callers never roll their source corpus back after
                # the new index has already become live.
                try:
                    shutil.rmtree(backup_db)
                except BaseException:
                    pass


def _content_manifest(rows: list[dict]) -> dict[str, tuple]:
    manifest: dict[str, tuple] = {}
    for row in rows:
        row_id = str(row.get("id", ""))
        if not row_id:
            raise SystemExit("refusing index row without an id")
        if row_id in manifest:
            raise SystemExit(f"refusing duplicate index row id: {row_id}")
        try:
            manifest[row_id] = tuple(row[field] for field in CONTENT_FIELDS)
        except KeyError as exc:
            raise SystemExit(f"refusing index row missing field: {exc.args[0]}") from exc
    return manifest


def _validate_staged_table(table, expected_rows: list[dict]) -> None:
    """Prove staged source text and risk labels match the parsed corpus."""
    expected = _content_manifest(expected_rows)
    try:
        persisted_rows = table.to_arrow().select(list(CONTENT_FIELDS)).to_pylist()
    except Exception as exc:
        raise SystemExit(f"could not validate staged index: {exc}") from exc
    actual = _content_manifest(persisted_rows)
    if actual != expected:
        missing = len(expected.keys() - actual.keys())
        extra = len(actual.keys() - expected.keys())
        changed = sum(
            expected[row_id] != actual[row_id]
            for row_id in expected.keys() & actual.keys()
        )
        raise SystemExit(
            "staged index content mismatch: "
            f"expected={len(expected)} actual={len(actual)} "
            f"missing={missing} extra={extra} changed={changed}"
        )


def rebuild_index_locked() -> None:
    """Build and promote an index while the caller holds ``_index_lock``."""
    rows = [dict(row) for row in load_rows()]
    if not rows:
        raise SystemExit(
            "No content found; refusing to replace the last-known-good index."
        )

    print(f"Reading {len(rows)} chunks from your content ...")
    print("Turning text into search fingerprints (first run downloads the model)...")
    vectors = embed_passages([r["text"] for r in rows])
    if len(vectors) != len(rows):
        raise SystemExit(f"embedding count mismatch: rows={len(rows)} vectors={len(vectors)}")
    for row, vec in zip(rows, vectors):
        row["vector"] = vec

    staging_root = pathlib.Path(tempfile.mkdtemp(prefix=".lancedb-staging-", dir=DB_DIR.parent))
    try:
        db = lancedb.connect(str(staging_root))
        table = db.create_table(TABLE, schema=KBChunk, mode="overwrite")
        table.add(rows)

        # keyword (full-text) index -- the other half of hybrid search
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                table.create_fts_index("text", replace=True, use_tantivy=False)
            except TypeError:
                table.create_fts_index("text", replace=True)

        _validate_staged_table(table, rows)
        print(f"Publishing {len(rows)} validated chunks to {DB_DIR}/{TABLE} ...")
        _promote(staging_root)
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)

    # Nothing after the promotion commit point may raise: product sync treats
    # any rebuild exception as pre-commit and restores the previous corpus.


def main() -> None:
    with _index_lock():
        rebuild_index_locked()


if __name__ == "__main__":
    main()
