#!/usr/bin/env python3
"""
kb_review_consolidate.py — Semantic merge + auto-approve for KB review queue.

Reads enriched_clusters.jsonl, merges similar Q&A via embedding cosine similarity,
filters noise, auto-approves high-confidence safe FAQs, and writes:
  - exports/kb_processing/enriched_clusters_review.jsonl  (review source)
  - exports/kb_processing/kb_review_state.json            (votes/comments)

Does NOT touch kb/ or pgvector. Safe to re-run (backs up prior review file).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone

import numpy as np

from embeddings import embed_texts

log = logging.getLogger("kb_review_consolidate")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_PATH = os.path.join(SCRIPT_DIR, "exports", "kb_processing", "enriched_clusters.jsonl")
REVIEW_PATH = os.path.join(SCRIPT_DIR, "exports", "kb_processing", "enriched_clusters_review.jsonl")
STATE_PATH = os.path.join(SCRIPT_DIR, "exports", "kb_processing", "kb_review_state.json")
QA_REVIEW_CSV = os.path.join(SCRIPT_DIR, "exports", "kb_processing", "qa_review.csv")
META_PATH = os.path.join(SCRIPT_DIR, "exports", "kb_processing", "kb_review_consolidation.json")
EMBED_CACHE_PATH = os.path.join(SCRIPT_DIR, "exports", "kb_processing", "kb_review_embed_cache.npz")

SIM_THRESHOLD = 0.82
EMBED_BATCH = 256

JUNK_PATTERNS = (
    "sent from my iphone",
    "you received a new message from your online store",
    "contact form",
    "country code:",
    "body:",
    "product:",
    "name:",
    "email:",
)

POLICY_TAGS = frozenset({
    "refund", "return", "cancel", "exchange", "damaged", "sensitive",
    "store credit", "wrong item", "warranty",
})

SAFE_FAQ_TAGS = frozenset({
    "shipping", "tracking", "delivery", "sizing", "size", "pickup",
    "order status", "discount", "order modification",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_source() -> list[dict]:
    rows = []
    with open(SOURCE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_conflict_ids() -> set[str]:
    if not os.path.isfile(QA_REVIEW_CSV):
        return set()
    ids: set[str] = set()
    with open(QA_REVIEW_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("cluster_id") or "").strip()
            if cid:
                ids.add(cid)
    return ids


def _is_junk(cluster: dict) -> bool:
    q = (cluster.get("canonical_question") or "").lower().strip()
    a = (cluster.get("canonical_answer") or "").strip()
    if len(q) < 12 or len(a) < 20:
        return True
    for pat in JUNK_PATTERNS:
        if pat in q:
            return True
    if q.count(">") > 2 or " wrote:" in q:
        return True
    if re.match(r"^(hi|hello|thanks|thank you|ok|yes|no)[\s,!?.]*$", q):
        return True
    return False


def _score(cluster: dict) -> float:
    size = int(cluster.get("size") or 0)
    conf = float(cluster.get("confidence") or 0)
    return size * 10 + conf


def _pick_representative(members: list[dict]) -> dict:
    return max(members, key=_score)


def _merge_tags(members: list[dict]) -> list[str]:
    tags: set[str] = set()
    for m in members:
        for t in m.get("tags") or []:
            if t:
                tags.add(str(t))
    return sorted(tags)


def _merge_members(members: list[dict], group_id: str) -> dict:
    rep = _pick_representative(members)
    total_size = sum(int(m.get("size") or 0) for m in members)
    merged_from = [str(m.get("cluster_id")) for m in members]
    confidences = [float(m.get("confidence") or 0) for m in members]
    sensitive = any(
        m.get("sensitive") in (True, "true", "True") for m in members
    )
    return {
        "cluster_id": group_id,
        "size": str(total_size),
        "canonical_question": rep.get("canonical_question", ""),
        "canonical_answer": rep.get("canonical_answer", ""),
        "tags": _merge_tags(members),
        "sensitive": sensitive,
        "confidence": round(max(confidences), 3),
        "caveats": rep.get("caveats", ""),
        "merged_from": merged_from,
        "merge_count": len(members),
        "consolidated_at": _now(),
    }


def _embed_questions(texts: list[str]) -> np.ndarray:
    """Embed questions, reusing a disk cache keyed by source mtime."""
    src_mtime = os.path.getmtime(SOURCE_PATH)
    if os.path.isfile(EMBED_CACHE_PATH):
        try:
            cache = np.load(EMBED_CACHE_PATH, allow_pickle=True)
            if float(cache["mtime"]) == src_mtime and int(cache["count"]) == len(texts):
                log.info("Using cached embeddings (%d vectors)", len(texts))
                return cache["vectors"]
        except Exception as exc:
            log.warning("Embed cache unreadable, recomputing: %s", exc)

    log.info("Embedding %d questions (batch=%d)…", len(texts), EMBED_BATCH)
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        all_vecs.extend(embed_texts(batch))
        if (i + EMBED_BATCH) % 1024 == 0 or i + EMBED_BATCH >= len(texts):
            log.info("  embedded %d / %d", min(i + EMBED_BATCH, len(texts)), len(texts))

    V = np.asarray(all_vecs, dtype=np.float32)
    np.savez_compressed(EMBED_CACHE_PATH, mtime=src_mtime, count=len(texts), vectors=V)
    log.info("Saved embedding cache → %s", EMBED_CACHE_PATH)
    return V


def semantic_merge(clusters: list[dict], sim_threshold: float = SIM_THRESHOLD) -> list[dict]:
    """Greedy centroid clustering on question embeddings."""
    if not clusters:
        return []

    texts = [c.get("canonical_question") or "" for c in clusters]
    V = _embed_questions(texts)
    norms = np.linalg.norm(V, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    V = V / norms

    order = sorted(range(len(clusters)), key=lambda i: _score(clusters[i]), reverse=True)

    groups: list[dict] = []
    centroids: list[np.ndarray] = []

    for idx in order:
        vec = V[idx]
        member = clusters[idx]
        if not centroids:
            groups.append({"members": [member], "centroid": vec.copy()})
            centroids.append(vec.copy())
            continue

        C = np.stack(centroids)
        sims = C @ vec
        best_j = int(np.argmax(sims))
        best_sim = float(sims[best_j])

        if best_sim >= sim_threshold:
            g = groups[best_j]
            g["members"].append(member)
            n = len(g["members"])
            g["centroid"] = ((g["centroid"] * (n - 1)) + vec) / n
            g["centroid"] /= max(np.linalg.norm(g["centroid"]), 1e-9)
            centroids[best_j] = g["centroid"]
        else:
            groups.append({"members": [member], "centroid": vec.copy()})
            centroids.append(vec.copy())

    merged: list[dict] = []
    for i, g in enumerate(groups, start=1):
        merged.append(_merge_members(g["members"], f"m-{i:04d}"))
    return merged


def _has_conflict(merged_from: list[str], conflict_ids: set[str]) -> bool:
    return bool(conflict_ids.intersection(merged_from))


def should_auto_approve(cluster: dict, conflict_ids: set[str]) -> bool:
    if _is_junk(cluster):
        return False
    if _has_conflict(cluster.get("merged_from") or [cluster.get("cluster_id", "")], conflict_ids):
        return False

    conf = float(cluster.get("confidence") or 0)
    size = int(cluster.get("size") or 0)
    tags = set(cluster.get("tags") or [])
    sensitive = cluster.get("sensitive") in (True, "true", "True")
    q = (cluster.get("canonical_question") or "").strip()
    a = (cluster.get("canonical_answer") or "").strip()

    if sensitive or "sensitive" in tags:
        return False
    if tags & POLICY_TAGS:
        return False

    if conf >= 0.88 and not (tags & POLICY_TAGS) and len(q) >= 15 and len(a) >= 30:
        return True
    if tags & SAFE_FAQ_TAGS and conf >= 0.85 and size >= 2 and len(q) >= 15:
        return True
    if conf >= 0.93 and size >= 2 and len(q) >= 20 and len(a) >= 35:
        return True
    if conf >= 0.90 and int(cluster.get("merge_count") or 1) >= 2 and len(a) >= 45:
        return True
    return False


def should_auto_reject(cluster: dict) -> bool:
    return _is_junk(cluster)


def apply_votes(clusters: list[dict], conflict_ids: set[str], reset: bool) -> dict:
    state = {"votes": {}, "comments": {}} if reset else _load_state_safe()
    auto_up = auto_down = 0

    for c in clusters:
        cid = str(c["cluster_id"])
        if should_auto_reject(c):
            state["votes"][cid] = "down"
            state["comments"][cid] = "Auto-rejected: noise / low-quality question"
            auto_down += 1
        elif should_auto_approve(c, conflict_ids):
            state["votes"][cid] = "up"
            state["comments"][cid] = "Auto-approved: high-confidence FAQ (semantic merge)"
            c["auto_approved"] = True
            auto_up += 1
        else:
            c["auto_approved"] = False

    state["updated_at"] = _now()
    state["consolidation"] = {
        "source_count": None,
        "review_count": len(clusters),
        "auto_approved": auto_up,
        "auto_rejected": auto_down,
        "sim_threshold": SIM_THRESHOLD,
        "at": _now(),
    }
    return state, auto_up, auto_down


def _load_state_safe() -> dict:
    if not os.path.isfile(STATE_PATH):
        return {"votes": {}, "comments": {}}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("votes", {})
    data.setdefault("comments", {})
    return data


def write_outputs(clusters: list[dict], state: dict, meta: dict) -> None:
    os.makedirs(os.path.dirname(REVIEW_PATH), exist_ok=True)
    if os.path.isfile(REVIEW_PATH):
        backup = REVIEW_PATH + ".bak"
        shutil.copy2(REVIEW_PATH, backup)
        log.info("Backed up prior review file → %s", backup)

    with open(REVIEW_PATH, "w", encoding="utf-8") as f:
        for c in clusters:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)

    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def run(sim_threshold: float = SIM_THRESHOLD, reset_votes: bool = True) -> dict:
    source = _load_source()
    conflict_ids = _load_conflict_ids()
    log.info("Loaded %d source clusters, %d conflict ids", len(source), len(conflict_ids))

    non_junk = [c for c in source if not _is_junk(c)]
    junk_count = len(source) - len(non_junk)
    log.info("Pre-filter: %d kept, %d junk (will merge then auto-reject)", len(non_junk), junk_count)

    merged = semantic_merge(non_junk, sim_threshold)
    log.info("Semantic merge: %d → %d groups (%.0f%% reduction)",
             len(non_junk), len(merged), 100 * (1 - len(merged) / max(len(non_junk), 1)))

    review_clusters = merged
    review_clusters.sort(
        key=lambda c: (-int(c.get("size") or 0), -float(c.get("confidence") or 0)),
    )

    state, auto_up, auto_down = apply_votes(review_clusters, conflict_ids, reset_votes)
    state["consolidation"]["source_count"] = len(source)
    state["consolidation"]["junk_filtered"] = junk_count

    meta = {
        "source_count": len(source),
        "non_junk_count": len(non_junk),
        "merged_count": len(merged),
        "review_count": len(review_clusters),
        "junk_filtered": junk_count,
        "auto_approved": auto_up,
        "auto_rejected": auto_down,
        "pending_manual": len(review_clusters) - auto_up - auto_down,
        "sim_threshold": sim_threshold,
        "conflict_ids_excluded_from_auto_approve": len(conflict_ids),
        "at": _now(),
    }

    write_outputs(review_clusters, state, meta)
    return meta


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Semantic merge + auto-approve KB review queue")
    p.add_argument("--sim", type=float, default=SIM_THRESHOLD, help="Cosine similarity threshold")
    p.add_argument("--keep-votes", action="store_true", help="Keep existing manual votes")
    args = p.parse_args()

    meta = run(sim_threshold=args.sim, reset_votes=not args.keep_votes)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
