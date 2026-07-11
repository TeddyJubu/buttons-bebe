"""search_kb.py -- ask the knowledge base a question.

Usage:   ./search.sh "where is my order"

It runs HYBRID search -- keyword search and meaning search at the same time --
then blends the two result lists. This catches both exact words (SKUs, order
numbers, other languages) and paraphrases. Returns the best passages with a
relevance score and the file's risk label.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lancedb
from kb_lib import DB_DIR, TABLE, embed_query

try:
    # Notice Board: owner-posted overrides that ride on top of every search.
    from notices_lib import as_search_results as _notice_results
except Exception:                      # never let the board break search
    def _notice_results(*_a, **_k):
        return []

K = 5        # how many results to return
POOL = 20    # how many to pull from each method before blending
RRF_K = 60   # reciprocal-rank-fusion constant (a standard, safe default)


def search(query: str, k: int = K) -> list[dict]:
    db = lancedb.connect(str(DB_DIR))
    table = db.open_table(TABLE)

    # 1) meaning search (vectors)
    qv = embed_query(query)
    vec_hits = table.search(qv).metric("cosine").limit(POOL).to_list()

    # 2) keyword search (full text / BM25)
    try:
        kw_hits = table.search(query, query_type="fts").limit(POOL).to_list()
    except Exception:
        kw_hits = []   # if the keyword index isn't ready, fall back to meaning only

    # 3) blend the two lists with reciprocal rank fusion
    scores: dict[str, float] = {}
    info: dict[str, dict] = {}
    for rank, hit in enumerate(vec_hits):
        i = hit["id"]
        scores[i] = scores.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
        info[i] = hit
    for rank, hit in enumerate(kw_hits):
        i = hit["id"]
        scores[i] = scores.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
        info.setdefault(i, hit)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
    results = []
    for i, score in ranked:
        hit = info[i]
        results.append(
            dict(score=round(score, 4), file=hit["file"], title=hit["title"],
                 category=hit.get("category"), status=hit.get("status"),
                 sensitive=bool(hit.get("sensitive")), heading=hit.get("heading"),
                 text=hit["text"])
        )

    # Notice Board: prepend active owner overrides so the agent always sees them
    # first. Fail-safe -- any error here must not break normal search.
    try:
        notices = _notice_results()
    except Exception:
        notices = []
    return notices + results


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: ./search.sh "your question"')
        return
    query = " ".join(sys.argv[1:])
    results = search(query)
    if not results:
        print("No matches found. If you just added content, run ./update.sh first.")
        return
    print(f'\nTop matches for: "{query}"')
    for r in results:
        flag = "  [SENSITIVE -> escalate]" if r["sensitive"] else ""
        print(f"\n[{r['score']}]  {r['title']}  >  {r['heading']}{flag}")
        print(f"        (file: {r['file']}, status: {r['status']})")
        snippet = r["text"].replace("\n", "\n  ")
        print("  " + snippet[:500])


if __name__ == "__main__":
    main()
