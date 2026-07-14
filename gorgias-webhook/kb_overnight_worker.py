#!/usr/bin/env python3
"""
kb_overnight_worker.py — Overnight KB export processor (checkpointed, resumable).

Stages (runs until all complete):
  1. clean    — filter spam, strip email quotes, normalize text
  2. pair     — extract customer→agent Q/A pairs per ticket
  3. dedupe   — fuzzy-cluster similar questions, pick best answer
  4. enrich   — LLM canonical Q/A + tags per cluster (uses model_gateway; config via .env)

Usage:
  python3 kb_overnight_worker.py run          # run/resume all stages
  python3 kb_overnight_worker.py status       # show progress
  python3 kb_overnight_worker.py run --stage clean   # single stage

LLM config is read from .env / environment (LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL,
LLM_API_KEY) — same as the main server. No separate model config needed.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import dotenv_loader; dotenv_loader.load()
except ImportError:
    pass

import model_gateway

logger = logging.getLogger("kb-overnight")

LABEL = os.environ.get("KB_EXPORT_LABEL", "12mo_2026-06-26")
WORKDIR = SCRIPT_DIR / "exports" / "kb_processing"
STATE_PATH = WORKDIR / "overnight_state.json"

INPUT_MESSAGES = SCRIPT_DIR / "exports" / f"messages_{LABEL}.csv"
INPUT_TICKETS = SCRIPT_DIR / "exports" / f"tickets_{LABEL}.csv"

CLEANED_CSV = WORKDIR / "cleaned_messages.csv"
QUARANTINE_CSV = WORKDIR / "quarantine.csv"
QA_PAIRS_CSV = WORKDIR / "qa_pairs.csv"
QA_UNIQUE_CSV = WORKDIR / "qa_unique.csv"
QA_REVIEW_CSV = WORKDIR / "qa_review.csv"
ENRICHED_JSONL = WORKDIR / "enriched_clusters.jsonl"

SPAM_PATTERNS = [
    r"unsubscribe",
    r"no longer want to receive these emails",
    r"playtime paris",
    r"hebe\.lv",
    r"kmail-lists\.com",
    r"newsletter",
    r"see you tomorrow! come discover our new",
]
QUOTE_SPLIT_RE = re.compile(
    r"\nOn .{10,120} wrote:\s*\n|\n-{2,}\s*Original Message\s*-{2,}",
    re.IGNORECASE,
)
SIG_RE = re.compile(r"\n(?:Best|Thanks|Regards),?\s*\n", re.IGNORECASE)

STAGES = ("clean", "pair", "dedupe", "enrich")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"label": LABEL, "stages": {}, "started_at": _now()}


def save_state(state: dict) -> None:
    state["updated_at"] = _now()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def stage_done(state: dict, stage: str) -> bool:
    return state.get("stages", {}).get(stage, {}).get("status") == "complete"


def mark_stage(state: dict, stage: str, **kwargs) -> None:
    state.setdefault("stages", {}).setdefault(stage, {})
    state["stages"][stage].update(kwargs)
    save_state(state)


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_email_quotes(text: str) -> str:
    text = normalize_text(text)
    parts = QUOTE_SPLIT_RE.split(text)
    head = parts[0] if parts else text
    head = SIG_RE.split(head)[0]
    return head.strip()


def is_spam(body: str, subject: str = "") -> bool:
    blob = f"{subject}\n{body}".lower()
    return any(re.search(p, blob) for p in SPAM_PATTERNS)


def norm_key(text: str) -> str:
    t = text.lower()
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# --------------------------------------------------------------------------- #
# Stage 1: CLEAN
# --------------------------------------------------------------------------- #
def run_clean(state: dict) -> None:
    if stage_done(state, "clean"):
        logger.info("Stage clean: already complete")
        return
    if not INPUT_MESSAGES.exists():
        raise FileNotFoundError(f"Missing input: {INPUT_MESSAGES}")

    mark_stage(state, "clean", status="in_progress", started_at=_now())
    offset = int(state["stages"].get("clean", {}).get("offset", 0))
    cleaned_n = int(state["stages"].get("clean", {}).get("cleaned", 0))
    quarantine_n = int(state["stages"].get("clean", {}).get("quarantined", 0))

    clean_fields = None
    mode = "a" if offset > 0 and CLEANED_CSV.exists() else "w"
    qmode = "a" if offset > 0 and QUARANTINE_CSV.exists() else "w"

    with INPUT_MESSAGES.open(newline="", encoding="utf-8") as inf:
        reader = csv.DictReader(inf)
        clean_fields = reader.fieldnames or []
        extra = ["body_clean", "filter_reason"]
        out_fields = clean_fields + [f for f in extra if f not in clean_fields]

        rows = list(reader)
        total = len(rows)
        batch_size = 500

        with CLEANED_CSV.open(mode, newline="", encoding="utf-8") as cf, \
             QUARANTINE_CSV.open(qmode, newline="", encoding="utf-8") as qf:
            cw = csv.DictWriter(cf, fieldnames=out_fields, extrasaction="ignore")
            qw = csv.DictWriter(qf, fieldnames=out_fields, extrasaction="ignore")
            if mode == "w":
                cw.writeheader()
            if qmode == "w":
                qw.writeheader()

            for i in range(offset, total):
                row = rows[i]
                body = row.get("body_text") or ""
                subject = row.get("ticket_subject") or ""
                speaker = row.get("speaker") or ""

                reason = None
                if speaker == "Internal":
                    reason = "internal"
                elif len(body.strip()) < 15:
                    reason = "too_short"
                elif is_spam(body, subject):
                    reason = "spam_marketing"
                elif speaker not in ("Customer", "Agent"):
                    reason = "unknown_speaker"

                row["body_clean"] = strip_email_quotes(body) if not reason else body
                row["filter_reason"] = reason or ""

                if reason:
                    qw.writerow(row)
                    quarantine_n += 1
                else:
                    cw.writerow(row)
                    cleaned_n += 1

                if (i + 1) % batch_size == 0:
                    mark_stage(state, "clean", offset=i + 1, cleaned=cleaned_n,
                               quarantined=quarantine_n, total=total)
                    logger.info("Clean progress: %s/%s (kept=%s quarantine=%s)",
                                i + 1, total, cleaned_n, quarantine_n)

    mark_stage(state, "clean", status="complete", offset=total,
               cleaned=cleaned_n, quarantined=quarantine_n, total=total,
               completed_at=_now())
    logger.info("Clean complete: kept=%s quarantined=%s", cleaned_n, quarantine_n)


# --------------------------------------------------------------------------- #
# Stage 2: PAIR
# --------------------------------------------------------------------------- #
def run_pair(state: dict) -> None:
    if stage_done(state, "pair"):
        logger.info("Stage pair: already complete")
        return
    if not CLEANED_CSV.exists():
        raise FileNotFoundError(f"Run clean first: {CLEANED_CSV}")

    mark_stage(state, "pair", status="in_progress", started_at=_now())

    by_ticket: dict[str, list[dict]] = defaultdict(list)
    with CLEANED_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_ticket[row["ticket_id"]].append(row)

    for tid in by_ticket:
        by_ticket[tid].sort(key=lambda r: r.get("message_created_at") or "")

    pair_fields = [
        "ticket_id", "ticket_subject", "ticket_tags", "customer_email",
        "customer_name", "question", "answer", "agent_email", "agent_name",
        "question_at", "answer_at", "message_ids",
    ]
    pairs = []
    for tid, msgs in by_ticket.items():
        pending_q = None
        pending_meta = {}
        for msg in msgs:
            speaker = msg.get("speaker")
            body = (msg.get("body_clean") or msg.get("body_text") or "").strip()
            if not body or len(body) < 10:
                continue
            if speaker == "Customer":
                pending_q = body
                pending_meta = {
                    "ticket_id": tid,
                    "ticket_subject": msg.get("ticket_subject", ""),
                    "ticket_tags": msg.get("ticket_tags", ""),
                    "customer_email": msg.get("customer_email", ""),
                    "customer_name": msg.get("customer_name", ""),
                    "question_at": msg.get("message_created_at", ""),
                    "q_msg_id": msg.get("message_id", ""),
                }
            elif speaker == "Agent" and pending_q:
                if len(body) < 40 and body.count("\n") < 2:
                    continue  # skip trivial acks
                pairs.append({
                    "ticket_id": pending_meta["ticket_id"],
                    "ticket_subject": pending_meta["ticket_subject"],
                    "ticket_tags": pending_meta["ticket_tags"],
                    "customer_email": pending_meta["customer_email"],
                    "customer_name": pending_meta["customer_name"],
                    "question_at": pending_meta["question_at"],
                    "question": pending_q,
                    "answer": body,
                    "agent_email": msg.get("sender_email", ""),
                    "agent_name": msg.get("sender_name", ""),
                    "answer_at": msg.get("message_created_at", ""),
                    "message_ids": f"{pending_meta.get('q_msg_id')},{msg.get('message_id','')}",
                })
                pending_q = None

    with QA_PAIRS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pair_fields)
        w.writeheader()
        w.writerows(pairs)

    mark_stage(state, "pair", status="complete", pairs=len(pairs), completed_at=_now())
    logger.info("Pair complete: %s Q/A pairs", len(pairs))


# --------------------------------------------------------------------------- #
# Stage 3: DEDUPE
# --------------------------------------------------------------------------- #
def _token_set(text: str) -> frozenset:
    """Split normalized text into a frozenset of tokens for fast Jaccard pre-filter."""
    return frozenset(text.split())


def _trigram_set(text: str) -> frozenset:
    """Extract character trigrams for fast fuzzy similarity (replaces SequenceMatcher)."""
    if len(text) < 3:
        return frozenset([text]) if text else frozenset()
    return frozenset(text[i:i+3] for i in range(len(text) - 2))


def _jaccard(a: frozenset, b: frozenset) -> float:
    """Fast Jaccard similarity on sets."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _block_key(normalized_text: str) -> str:
    """Generate a blocking key from the first few content words of the question.

    This groups questions that start with the same content words so we only
    compare within small buckets instead of scanning all clusters. This is the
    classic record-linkage "blocking" technique and reduces dedupe from O(n^2)
    to O(n * avg_bucket_size).
    """
    words = normalized_text.split()
    content_words = [w for w in words if len(w) > 2][:3]
    return " ".join(content_words) if content_words else normalized_text[:20]


def run_dedupe(state: dict, similarity_threshold: float = 0.82) -> None:
    if stage_done(state, "dedupe"):
        logger.info("Stage dedupe: already complete")
        return
    if not QA_PAIRS_CSV.exists():
        raise FileNotFoundError(f"Run pair first: {QA_PAIRS_CSV}")

    mark_stage(state, "dedupe", status="in_progress", started_at=_now())

    pairs = list(csv.DictReader(QA_PAIRS_CSV.open(newline="", encoding="utf-8")))
    clusters: list[dict] = []

    # --- Blocking: group questions by first-3-content-words hash ---
    # Only clusters in the same block are compared. This reduces comparisons
    # from O(n^2) to O(n * avg_bucket_size).
    block_to_clusters: dict[str, set[int]] = defaultdict(set)

    # Truncate questions for similarity comparison. The first ~300 chars capture
    # the customer's intent; the rest is usually order details, addresses, etc.
    _MAX_CMP_LEN = 300

    # Trigram Jaccard threshold. Trigram Jaccard scores lower than
    # SequenceMatcher.ratio() for the same pair, so we use a lower threshold
    # (0.55 trigram-Jaccard ≈ 0.82 SequenceMatcher.ratio() on typical text).
    _TRIGRAM_THRESHOLD = 0.55

    for i, pair in enumerate(pairs):
        qn = norm_key(pair["question"])
        if not qn:
            continue
        q_cmp = qn[:_MAX_CMP_LEN]
        q_trigrams = _trigram_set(q_cmp)
        q_block = _block_key(q_cmp)

        # Find candidate clusters only in the same block
        candidate_ids = block_to_clusters.get(q_block, set())

        # Sort candidates by size descending (bigger clusters first)
        placed = False
        for cid in sorted(candidate_ids, key=lambda c: -len(clusters[c]["members"])):
            cluster = clusters[cid]
            # Fast trigram Jaccard — O(n) per comparison
            if _jaccard(q_trigrams, cluster["trigrams"]) < _TRIGRAM_THRESHOLD:
                continue
            # Match!
            cluster["members"].append(pair)
            cluster["ticket_ids"].add(pair["ticket_id"])
            placed = True
            break

        if not placed:
            new_cid = len(clusters)
            clusters.append({
                "question_norm": q_cmp,
                "trigrams": q_trigrams,
                "members": [pair],
                "ticket_ids": {pair["ticket_id"]},
            })
            block_to_clusters[q_block].add(new_cid)

        if (i + 1) % 1000 == 0:
            logger.info("Dedupe progress: %s/%s pairs, %s clusters so far",
                        i + 1, len(pairs), len(clusters))

    unique_fields = [
        "cluster_id", "size", "ticket_count", "question_sample", "answer_best",
        "answer_variance", "tags_hint", "ticket_ids",
    ]
    review_fields = unique_fields + ["conflict_note"]

    unique_rows = []
    review_rows = []
    for cid, cluster in enumerate(clusters, start=1):
        members = cluster["members"]
        answers = [m["answer"] for m in members]
        # Pick longest answer with median length bias (avoid one-liners)
        answers_sorted = sorted(answers, key=lambda a: len(a), reverse=True)
        best = answers_sorted[0]
        if len(answers_sorted) > 1:
            top2 = answers_sorted[:2]
            var = 1.0 - SequenceMatcher(None, norm_key(top2[0]), norm_key(top2[1])).ratio()
        else:
            var = 0.0

        tags = set()
        for m in members:
            for t in (m.get("ticket_tags") or "").split(","):
                t = t.strip()
                if t:
                    tags.add(t)

        row = {
            "cluster_id": cid,
            "size": len(members),
            "ticket_count": len(cluster["ticket_ids"]),
            "question_sample": members[0]["question"][:2000],
            "answer_best": best[:4000],
            "answer_variance": round(var, 3),
            "tags_hint": ", ".join(sorted(tags)[:10]),
            "ticket_ids": ",".join(sorted(cluster["ticket_ids"], key=lambda x: int(x) if x.isdigit() else 0)[:20]),
        }
        if var > 0.35 and len(members) >= 2:
            review_rows.append({**row, "conflict_note": "conflicting_answers"})
        else:
            unique_rows.append(row)

    unique_rows.sort(key=lambda r: (-r["size"], -r["ticket_count"]))

    with QA_UNIQUE_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=unique_fields)
        w.writeheader()
        w.writerows(unique_rows)

    with QA_REVIEW_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=review_fields)
        w.writeheader()
        w.writerows(review_rows)

    mark_stage(state, "dedupe", status="complete",
               clusters=len(clusters), unique=len(unique_rows),
               review=len(review_rows), completed_at=_now())
    logger.info("Dedupe complete: %s clusters, %s unique, %s review",
                len(clusters), len(unique_rows), len(review_rows))


# --------------------------------------------------------------------------- #
# Stage 4: ENRICH (LLM)
# --------------------------------------------------------------------------- #
ENRICH_SYSTEM = """You are a knowledge-base curator for Buttons Bebe, an e-commerce baby clothing store.
Given a cluster of similar customer support Q&A pairs, produce a single canonical FAQ entry.
Rules:
- Be factually conservative; only state what the agent answers support
- Redact customer names, emails, street addresses
- Tag sensitive topics: refund, cancel, damaged, chargeback -> add tag "sensitive"
- Output ONLY valid JSON, no markdown fences"""


def _llm_enrich_cluster(cluster_row: dict, members_sample: list[dict]) -> dict:
    examples = []
    for m in members_sample[:4]:
        examples.append({
            "q": (m.get("question") or "")[:600],
            "a": (m.get("answer") or "")[:800],
        })
    prompt = json.dumps({
        "cluster_size": cluster_row["size"],
        "tags_hint": cluster_row.get("tags_hint", ""),
        "examples": examples,
        "task": "Return JSON: {canonical_question, canonical_answer, tags[], sensitive:bool, confidence:0-1, caveats}",
    }, ensure_ascii=False)

    raw = model_gateway.complete_text(
        prompt,
        system=ENRICH_SYSTEM,
        temperature=0.1,
        max_tokens=800,
        timeout=90,
    )
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {
            "canonical_question": cluster_row["question_sample"][:300],
            "canonical_answer": cluster_row["answer_best"][:500],
            "tags": [],
            "sensitive": False,
            "confidence": 0.3,
            "caveats": "llm_parse_failed",
            "raw": raw[:500],
        }
    return {
        "cluster_id": cluster_row["cluster_id"],
        "size": cluster_row["size"],
        **parsed,
        "enriched_at": _now(),
    }


def run_enrich(state: dict, batch_sleep: float = 0.5) -> None:
    if stage_done(state, "enrich"):
        logger.info("Stage enrich: already complete")
        return
    if not QA_UNIQUE_CSV.exists():
        raise FileNotFoundError(f"Run dedupe first: {QA_UNIQUE_CSV}")

    if not model_gateway.is_live():
        raise RuntimeError(
            "LLM not live. Set LLM_PROVIDER=ollama LLM_MODEL=deepseek-v4-flash "
            "LLM_API_KEY=$OLLAMA_API_KEY"
        )

    mark_stage(state, "enrich", status="in_progress", started_at=_now())

    clusters = list(csv.DictReader(QA_UNIQUE_CSV.open(newline="", encoding="utf-8")))
    pairs_by_ticket = defaultdict(list)
    with QA_PAIRS_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pairs_by_ticket[row["ticket_id"]].append(row)

    done_ids = set()
    if ENRICHED_JSONL.exists():
        for line in ENRICHED_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done_ids.add(json.loads(line)["cluster_id"])

    # Build member lookup by question similarity (cheap: same ticket_ids)
    offset = int(state["stages"].get("enrich", {}).get("offset", 0))
    enriched_n = len(done_ids)
    errors = 0

    with ENRICHED_JSONL.open("a", encoding="utf-8") as outf:
        for i, cluster in enumerate(clusters):
            cid = int(cluster["cluster_id"])
            if cid in done_ids:
                continue
            if i < offset:
                continue

            # Gather member pairs from ticket_ids in cluster
            members = []
            for tid in (cluster.get("ticket_ids") or "").split(",")[:10]:
                tid = tid.strip()
                if tid:
                    members.extend(pairs_by_ticket.get(tid, []))
            if not members:
                members = [{"question": cluster["question_sample"], "answer": cluster["answer_best"]}]

            try:
                result = _llm_enrich_cluster(cluster, members)
                outf.write(json.dumps(result, ensure_ascii=False) + "\n")
                outf.flush()
                enriched_n += 1
            except Exception as exc:
                errors += 1
                logger.error("Enrich cluster %s failed: %s", cid, exc)
                err_row = {
                    "cluster_id": cid,
                    "error": str(exc),
                    "enriched_at": _now(),
                }
                outf.write(json.dumps(err_row, ensure_ascii=False) + "\n")
                outf.flush()

            if (i + 1) % 25 == 0:
                mark_stage(state, "enrich", offset=i + 1, enriched=enriched_n,
                           errors=errors, total=len(clusters))
                logger.info("Enrich progress: %s/%s (errors=%s)", i + 1, len(clusters), errors)

            time.sleep(batch_sleep)

    mark_stage(state, "enrich", status="complete", enriched=enriched_n,
               errors=errors, total=len(clusters), completed_at=_now())
    logger.info("Enrich complete: %s clusters enriched, %s errors", enriched_n, errors)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def is_fully_complete() -> bool:
    if not STATE_PATH.exists():
        return False
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return state.get("status") == "complete"


def run_all(stages: list[str] | None = None) -> dict:
    state = load_state()
    to_run = stages or list(STAGES)
    for stage in to_run:
        logger.info("=== Stage: %s ===", stage)
        if stage == "clean":
            run_clean(state)
        elif stage == "pair":
            run_pair(state)
        elif stage == "dedupe":
            run_dedupe(state)
        elif stage == "enrich":
            run_enrich(state)
        else:
            raise ValueError(f"Unknown stage: {stage}")
        state = load_state()

    all_done = all(stage_done(state, s) for s in STAGES)
    state["status"] = "complete" if all_done else "in_progress"
    save_state(state)
    return state


def print_status() -> None:
    state = load_state()
    print(json.dumps(state, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Overnight KB processor")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_run = sub.add_parser("run")
    p_run.add_argument("--stage", choices=STAGES, help="Run a single stage only")
    p_run.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.cmd == "status":
        print_status()
        return

    stages = [args.stage] if args.stage else None
    result = run_all(stages)
    print(json.dumps({"status": result.get("status"), "stages": result.get("stages")}, indent=2))
    if result.get("status") != "complete" and not args.stage:
        sys.exit(0)  # partial ok when resuming


if __name__ == "__main__":
    main()
