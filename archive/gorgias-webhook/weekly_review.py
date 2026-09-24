#!/usr/bin/env python3
"""
weekly_review.py — Stage 5, Task 16: weekly operational metrics for the owner.

Reads READ-ONLY from feedback.db (the AI support agent's operational store) and
produces a concise, plain-text weekly summary that is sent to the owner via
Telegram (telegram_notify.send_weekly_report). Scheduled by a systemd timer
(infra/weekly-review/) every Monday at 09:00.

WHAT IT REPORTS (over a window, default last 7 days):
  Drafts
    * total drafts in window
    * by status (drafted / escalated / kb_gap / other)
    * actually posted vs dry-run (posted_note_id not null)
    * # KB gaps (kb_gap=1)
    * escalation rate (escalated / total)
    * top categories (priority) — drafts grouped by priority
  Learning loop
    * # human replies captured
    * # draft<->reply comparisons
    * average similarity_score
    * exact-match rate
    * avg / median response_time_sec (when present)

SAFETY / DESIGN:
  * READ-ONLY: every query is a SELECT through feedback_db.get_conn(); this
    module NEVER writes to feedback.db.
  * Parameterized SQL — the date-window cutoff reaches SQL through a ? bind,
    never string-formatted in.
  * Empty-DB / empty-window is handled gracefully: no division by zero, the
    report says "no activity this week" rather than crashing.
  * NEVER raises into a scheduled run: compute_metrics/format_report are pure
    and defensive; the CLI catches everything, logs it, and exits non-zero
    without crashing mid-report.

Stdlib + project imports only (sqlite3 via feedback_db, datetime, statistics).

CLI:
    python3 weekly_review.py                 # compute + PRINT the report only
    python3 weekly_review.py --send          # compute + SEND via Telegram
    python3 weekly_review.py --dry-run       # compute + show what WOULD send
    python3 weekly_review.py --days 14       # change the window
    python3 weekly_review.py --json          # print raw metrics dict as JSON

Public API:
    compute_metrics(days=7, path=...) -> dict
    format_report(metrics) -> str
"""

import argparse
import datetime
import json
import logging
import statistics
import sys

import feedback_db

logger = logging.getLogger("gorgias-weekly-review")

# Status buckets we report explicitly. Anything else falls into "other".
_KNOWN_STATUSES = ("drafted", "escalated", "kb_gap")

# How many top priority/category buckets to list.
_TOP_N = 5


# --------------------------------------------------------------------------- #
# Time window
# --------------------------------------------------------------------------- #
def _window_bounds(days):
    """Return (cutoff_iso, now_iso, days) for the last `days` days, UTC.

    created_at in feedback.db is an ISO8601 UTC string (utc_now_iso()), so an
    ISO string comparison (created_at >= cutoff) is a correct lexicographic
    range filter. `days` is clamped to a sane minimum of 1.
    """
    days = max(1, int(days))
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    return cutoff.isoformat(), now.isoformat(), days


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def _safe_rate(numerator, denominator):
    """numerator/denominator as a float, or 0.0 if denominator is 0."""
    if not denominator:
        return 0.0
    return numerator / denominator


def compute_metrics(days=7, path=None):
    """Compute weekly operational metrics from feedback.db (READ-ONLY).

    Args:
        days — size of the reporting window in days (default 7, min 1).
        path — feedback.db path; defaults to feedback_db.DB_PATH (env-overridable
               via FEEDBACK_DB_PATH). Tests pass a temp db path here.

    Returns:
        A plain dict of metrics (JSON-serializable). Always returns a dict;
        an empty window simply yields zeroed counts and has_activity=False.
        Never writes; opens one short-lived read connection.
    """
    if path is None:
        path = feedback_db.DB_PATH

    cutoff_iso, now_iso, days = _window_bounds(days)

    metrics = {
        "window_days": days,
        "window_start": cutoff_iso,
        "window_end": now_iso,
        "generated_at": feedback_db.utc_now_iso(),
        "db_path": path,
        "has_activity": False,
        # drafts
        "drafts_total": 0,
        "drafts_by_status": {},      # status -> count (known buckets + other)
        "drafts_posted": 0,          # posted_note_id NOT NULL
        "drafts_dry_run": 0,         # posted_note_id IS NULL
        "kb_gaps": 0,
        "escalations": 0,
        "escalation_rate": 0.0,
        "top_priorities": [],        # [(priority, count), ...] desc
        # learning loop
        "replies_total": 0,
        "comparisons_total": 0,
        "avg_similarity": None,
        "exact_matches": 0,
        "exact_match_rate": 0.0,
        "avg_response_time_sec": None,
        "median_response_time_sec": None,
    }

    conn = feedback_db.get_conn(path)
    try:
        # ---- DRAFTS ------------------------------------------------------- #
        # All filtered on created_at >= cutoff (parameterized ? bind).
        drafts_total = conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE created_at >= ?",
            (cutoff_iso,),
        ).fetchone()[0]
        metrics["drafts_total"] = drafts_total

        # By status — initialize known buckets to 0 so the report is stable.
        by_status = {s: 0 for s in _KNOWN_STATUSES}
        other = 0
        for row in conn.execute(
            "SELECT status, COUNT(*) AS c FROM drafts "
            "WHERE created_at >= ? GROUP BY status",
            (cutoff_iso,),
        ):
            status = row["status"]
            count = row["c"]
            if status in by_status:
                by_status[status] = count
            else:
                other += count
        if other:
            by_status["other"] = other
        metrics["drafts_by_status"] = by_status
        metrics["escalations"] = by_status.get("escalated", 0)

        # Posted vs dry-run (posted_note_id presence is the source of truth).
        metrics["drafts_posted"] = conn.execute(
            "SELECT COUNT(*) FROM drafts "
            "WHERE created_at >= ? AND posted_note_id IS NOT NULL",
            (cutoff_iso,),
        ).fetchone()[0]
        metrics["drafts_dry_run"] = conn.execute(
            "SELECT COUNT(*) FROM drafts "
            "WHERE created_at >= ? AND posted_note_id IS NULL",
            (cutoff_iso,),
        ).fetchone()[0]

        # KB gaps (the kb_gap flag — independent of status bucketing).
        metrics["kb_gaps"] = conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE created_at >= ? AND kb_gap = 1",
            (cutoff_iso,),
        ).fetchone()[0]

        metrics["escalation_rate"] = _safe_rate(metrics["escalations"], drafts_total)

        # Top priorities (categories) — newest-window drafts grouped by priority.
        top_priorities = []
        for row in conn.execute(
            "SELECT priority, COUNT(*) AS c FROM drafts "
            "WHERE created_at >= ? GROUP BY priority "
            "ORDER BY c DESC, priority ASC LIMIT ?",
            (cutoff_iso, _TOP_N),
        ):
            top_priorities.append((row["priority"], row["c"]))
        metrics["top_priorities"] = top_priorities

        # ---- LEARNING LOOP ------------------------------------------------ #
        metrics["replies_total"] = conn.execute(
            "SELECT COUNT(*) FROM replies WHERE created_at >= ?",
            (cutoff_iso,),
        ).fetchone()[0]

        comparisons_total = conn.execute(
            "SELECT COUNT(*) FROM comparisons WHERE created_at >= ?",
            (cutoff_iso,),
        ).fetchone()[0]
        metrics["comparisons_total"] = comparisons_total

        # Average similarity — AVG ignores NULL scores; guard the all-NULL case.
        avg_sim_row = conn.execute(
            "SELECT AVG(similarity_score) FROM comparisons "
            "WHERE created_at >= ? AND similarity_score IS NOT NULL",
            (cutoff_iso,),
        ).fetchone()
        avg_sim = avg_sim_row[0] if avg_sim_row else None
        metrics["avg_similarity"] = round(avg_sim, 4) if avg_sim is not None else None

        # Exact-match count + rate (rate over all comparisons in window).
        metrics["exact_matches"] = conn.execute(
            "SELECT COUNT(*) FROM comparisons "
            "WHERE created_at >= ? AND exact_match = 1",
            (cutoff_iso,),
        ).fetchone()[0]
        metrics["exact_match_rate"] = _safe_rate(
            metrics["exact_matches"], comparisons_total
        )

        # Response time — avg + median over non-null values (median needs the
        # raw list; SQLite has no median function).
        rt_values = [
            r[0]
            for r in conn.execute(
                "SELECT response_time_sec FROM comparisons "
                "WHERE created_at >= ? AND response_time_sec IS NOT NULL",
                (cutoff_iso,),
            )
        ]
        if rt_values:
            metrics["avg_response_time_sec"] = round(statistics.mean(rt_values), 1)
            metrics["median_response_time_sec"] = round(
                statistics.median(rt_values), 1
            )

        # has_activity = anything happened in the window across all 3 tables.
        metrics["has_activity"] = bool(
            drafts_total or metrics["replies_total"] or comparisons_total
        )
    finally:
        conn.close()

    return metrics


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def _fmt_date(iso_str):
    """'2026-06-26T...' -> '2026-06-26' for a tidy window header."""
    if not iso_str:
        return "?"
    return str(iso_str)[:10]


def _fmt_pct(rate):
    """A 0..1 rate as a friendly percentage string, e.g. '33.3%'."""
    return f"{rate * 100:.1f}%"


def _fmt_secs(seconds):
    """Seconds as e.g. '812s (13.5m)'; passthrough None -> 'n/a'."""
    if seconds is None:
        return "n/a"
    minutes = seconds / 60.0
    return f"{seconds:g}s ({minutes:.1f}m)"


def format_report(metrics):
    """Render a metrics dict (from compute_metrics) into a plain-text report.

    Owner-friendly, Telegram-safe (plain text, no Markdown/HTML), and kept well
    under the ~4096-char Telegram limit. Handles the empty-window case with a
    clear "no activity this week" line. Pure — never raises on a well-formed
    metrics dict; missing keys fall back to safe defaults.
    """
    m = metrics or {}
    days = m.get("window_days", 7)
    start = _fmt_date(m.get("window_start"))
    end = _fmt_date(m.get("window_end"))

    lines = []
    lines.append(f"Window: {start} -> {end} (last {days} days)")
    lines.append("")

    if not m.get("has_activity"):
        lines.append("No activity this week.")
        lines.append("")
        lines.append(
            "0 drafts, 0 captured replies, 0 comparisons in the window."
        )
        lines.append("(The agent ran but had nothing to report.)")
        return "\n".join(lines)

    by_status = m.get("drafts_by_status", {}) or {}
    drafts_total = m.get("drafts_total", 0)

    # --- Drafts ---------------------------------------------------------- #
    lines.append("DRAFTS")
    lines.append(f"  Total: {drafts_total}")
    lines.append(
        "  By status: "
        f"drafted={by_status.get('drafted', 0)}, "
        f"escalated={by_status.get('escalated', 0)}, "
        f"kb_gap={by_status.get('kb_gap', 0)}"
        + (f", other={by_status['other']}" if by_status.get("other") else "")
    )
    lines.append(
        f"  Posted to Gorgias: {m.get('drafts_posted', 0)} "
        f"| Dry-run only: {m.get('drafts_dry_run', 0)}"
    )
    lines.append(f"  KB gaps: {m.get('kb_gaps', 0)}")
    lines.append(
        f"  Escalation rate: {_fmt_pct(m.get('escalation_rate', 0.0))} "
        f"({m.get('escalations', 0)}/{drafts_total})"
    )

    top = m.get("top_priorities") or []
    if top:
        rendered = ", ".join(f"{prio}={count}" for prio, count in top)
        lines.append(f"  Top priorities: {rendered}")
    lines.append("")

    # --- Learning loop --------------------------------------------------- #
    lines.append("LEARNING LOOP")
    lines.append(f"  Human replies captured: {m.get('replies_total', 0)}")
    comparisons_total = m.get("comparisons_total", 0)
    lines.append(f"  Draft<->reply comparisons: {comparisons_total}")

    avg_sim = m.get("avg_similarity")
    if avg_sim is not None:
        lines.append(f"  Avg similarity: {avg_sim:.3f} (0=different, 1=identical)")
    else:
        lines.append("  Avg similarity: n/a (no scored comparisons)")

    lines.append(
        f"  Exact-match rate: {_fmt_pct(m.get('exact_match_rate', 0.0))} "
        f"({m.get('exact_matches', 0)}/{comparisons_total})"
    )

    if m.get("avg_response_time_sec") is not None:
        lines.append(
            f"  Response time avg: {_fmt_secs(m.get('avg_response_time_sec'))} "
            f"| median: {_fmt_secs(m.get('median_response_time_sec'))}"
        )

    text = "\n".join(lines)

    # Belt-and-braces: keep well under Telegram's hard 4096-char limit.
    if len(text) > 3900:
        text = text[:3890] + "\n...(truncated)"
    return text


# --------------------------------------------------------------------------- #
# CLI — never raises into a scheduled run.
# --------------------------------------------------------------------------- #
def _build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Weekly operational metrics from feedback.db for the Buttons Bebe "
            "AI support agent. Default action: compute + print only."
        )
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="reporting window in days (default 7, min 1)",
    )
    parser.add_argument(
        "--send", action="store_true",
        help="send the report to the owner via Telegram (live)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="compute + show what WOULD be sent, without sending",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print the raw metrics dict as JSON (no Telegram)",
    )
    parser.add_argument(
        "--path", default=None,
        help="path to feedback.db (defaults to FEEDBACK_DB_PATH / next to module)",
    )
    return parser


def main(argv=None):
    """CLI entry point. Returns a process exit code (0 ok, non-zero on error).

    Designed to NEVER crash a scheduled run: any unexpected error is logged and
    converted into a non-zero exit code, but we never leave a half-sent report.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _build_arg_parser().parse_args(argv)

    # 1) Compute metrics + render the report (read-only). Guarded.
    try:
        metrics = compute_metrics(days=args.days, path=args.path)
        report = format_report(metrics)
    except Exception as exc:  # noqa: BLE001 — a scheduled run must not crash
        logger.exception("weekly_review: failed to compute metrics: %s", exc)
        return 1

    if args.json:
        print(json.dumps(metrics, indent=2, default=str))
        return 0

    # 2) Decide what to do with the report.
    if args.send and not args.dry_run:
        # Live send via the Task-15 sender. It is itself resilient (never
        # raises), but we still guard + inspect its result.
        try:
            import telegram_notify
            result = telegram_notify.send_weekly_report(report, dry_run=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception("weekly_review: send failed: %s", exc)
            print(report)  # still surface the report so the run isn't a black hole
            return 1
        if result.get("ok"):
            logger.info("weekly_review: report sent to owner Telegram.")
            print(report)
            return 0
        logger.error("weekly_review: report NOT sent: %s", result.get("error"))
        print(report)
        return 1

    if args.dry_run:
        # Show exactly what WOULD be sent (payload), without sending.
        try:
            import telegram_notify
            result = telegram_notify.send_weekly_report(report, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("weekly_review: dry-run render failed: %s", exc)
            print(report)
            return 1
        print("=== WOULD SEND (dry-run, nothing sent) ===")
        print(result.get("text", report))
        return 0

    # Default: compute + PRINT only.
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
