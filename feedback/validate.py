"""validate.py — prove the loop actually helps (the M5 gate).

The plumbing working ("a file got written") is NOT proof the agent improved. Before
flipping CLAUDE.md §8 from STUB to LIVE, run a before/after check:

    1. pick a promoted-and-confirmed exemplar and a question it should answer;
    2. BEFORE: search the KB for that question, note whether the exemplar is absent;
    3. re-index (review_learned.py reindex);
    4. AFTER: search again and confirm the exemplar is now retrieved.

If the exemplar never surfaces in retrieval, promotion changed nothing and the loop
is a no-op — do not go LIVE. This module shells out to the KB's own search so it
uses the exact same retrieval the agent uses. Runs on the VPS (where LanceDB is
installed); off-box it degrades to a clear message instead of a false pass.
"""
from __future__ import annotations

import subprocess

from . import config


def _search(query: str, limit: int = 5) -> str:
    """Run the KB's own search and return raw text output (best-effort)."""
    for candidate in ("search.sh", "scripts/search.py"):
        path = config.KB_ROOT / candidate
        if path.exists():
            cmd = (["bash", str(path), query] if candidate.endswith(".sh")
                   else ["python3", str(path), query, str(limit)])
            try:
                return subprocess.run(
                    cmd, cwd=str(config.KB_ROOT), capture_output=True, text=True, timeout=60
                ).stdout
            except Exception as e:  # pragma: no cover
                return f"__ERROR__ {e!r}"
    return "__NO_SEARCH_ENTRYPOINT__"


def retrieval_contains(query: str, needle: str, limit: int = 5) -> dict:
    """Does searching `query` surface something matching `needle`
    (e.g. a ticket id or exemplar filename)?"""
    out = _search(query, limit)
    if out.startswith("__NO_SEARCH") or out.startswith("__ERROR__"):
        return {"ok": False, "usable": False, "detail": out.strip()}
    return {"ok": needle.lower() in out.lower(), "usable": True, "output": out}


def before_after(query: str, needle: str) -> dict:
    """Meant to be called AFTER promoting + reindexing. Returns whether the new
    exemplar is now retrieved. Compare with a run taken before you reindexed."""
    result = retrieval_contains(query, needle)
    verdict = (
        "PASS — exemplar is retrieved; promotion changed retrieval."
        if result.get("ok")
        else "FAIL — exemplar NOT retrieved; do not flip STUB->LIVE."
    )
    return {"query": query, "needle": needle, "verdict": verdict, **result}


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("usage: python -m feedback.validate \"<question>\" \"<ticket-id-or-filename>\"")
        raise SystemExit(2)
    print(json.dumps(before_after(sys.argv[1], sys.argv[2]), indent=2)[:2000])
