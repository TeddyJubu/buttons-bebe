"""index_kb.py -- (re)build the search index from your markdown files.

Run this whenever you add or change content:   ./update.sh

It reads everything in vault/, turns it into a searchable form, and stores it
in the lancedb/ folder. Old index is replaced each time, so it always matches
your current files.
"""
import os
import sys
import warnings

# make sure we can import kb_lib no matter where this is run from
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lancedb
from kb_lib import DB_DIR, TABLE, KBChunk, load_rows, embed_passages


def main() -> None:
    rows = load_rows()
    if not rows:
        print("No content found. Add some .md files to the content folders, then re-run.")
        return

    print(f"Reading {len(rows)} chunks from your content ...")
    print("Turning text into search fingerprints (first run downloads the model)...")
    vectors = embed_passages([r["text"] for r in rows])
    for row, vec in zip(rows, vectors):
        row["vector"] = vec

    db = lancedb.connect(str(DB_DIR))
    table = db.create_table(TABLE, schema=KBChunk, mode="overwrite")
    table.add(rows)

    # keyword (full-text) index -- the other half of hybrid search
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            table.create_fts_index("text", replace=True, use_tantivy=False)
        except TypeError:
            table.create_fts_index("text", replace=True)

    print(f"Done. Indexed {len(rows)} chunks into {DB_DIR}/{TABLE}.")


if __name__ == "__main__":
    main()
