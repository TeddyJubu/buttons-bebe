"""search_kb.py -- ask the knowledge base a question.

Usage:   ./search.sh "where is my order"

It runs HYBRID search -- keyword search and meaning search at the same time --
then blends the two result lists. This catches both exact words (SKUs, order
numbers, other languages) and paraphrases. Returns the best passages with a
relevance score and the file's risk label.
"""
import os
import fcntl
import glob
import re
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lancedb
from kb_lib import CATEGORY_WEIGHT, DB_DIR, PROMOTE_LOCK_PATH, TABLE, embed_query

try:
    # Notice Board: owner-posted overrides that ride on top of every search.
    from notices_lib import as_search_results as _notice_results
except Exception:                      # never let the board break search
    def _notice_results(*_a, **_k):
        return []

K = 5         # how many results to return
POOL = 100    # deep enough to diversify repeated chunks across the 22 intents
RRF_K = 60   # reciprocal-rank-fusion constant (a standard, safe default)
PLATFORM_IDENTIFIER_RE = re.compile(
    r"(?<!\w)[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?!\w)"
)
EXACT_IDENTIFIER_RRF = 1.0 / (RRF_K + 1)
MAX_IDENTIFIER_SCAN_CHARS = 4096
MAX_PLATFORM_IDENTIFIERS = 8
MAX_PLATFORM_IDENTIFIER_CHARS = 80


def _platform_identifiers(query: str) -> tuple[str, ...]:
    """Return a bounded set of distinct snake-case identifiers in the query."""
    identifiers: list[str] = []
    for match in PLATFORM_IDENTIFIER_RE.finditer(query[:MAX_IDENTIFIER_SCAN_CHARS]):
        identifier = match.group(0).casefold()
        if len(identifier) > MAX_PLATFORM_IDENTIFIER_CHARS or identifier in identifiers:
            continue
        identifiers.append(identifier)
        if len(identifiers) == MAX_PLATFORM_IDENTIFIERS:
            break
    return tuple(identifiers)


def _has_exact_identifier(text: str, identifier: str) -> bool:
    """Match a platform identifier as a complete token, not a substring."""
    return re.search(rf"(?<!\w){re.escape(identifier)}(?!\w)", text, re.IGNORECASE) is not None


def _weighted_score(score: float, hit: dict, identifiers: tuple[str, ...] = ()) -> float:
    """Apply exact identifier evidence, then the shared category multiplier."""
    category = hit.get("category")
    weight = CATEGORY_WEIGHT.get(category, 1.0) if isinstance(category, str) else 1.0
    text = str(hit.get("text", ""))
    exact_matches = sum(_has_exact_identifier(text, identifier) for identifier in identifiers)
    return (score + exact_matches * EXACT_IDENTIFIER_RRF) * weight


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
    path = PROMOTE_LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _heal_missing_index() -> str | None:
    """Restore the newest promote-crash backup when DB_DIR is gone (3.9).

    Returns the restored backup name, or None when there is nothing to heal.
    Crash-mid-swap leaves DB_DIR missing with a `.lancedb-backup-*` sibling;
    without this, search fails hard until a human re-runs ./update.sh.
    """
    if DB_DIR.exists():
        return None
    backups = sorted(glob.glob(str(DB_DIR.parent / ".lancedb-backup-*")))
    if not backups:
        return None
    # ponytail: newest backup wins; restore is one rename, logged loudly by the caller.
    newest = backups[-1]
    staging = tempfile.mkdtemp(prefix=".lancedb-heal-", dir=DB_DIR.parent)
    os.rmdir(staging)
    os.replace(newest, str(DB_DIR))
    return newest.rsplit("/", 1)[-1]


def search(query: str, k: int = K) -> list[dict]:
    candidate_pool = max(POOL, k * 20)
    with _index_read_lock():
        healed = _heal_missing_index()
        if healed:
            print(f"search_kb: restored crash-mid-swap backup {healed}; run ./update.sh to rebuild clean",
                  file=sys.stderr)
        try:
            db = lancedb.connect(str(DB_DIR))
            table = db.open_table(TABLE)
        except Exception as exc:
            # ponytail: friendly fail-closed, never a raw LanceDB traceback to Hermes.
            print(f"search_kb: index unavailable ({exc}); run ./update.sh to rebuild",
                  file=sys.stderr)
            return []

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

    # Exact platform identifiers are an independent lexical anchor. Treat each
    # verbatim identifier match as rank-one RRF evidence before applying the
    # category trust prior. Unknown categories keep the neutral multiplier so a
    # malformed row cannot gain extra authority; queries without identifiers
    # retain the ordinary post-RRF weighting behavior.
    identifiers = _platform_identifiers(query)
    weighted_scores = {
        i: _weighted_score(score, info[i], identifiers) for i, score in scores.items()
    }
    ranked_all = sorted(weighted_scores.items(), key=lambda kv: kv[1], reverse=True)
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
