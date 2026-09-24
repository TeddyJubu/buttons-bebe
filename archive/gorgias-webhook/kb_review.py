#!/usr/bin/env python3
"""
kb_review.py — Owner review queue for enriched KB clusters.

Isolated from the live KB pipeline: reads enriched_clusters.jsonl, stores
votes/comments in kb_review_state.json. Does NOT touch kb/, pgvector, or
ingestion_worker until Hermes promotes approved items separately.
"""

import fcntl
import json
import os
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_PATH = os.path.join(SCRIPT_DIR, "exports", "kb_processing", "enriched_clusters.jsonl")
REVIEW_PATH = os.path.join(SCRIPT_DIR, "exports", "kb_processing", "enriched_clusters_review.jsonl")
CLUSTERS_PATH = REVIEW_PATH if os.path.isfile(REVIEW_PATH) else SOURCE_PATH
STATE_PATH = os.path.join(SCRIPT_DIR, "exports", "kb_processing", "kb_review_state.json")
PER_PAGE = 20

_cache = {"clusters": None, "mtime": None}


def _load_clusters():
    mtime = os.path.getmtime(CLUSTERS_PATH)
    if _cache["clusters"] is not None and _cache["mtime"] == mtime:
        return _cache["clusters"]
    clusters = []
    with open(CLUSTERS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            clusters.append(json.loads(line))
    clusters.sort(
        key=lambda c: (-int(c.get("size") or 0), -float(c.get("confidence") or 0)),
    )
    _cache["clusters"] = clusters
    _cache["mtime"] = mtime
    return clusters


def _load_state():
    if not os.path.isfile(STATE_PATH):
        return {"votes": {}, "comments": {}}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("votes", {})
    data.setdefault("comments", {})
    return data


def _save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_PATH)


def _cluster_ids():
    return {str(c["cluster_id"]) for c in _load_clusters()}


def get_stats():
    clusters = _load_clusters()
    state = _load_state()
    votes = state.get("votes", {})
    up = sum(1 for v in votes.values() if v == "up")
    down = sum(1 for v in votes.values() if v == "down")
    reviewed = len(votes)
    return {
        "total": len(clusters),
        "reviewed": reviewed,
        "approved": up,
        "rejected": down,
        "pending": len(clusters) - reviewed,
        "comments": len(state.get("comments", {})),
        "auto_approved": sum(1 for c in clusters if c.get("auto_approved")),
        "source": "review" if CLUSTERS_PATH == REVIEW_PATH else "raw",
    }


def get_page(page=1, per_page=PER_PAGE, filter_status=None):
    clusters = _load_clusters()
    state = _load_state()
    votes = state.get("votes", {})
    comments = state.get("comments", {})

    items = []
    for c in clusters:
        cid = str(c["cluster_id"])
        vote = votes.get(cid)
        if filter_status == "pending" and vote is not None:
            continue
        if filter_status == "up" and vote != "up":
            continue
        if filter_status == "down" and vote != "down":
            continue
        items.append({
            **c,
            "cluster_id": cid,
            "vote": vote,
            "comment": comments.get(cid, ""),
        })

    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * per_page
    page_items = items[start : start + per_page]

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "items": page_items,
        "stats": get_stats(),
    }


def set_vote(cluster_id, vote):
    cid = str(cluster_id)
    if vote not in ("up", "down", None):
        raise ValueError("vote must be 'up', 'down', or null")
    if cid not in _cluster_ids():
        raise ValueError(f"unknown cluster_id: {cid}")

    state = _load_state()
    votes = state.setdefault("votes", {})
    if vote is None:
        votes.pop(cid, None)
    else:
        votes[cid] = vote
    _save_state(state)
    return {"cluster_id": cid, "vote": vote}


def set_comment(cluster_id, comment):
    cid = str(cluster_id)
    if cid not in _cluster_ids():
        raise ValueError(f"unknown cluster_id: {cid}")

    state = _load_state()
    comments = state.setdefault("comments", {})
    comment = (comment or "").strip()
    if comment:
        comments[cid] = comment
    else:
        comments.pop(cid, None)
    _save_state(state)
    return {"cluster_id": cid, "comment": comments.get(cid, "")}


def get_approved():
    clusters = _load_clusters()
    state = _load_state()
    votes = state.get("votes", {})
    comments = state.get("comments", {})
    approved = []
    for c in clusters:
        cid = str(c["cluster_id"])
        if votes.get(cid) == "up":
            approved.append({
                **c,
                "cluster_id": cid,
                "comment": comments.get(cid, ""),
            })
    return {
        "count": len(approved),
        "items": approved,
        "updated_at": state.get("updated_at"),
    }


def get_rejected():
    clusters = _load_clusters()
    state = _load_state()
    votes = state.get("votes", {})
    comments = state.get("comments", {})
    rejected = []
    for c in clusters:
        cid = str(c["cluster_id"])
        if votes.get(cid) == "down":
            rejected.append({
                **c,
                "cluster_id": cid,
                "comment": comments.get(cid, ""),
            })
    return {
        "count": len(rejected),
        "items": rejected,
        "updated_at": state.get("updated_at"),
    }
