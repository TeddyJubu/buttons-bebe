from __future__ import annotations

import os
import fcntl
import glob
import re
import sys
import tempfile
from contextlib import contextmanager
from typing import Literal, TypedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lancedb
from kb_lib import CATEGORY_WEIGHT, DB_DIR, PROMOTE_LOCK_PATH, TABLE, embed_query

from notices_lib import as_search_results as _notice_results

HealthState = Literal["healthy", "degraded", "unavailable"]


class RetrievalHealth(TypedDict):
    state: HealthState
    codes: list[str]


class NoticeHealth(RetrievalHealth):
    active_count: int | None
    operator_action: str


class SearchOutcome(TypedDict):
    status: HealthState
    notice_board: NoticeHealth
    index: RetrievalHealth
    results: list[dict]

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
    # mkdtemp backup names carry random suffixes, so name order is not age
    # order — sort by modification time or the "newest" restore can be the
    # oldest crash.
    backups = sorted(
        glob.glob(str(DB_DIR.parent / ".lancedb-backup-*")), key=os.path.getmtime
    )
    if not backups:
        return None
    # ponytail: newest backup wins; restore is one rename, logged loudly by the caller.
    newest = backups[-1]
    staging = tempfile.mkdtemp(prefix=".lancedb-heal-", dir=DB_DIR.parent)
    os.rmdir(staging)
    os.replace(newest, str(DB_DIR))
    return newest.rsplit("/", 1)[-1]


def _diagnose(code: str, exc: Exception | None = None) -> None:
    known_types = (FileNotFoundError, PermissionError, TypeError, AttributeError,
                   KeyError, IndexError, AssertionError, OSError, ValueError, RuntimeError)
    error_type = next((cls.__name__ for cls in known_types if isinstance(exc, cls)),
                      "Exception" if exc is not None else "none")
    kind = "unexpected" if exc is not None and not isinstance(exc, (OSError, ValueError, RuntimeError)) else "source"
    print(f"search_kb: code={code} kind={kind} error_type={error_type}", file=sys.stderr)


def _index_results(query: str, k: int) -> tuple[RetrievalHealth, list[dict]]:
    candidate_pool = max(POOL, k * 20)
    codes: list[str] = []
    vec_hits: list[dict] = []
    kw_hits: list[dict] = []
    successful_queries = 0
    stage = "index_lock_failed"
    try:
        with _index_read_lock():
            stage = "index_recovery_failed"
            healed = _heal_missing_index()
            if healed:
                _diagnose("index_restored")
            stage = "index_open_failed"
            db = lancedb.connect(str(DB_DIR))
            table = db.open_table(TABLE)
            try:
                qv = embed_query(query)
            except Exception as exc:
                codes.append("embedding_failed")
                _diagnose(codes[-1], exc)
            else:
                try:
                    vec_hits = table.search(qv).metric("cosine").limit(candidate_pool).to_list()
                    successful_queries += 1
                except Exception as exc:
                    codes.append("vector_lookup_failed")
                    _diagnose(codes[-1], exc)
            try:
                kw_hits = table.search(query, query_type="fts").limit(candidate_pool).to_list()
                successful_queries += 1
            except Exception as exc:
                codes.append("keyword_lookup_failed")
                _diagnose(codes[-1], exc)
            stage = "index_lock_failed"
    except Exception as exc:
        codes.append(stage)
        _diagnose(stage, exc)
    if not successful_queries:
        return {"state": "unavailable", "codes": codes}, []
    try:
        results = _rank_results(query, vec_hits, kw_hits, k)
    except Exception as exc:
        _diagnose("index_rows_invalid", exc)
        return {"state": "unavailable", "codes": [*codes, "index_rows_invalid"]}, []
    return {"state": "degraded" if codes else "healthy", "codes": codes}, results


def _rank_results(query: str, vec_hits: list[dict], kw_hits: list[dict], k: int) -> list[dict]:
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
                 source=hit.get("source") or "", tags=hit.get("tags") or "",
                 sensitive=bool(hit.get("sensitive")), heading=hit.get("heading"),
                 text=hit["text"])
        )

    return results


def search(query: str, k: int = K) -> SearchOutcome:
    """Return independent source health and passages, including empty success."""
    try:
        notices = _notice_results()
        notice_health: NoticeHealth = {"state": "healthy", "active_count": len(notices), "codes": [], "operator_action": ""}
    except Exception as exc:
        code = "notice_store_invalid" if isinstance(exc, ValueError) else "notice_read_failed"
        _diagnose(code, exc)
        notices = []
        notice_health = {"state": "unavailable", "active_count": None, "codes": [code],
                         "operator_action": "Inspect the Notice Board. Active owner overrides may still exist."}
    index_health, results = _index_results(query, k)
    states = (notice_health["state"], index_health["state"])
    status: HealthState = "healthy" if states == ("healthy", "healthy") else (
        "unavailable" if states == ("unavailable", "unavailable") else "degraded"
    )
    return {"status": status, "notice_board": notice_health, "index": index_health,
            "results": notices + results}


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: ./search.sh "your question"')
        return
    query = " ".join(sys.argv[1:])
    outcome = search(query)
    for name in ("notice_board", "index"):
        health = outcome[name]
        if health["state"] != "healthy":
            print(f"{name}: {health['state']} ({', '.join(health['codes'])})")
    results = outcome["results"]
    if not results:
        print("No matches found." if outcome["status"] == "healthy" else "No passages available from the readable sources. Check retrieval health before answering.")
        return
    print("\nTop matches")
    for r in results:
        flag = "  [SENSITIVE -> escalate]" if r["sensitive"] else ""
        print(f"\n[{r['score']}]  {r['title']}  >  {r['heading']}{flag}")
        print(f"        (file: {r['file']}, status: {r['status']}, source: {r['source']}, tags: {r['tags']})")
        snippet = r["text"].replace("\n", "\n  ")
        print("  " + snippet[:500])


if __name__ == "__main__":
    main()
