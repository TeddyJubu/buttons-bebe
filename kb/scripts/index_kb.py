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
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _promote(staged_db: pathlib.Path) -> None:
    """Swap a completed staged database into place, restoring the old one on error."""
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
            shutil.rmtree(backup_db)


def main() -> None:
    with _index_lock():
        rows = load_rows()
        if not rows:
            print("No content found. Add some .md files to the content folders, then re-run.")
            return

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

            _promote(staging_root)
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)

        print(f"Done. Indexed {len(rows)} chunks into {DB_DIR}/{TABLE}.")


if __name__ == "__main__":
    main()
