"""search_kb.py -- ask the knowledge base a question.

Usage:   ./search.sh "where is my order"

It runs HYBRID search -- keyword search and meaning search at the same time --
then blends the two result lists. This catches both exact words (SKUs, order
numbers, other languages) and paraphrases. Returns the best passages with a
relevance score and the file's risk label.
"""
import os
import fcntl
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lancedb
from kb_lib import DB_DIR, TABLE, embed_query

try:
    # Notice Board: owner-posted overrides that ride on top of every search.
    from notices_lib import as_search_results as _notice_results
except Exception:                      # never let the board break search
    def _notice_results(*_a, **_k):
        return []

K = 5         # how many results to return
POOL = 100    # deep enough to diversify repeated chunks across the 22 intents
RRF_K = 60   # reciprocal-rank-fusion constant (a standard, safe default)


def _diversify_by_file(ranked: list[tuple[str, float]], info: dict[str, dict], k: int):
    """Prefer one result per file, then use second chunks for spare slots."""
    if k <= 0:
        return []
    selected: list[tuple[str, float]] = []
    deferred: list[tuple[str, float]] = []
    seen_files: set[str] = set()
    for item in ranked:
        file = str(info[item[0]].get("file", ""))
        if file not in seen_files:
            selected.append(item)
            seen_files.add(file)
            if len(selected) == k:
                return selected
        else:
            deferred.append(item)
    if len(selected) < k:
        selected.extend(deferred[: k - len(selected)])
    return selected


@contextmanager
def _index_read_lock():
    """Prevent a search from observing the brief staged-index promotion gap."""
    path = DB_DIR.parent / ".index_kb.promote.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def search(query: str, k: int = K) -> list[dict]:
    candidate_pool = max(POOL, k * 20)
    with _index_read_lock():
        db = lancedb.connect(str(DB_DIR))
        table = db.open_table(TABLE)

        # 1) meaning search (vectors)
        qv = embed_query(query)
        vec_hits = table.search(qv).metric("cosine").limit(candidate_pool).to_list()

        # 2) keyword search (full text / BM25)
        try:
            kw_hits = table.search(query, query_type="fts").limit(candidate_pool).to_list()
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

    ranked_all = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    ranked = _diversify_by_file(ranked_all, info, k)
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
