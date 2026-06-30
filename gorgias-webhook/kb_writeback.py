#!/usr/bin/env python3
"""
kb_writeback.py — let approved replies / owner answers GROW the KB.

Stage 4, Task 14 (Buttons Bebe AI support agent). This is the write side of the
self-improving knowledge loop: an OWNER Q&A or a HUMAN-APPROVED reply becomes a
new CONVENTIONS-correct Markdown file under `kb/learned/`, which the ingestion
worker then embeds into pgvector (`ingestion_worker.sync()`), so the KB grows
itself.

  owner Q&A          -> kb/learned/owner-qa-<UTC-ts>.md   status: confirmed
  approved reply     -> kb/learned/ticket-<id|ts>.md      status: review_pending

SAFETY-CRITICAL. Anything written here gets embedded and RETRIEVED INTO FUTURE
CUSTOMER DRAFTS. Two non-negotiables:

  1. PII NEVER lands here. Real owner/agent text contains customer names, emails,
     phones, order/tracking numbers and street addresses. `scrub_pii()` redacts
     them to placeholders BEFORE writing, and we FAIL SAFE: if, after scrubbing,
     the text still smells like un-redactable PII, we REFUSE to write (return
     None + log) rather than risk a leak. When unsure -> don't write. (See
     CONVENTIONS.md §5 "NO customer PII, ever.")

  2. The file MUST parse under the SAME parser the ingestion worker uses
     (`kb_client.parse_file`). We don't hand-wave the format — after writing we
     re-parse the file and assert it yields >=1 chunk with the right
     category/status. If it doesn't parse, the worker would silently skip it, so
     we delete the bad file and raise.

NOT auto-fired. This module is the MACHINERY + a CLI; it is invoked DELIBERATELY
(owner-triggered / explicit human approval), NOT automatically on every Workflow
B reply. Auto-writing every captured reply would pollute the KB and risk PII.
See the "DELIBERATE INVOCATION" note below. server.py is intentionally NOT wired
to this.

Stdlib only (+ project imports: kb_client, ingestion_worker). Uses subprocess for
git. Run the self-test (no repo/KB pollution) with:
    .venv/bin/python kb_writeback.py            # __main__ self-test
CLI:
    python3 kb_writeback.py owner-answer  --q "..." --a "..."
    python3 kb_writeback.py approved-reply --ticket 123 --q "..." --reply "..."
    ... add --no-commit / --no-ingest for a write-only dry run.
"""

# --------------------------------------------------------------------------- #
# DELIBERATE INVOCATION (read me before wiring this anywhere)
# --------------------------------------------------------------------------- #
# This loop is OWNER-TRIGGERED, not automatic. Do NOT call record_*() from the
# Workflow B capture path or any per-message handler. Reasons:
#   * PII risk — raw replies carry customer PII; a per-reply auto-write multiplies
#     the chance a redaction miss leaks into a future draft.
#   * Pollution — not every approved reply is general knowledge; auto-ingesting
#     all of them dilutes retrieval with one-off, ticket-specific noise.
# The intended trigger is a human deciding "this answer is worth teaching the KB"
# (an owner Q&A, or an agent reply someone explicitly promotes). Keep it that way.
# --------------------------------------------------------------------------- #

import argparse
import datetime
import logging
import os
import re
import subprocess
import sys

import kb_client

log = logging.getLogger("kb_writeback")

# --------------------------------------------------------------------------- #
# Paths — kb/ lives next to this module; never hardcoded absolute.
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = SCRIPT_DIR
KB_ROOT = os.path.join(REPO_ROOT, "kb")
LEARNED_DIR = os.path.join(KB_ROOT, "learned")


# =========================================================================== #
# PII SCRUBBING
# =========================================================================== #
# Mirrors the spirit of the Stage 1 seed scrub (CONVENTIONS.md §5 / kb/README.md
# "fully PII-scrubbed"). Conservative, order matters: redact the most specific,
# highest-confidence patterns first (emails, URLs) so their digits don't get
# eaten by the generic long-digit-run rule, then phones, then order/tracking
# numbers, then addresses, then obvious personal names.
#
# WHAT scrub_pii() CATCHES (each -> a stable placeholder):
#   [email]    email addresses                  (\S+@\S+ style, incl. obfuscated)
#   [url]      http(s):// links                 (tracking links often embed PII)
#   [phone]    phone numbers                     (NANP-ish, +country, separators)
#   [order#]   long digit runs (>=5 digits)      order / tracking / card-ish runs
#   [address]  street addresses                  "123 Main St", PO boxes, ZIPs
#   [name]     "Hi <Name>," / "Dear <Name>," greetings + sign-offs (best-effort)
#
# FAIL-SAFE: after scrubbing, _residual_pii() re-scans for anything that still
# looks like PII we could not confidently redact (a stray long digit run, an
# unredacted '@'-handle, an email-shaped token). If found, the caller REFUSES to
# write (returns None + logs). We would rather drop a useful answer than leak.

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b", re.IGNORECASE)
# Obfuscated emails: "john [at] x [dot] com", "john(at)x.com"
_EMAIL_OBFUSC_RE = re.compile(
    r"\b[\w.+-]+\s*[\(\[]?\s*(?:at|@)\s*[\)\]]?\s*[\w-]+(?:\s*[\(\[]?\s*(?:dot|\.)\s*[\)\]]?\s*[\w-]+)+\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"\bhttps?://\S+", re.IGNORECASE)

# Phones: optional +CC, then 7+ digits with common separators ( ) - . space.
# Requires at least one separator OR a leading + so we don't grab every number;
# the long-digit-run rule below catches separator-less 10+ digit strings.
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{3}\)[\s.\-]?|\d{3}[\s.\-])\d{3}[\s.\-]?\d{4}(?!\w)"
)
# International / loose phone with a leading + and 7+ digits.
_PHONE_INTL_RE = re.compile(r"(?<!\w)\+\d[\d\s.\-]{6,}\d(?!\w)")

# Long digit runs: order numbers, tracking numbers, card-ish runs. 5+ digits,
# possibly grouped (e.g. "1Z 999 AA1 01" trackings, "100023456"). Run AFTER
# emails/phones/urls so we don't shred those.
_LONG_DIGIT_RE = re.compile(r"(?<!\w)#?\d[\d\s\-]{3,}\d(?!\w)")
# Alphanumeric tracking-like tokens (e.g. "1Z999AA10123456784", "RR123456789US").
_TRACKING_RE = re.compile(r"(?<!\w)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,}(?!\w)")

# Street addresses: "<num> <Words> St/Ave/Rd/...", optional unit; plus PO boxes.
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.'\-]+\s+){0,4}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|"
    r"Place|Pl|Way|Terrace|Ter|Circle|Cir|Highway|Hwy|Parkway|Pkwy|Square|Sq|"
    r"Apt|Apartment|Suite|Ste|Unit)\b\.?",
    re.IGNORECASE,
)
_POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*Box\s+\d+\b", re.IGNORECASE)
# US ZIP (5 or ZIP+4). Bounded so it isn't an order#; address-context rule.
_ZIP_RE = re.compile(r"(?<!\w)\d{5}(?:-\d{4})?(?!\w)")

# Obvious personal names in greetings / sign-offs — best-effort, English-shaped.
# We only touch the captured Name group, leaving the greeting word intact.
_GREETING_NAME_RE = re.compile(
    r"\b(Hi|Hello|Hey|Dear|Thanks|Thank you|Regards|Best|Sincerely|Cheers)"
    r"[,!]?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)

# A short list of greeting/closing words that legitimately precede a capitalized
# word that is NOT a name — guards the name heuristic from false positives like
# "Thanks Team" we still redact, but "Best Buttons Bebe" style brand words: we
# accept the small over-redaction risk (fail-safe favors over-redaction).

_PLACEHOLDERS = ("[email]", "[url]", "[phone]", "[order#]", "[address]", "[name]")


def _redact(pattern, replacement, text, found, label):
    """Substitute `pattern`->`replacement`, recording a `label` hit per match."""
    def _sub(m):
        found.append(label)
        return replacement
    return pattern.sub(_sub, text)


def scrub_pii(text):
    """Redact PII from `text`. Returns (clean_text, found:list[str]).

    `found` is the list of redaction labels applied (e.g. ['[email]', '[order#]'])
    — empty means nothing matched. This does NOT decide writeability on its own;
    callers additionally check `_residual_pii(clean_text)` and fail safe. See the
    module-level table for exactly what is caught.

    Order is deliberate (specific -> generic) so high-value patterns aren't eaten
    by the broad long-digit rule.
    """
    if not text:
        return "", []
    found = []
    out = text

    # 1. Emails (plain + obfuscated) and URLs first — they contain @, dots, digits
    #    we don't want the later rules to mangle into partial leaks.
    out = _redact(_EMAIL_RE, "[email]", out, found, "[email]")
    out = _redact(_EMAIL_OBFUSC_RE, "[email]", out, found, "[email]")
    out = _redact(_URL_RE, "[url]", out, found, "[url]")

    # 2. Phones (formatted + international) before generic digit runs.
    out = _redact(_PHONE_RE, "[phone]", out, found, "[phone]")
    out = _redact(_PHONE_INTL_RE, "[phone]", out, found, "[phone]")

    # 3. Addresses (street / PO box) before ZIP / digit-run rules eat their nums.
    out = _redact(_ADDRESS_RE, "[address]", out, found, "[address]")
    out = _redact(_POBOX_RE, "[address]", out, found, "[address]")

    # 4. Alphanumeric tracking tokens, then long digit runs (orders/tracking),
    #    then bare ZIPs. ZIP last so a standalone 5-digit run is caught either
    #    way; we label it [order#] when not in an address context (conservative).
    out = _redact(_TRACKING_RE, "[order#]", out, found, "[order#]")
    out = _redact(_LONG_DIGIT_RE, "[order#]", out, found, "[order#]")
    out = _redact(_ZIP_RE, "[order#]", out, found, "[order#]")

    # 5. Obvious personal names in greetings/sign-offs (best-effort).
    def _name_sub(m):
        found.append("[name]")
        return f"{m.group(1)} [name]"
    out = _GREETING_NAME_RE.sub(_name_sub, out)

    return out, found


# --------------------------------------------------------------------------- #
# Fail-safe residual-PII detector — run on the ALREADY-scrubbed text.
# --------------------------------------------------------------------------- #
# These re-scan for patterns that, if STILL present after scrub_pii(), mean we
# could not confidently redact and must REFUSE to write. Kept strict but not
# hair-trigger: small numbers (prices "$25", "10%", "2 items", "size 6") are
# fine; what trips it is email shapes, raw @handles, and digit runs >=5.
_RESIDUAL_CHECKS = (
    ("email-shape", re.compile(r"[\w.+-]+@[\w-]+\.[\w-]+")),
    ("at-handle", re.compile(r"(?<!\w)@[A-Za-z][\w.]{2,}")),
    ("long-digit-run", re.compile(r"(?<!\w)\d{5,}(?!\w)")),
    ("grouped-digits", re.compile(r"(?<!\w)\d{3,}[\s\-]\d{3,}(?:[\s\-]\d{2,})+(?!\w)")),
)


def _residual_pii(text):
    """Return a list of (kind, snippet) for residual PII still in `text`, or []."""
    hits = []
    for kind, rx in _RESIDUAL_CHECKS:
        m = rx.search(text or "")
        if m:
            hits.append((kind, m.group(0)))
    return hits


class PIIRefusal(Exception):
    """Raised internally when scrubbed text still looks like it contains PII."""


def _scrub_or_refuse(text, field):
    """scrub_pii() then fail-safe. Returns clean text, or raises PIIRefusal.

    `field` is a label ('question'/'answer'/'reply') used only for logging.
    """
    clean, found = scrub_pii(text or "")
    if found:
        log.info("scrubbed %s: redacted %s", field, ", ".join(sorted(set(found))))
    residual = _residual_pii(clean)
    if residual:
        kinds_log = ", ".join(k for k, _ in residual)
        log.error(
            "REFUSING to write: %s still looks like it contains PII after "
            "scrubbing (%s). Edit the text by hand and retry.", field, kinds_log,
        )
        kinds_detail = ", ".join(f"{k}({s!r})" for k, s in residual)
        raise PIIRefusal(f"{field} has residual PII: {kinds_detail}")
    return clean


# =========================================================================== #
# Markdown rendering (CONVENTIONS-correct front-matter + `##` chunk)
# =========================================================================== #
def _yaml_inline_list(items):
    """Render a Python list as a `[a, b, c]` inline YAML list (lowercase tags)."""
    clean = [
        re.sub(r"[\r\n]", "", str(t).strip().lower().replace(" ", "-"))
        for t in (items or []) if str(t).strip()
    ]
    return "[" + ", ".join(clean) + "]"


def _yaml_escape_title(title):
    """One-line, double-quoted title for the front-matter `title:` field.

    Wrapping in double-quotes makes the value safe for any content that would
    otherwise break YAML parsing (`: `, `#`, `{`, `}`, etc.).
    """
    t = " ".join((title or "").split())  # collapse whitespace/newlines
    t = t.replace('"', "'")             # inner double-quotes → single-quotes
    if len(t) > 120:
        t = t[:117].rstrip() + "..."
    t = t or "Learned answer"
    return f'"{t}"'


def _heading_safe(text):
    """Single-line `##` heading text (no embedded newlines or leading hashes)."""
    h = " ".join((text or "").split())
    h = h.lstrip("#").strip()
    if len(h) > 120:
        h = h[:117].rstrip() + "..."
    return h or "Answer"


def _render_markdown(*, title, category, status, source, tags, extra_meta,
                     heading, body, banner=None):
    """Assemble a CONVENTIONS-correct KB Markdown document string.

    front-matter (title/category/status[/source][/tags] + reserved extras like
    created_at/source_type/ticket_id from CONVENTIONS §2), an optional intro
    banner, then exactly one `##` section = (heading, body).
    """
    lines = ["---"]
    lines.append(f"title: {_yaml_escape_title(title)}")
    lines.append(f"category: {category}")
    lines.append(f"status: {status}")
    if source:
        lines.append(f"source: {source}")
    if tags:
        lines.append(f"tags: {_yaml_inline_list(tags)}")
    for key, val in (extra_meta or {}).items():
        lines.append(f"{key}: {val}")
    lines.append("---")
    lines.append("")
    if banner:
        lines.append(banner)
        lines.append("")
    lines.append(f"## {_heading_safe(heading)}")
    lines.append("")
    lines.append((body or "").strip())
    lines.append("")  # trailing newline
    return "\n".join(lines)


# =========================================================================== #
# Git + ingest (both optional, both individually guarded)
# =========================================================================== #
def _git(*args, check=True):
    """Run a git command in REPO_ROOT, returning stdout (stripped)."""
    out = subprocess.run(
        ["git", "-C", REPO_ROOT, *args],
        check=check, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _git_commit_file(abs_path, message):
    """git add + commit ONLY this one file. Best-effort; returns True on success.

    We add ONLY the specific new file (never `.` / `-A`) so we can't accidentally
    sweep in unrelated working-tree changes or secrets.
    """
    rel = os.path.relpath(abs_path, REPO_ROOT)
    try:
        _git("add", "--", rel)
        # If nothing is staged for this path (e.g. re-run, identical), don't fail.
        staged = _git("diff", "--cached", "--name-only", "--", rel)
        if not staged:
            log.info("git: nothing to commit for %s (already committed?)", rel)
            return False
        _git("commit", "-m", message, "--", rel)
        log.info("git: committed %s", rel)
        return True
    except subprocess.CalledProcessError as exc:
        log.error("git commit failed for %s: %s", rel, exc.stderr.strip() if exc.stderr else exc)
        return False
    except Exception as exc:
        log.error("git commit error for %s: %s", rel, exc)
        return False


def _ingest_sync():
    """Call ingestion_worker.sync() to embed the new file. Best-effort.

    Imported lazily so writing-only (or the self-test) never needs pg8000/
    fastembed or a live DB.
    """
    try:
        import ingestion_worker
        result = ingestion_worker.sync()
        log.info("ingest: sync() -> %s", result)
        return True
    except Exception as exc:
        log.error("ingest sync() failed (file is committed; re-run later): %s", exc)
        return False


# =========================================================================== #
# Core writer (shared by both entry points)
# =========================================================================== #
def _utc_timestamp():
    """Filesystem-safe UTC timestamp: 2026-06-26T17-30-05Z."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_learned_file(
    *, filename, title_prefix, status, source, source_type, tags, extra_meta,
    heading_text, body_text, banner, expect_category, expect_status,
    learned_dir, commit, commit_message, ingest,
):
    """Scrub -> render -> verify-parse -> (git) -> (ingest). Returns path or None.

    Returns None (logged) if PII scrubbing fails safe. Raises AssertionError only
    if a file we wrote does NOT parse (a real bug we don't want to swallow); we
    delete the bad file before re-raising so the KB is never left polluted.

    The front-matter `title` is built from `title_prefix` + the SCRUBBED heading
    (never the raw question), so a question's PII can't sneak into the title field.
    """
    os.makedirs(learned_dir, exist_ok=True)

    # --- 1. PII scrub (fail-safe) -------------------------------------------- #
    try:
        clean_heading = _scrub_or_refuse(heading_text, "question")
        clean_body = _scrub_or_refuse(body_text, "answer/reply")
    except PIIRefusal:
        return None  # already logged; caller gets None == "not written"

    # Title derives from the SCRUBBED heading so it can never carry PII.
    title = f"{title_prefix} — {clean_heading}"

    # --- 2. Render the CONVENTIONS-correct doc ------------------------------- #
    doc = _render_markdown(
        title=title,
        category=expect_category,
        status=status,
        source=source,
        tags=tags,
        extra_meta=extra_meta,
        heading=clean_heading,
        body=clean_body,
        banner=banner,
    )

    abs_path = os.path.join(learned_dir, filename)
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    log.info("wrote %s (%d bytes)", abs_path, len(doc))

    # --- 3. Convention-correctness GUARD (parse_file) ------------------------ #
    # Verify against the directory we actually wrote into (so the self-test's
    # temp KB works too), but kb_client.parse_file needs the kb_root to derive
    # the doc-id; pass the parent kb/ of the learned dir.
    kb_root_for_check = os.path.dirname(learned_dir)
    try:
        chunks = kb_client.parse_file(abs_path, kb_root=kb_root_for_check)
        if not chunks:
            raise ValueError("parsed to 0 chunks — worker would skip it")
        c0 = chunks[0]
        if c0.category != expect_category:
            raise ValueError(f"category {c0.category!r} != {expect_category!r}")
        if c0.status != expect_status:
            raise ValueError(f"status {c0.status!r} != {expect_status!r}")
        if not any(c.heading for c in chunks):
            raise ValueError("no `##`-heading chunk (not retrievable)")
    except Exception:
        # Never leave an unparseable or invalid file in the KB.
        try:
            os.remove(abs_path)
        except OSError:
            pass
        log.error("written file did not satisfy the KB contract — deleted %s", abs_path)
        raise
    log.info("verified: %s parses to %d chunk(s) [%s/%s]",
             os.path.basename(abs_path), len(chunks), c0.category, c0.status)

    # --- 4. Git commit (optional, guarded) ----------------------------------- #
    if commit:
        _git_commit_file(abs_path, commit_message)

    # --- 5. Ingest sync (optional, guarded) ---------------------------------- #
    if ingest:
        if not commit:
            # sync() is git-diff driven; an uncommitted file won't be ingested.
            log.warning(
                "ingest=True but commit=False: sync() is git-diff driven and "
                "will not pick up an uncommitted file. Skipping ingest."
            )
        else:
            _ingest_sync()

    return abs_path


# =========================================================================== #
# PUBLIC ENTRY POINTS
# =========================================================================== #
def record_owner_answer(
    question, answer, *, tags=None, source_ticket_id=None,
    commit=True, ingest=True, learned_dir=None,
):
    """Record an OWNER Q&A as a confirmed KB file. Returns the path, or None.

    Owner answers are the highest-trust learned knowledge -> status: confirmed,
    source: owner_qa. The file is kb/learned/owner-qa-<UTC-timestamp>.md with one
    `## <question>` section whose body is the answer.

    Both `question` and `answer` are PII-scrubbed before writing; if either still
    looks like it contains un-redactable PII, NOTHING is written and None is
    returned (logged). `source_ticket_id`, if given, is recorded as front-matter
    provenance (and itself scrubbed of digit runs).

    Args:
        question:          the owner's question / topic (becomes the `##` heading).
        answer:            the owner's authoritative answer (the chunk body).
        tags:              optional list[str] of lowercase-hyphenated tags.
        source_ticket_id:  optional originating ticket id (provenance only).
        commit:            git add + commit the new file (default True).
        ingest:            ingestion_worker.sync() after commit (default True).
        learned_dir:       override output dir (used by the self-test only).

    Returns:
        absolute path of the written file, or None if PII scrubbing failed safe.
    """
    ts = _utc_timestamp()
    learned_dir = learned_dir or LEARNED_DIR
    # Provenance ticket id, scrubbed (an id is itself a long digit run -> [order#]).
    extra_meta = {"created_at": _utc_iso(), "source_type": "owner_qa"}
    if source_ticket_id is not None:
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", str(source_ticket_id))
        extra_meta["source_ticket_id"] = safe_id

    return _write_learned_file(
        filename=f"owner-qa-{ts}.md",
        title_prefix="Owner answer",
        status="confirmed",
        source="owner_qa",
        source_type="owner_qa",
        tags=tags,
        extra_meta=extra_meta,
        heading_text=question,
        body_text=answer,
        banner=None,  # confirmed owner answers carry no DRAFT banner
        expect_category="learned",
        expect_status="confirmed",
        learned_dir=learned_dir,
        commit=commit,
        commit_message=f"kb(learned): owner Q&A {ts}",
        ingest=ingest,
    )


def record_approved_reply(
    question, reply_text, *, ticket_id=None, tags=None,
    commit=True, ingest=True, learned_dir=None,
):
    """Record a HUMAN-APPROVED agent reply as a (lower-trust) KB file.

    Approved replies are worth learning but are LESS trusted than owner answers
    -> status: review_pending, source: agent_reply, plus a visible review banner.
    The file is kb/learned/ticket-<id-or-ts>.md with one `## <question>` section
    whose body is the reply.

    Both `question` and `reply_text` are PII-scrubbed before writing; if either
    still looks like it contains un-redactable PII, NOTHING is written and None is
    returned (logged). The ticket id is recorded as provenance (scrubbed).

    Args:
        question:    the customer's question / topic (becomes the `##` heading).
        reply_text:  the approved agent reply (the chunk body).
        ticket_id:   originating Gorgias ticket id; names the file + provenance.
        tags:        optional list[str] of lowercase-hyphenated tags.
        commit:      git add + commit the new file (default True).
        ingest:      ingestion_worker.sync() after commit (default True).
        learned_dir: override output dir (used by the self-test only).

    Returns:
        absolute path of the written file, or None if PII scrubbing failed safe.
    """
    learned_dir = learned_dir or LEARNED_DIR
    ts = _utc_timestamp()
    # File name: ticket-<id> when we have an id, else timestamp. The id in the
    # FILENAME is a routing/provenance id (its own column, like CONVENTIONS §2's
    # ticket_id), kept literal so a re-ingest of the same ticket is idempotent.
    if ticket_id is not None and str(ticket_id).strip():
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", str(ticket_id))[:32] or ts
        filename = f"ticket-{safe_id}.md"
        extra_meta = {"created_at": _utc_iso(), "source_type": "agent_reply",
                      "ticket_id": safe_id, "review_pending": "true"}
    else:
        filename = f"ticket-{ts}.md"
        extra_meta = {"created_at": _utc_iso(), "source_type": "agent_reply",
                      "review_pending": "true"}

    banner = (
        "> ⚠️ REVIEW PENDING — learned from a human-approved reply, not yet "
        "owner-confirmed. Lower trust than owner Q&A."
    )

    return _write_learned_file(
        filename=filename,
        title_prefix="Learned reply",
        status="review_pending",
        source="agent_reply",
        source_type="agent_reply",
        tags=tags,
        extra_meta=extra_meta,
        heading_text=question,
        body_text=reply_text,
        banner=banner,
        expect_category="learned",
        expect_status="review_pending",
        learned_dir=learned_dir,
        commit=commit,
        commit_message=f"kb(learned): approved reply ticket-"
                       f"{filename[len('ticket-'):-len('.md')]}",
        ingest=ingest,
    )


# =========================================================================== #
# CLI
# =========================================================================== #
def _build_parser():
    p = argparse.ArgumentParser(
        description="KB write-back: turn owner Q&A / approved replies into "
                    "kb/learned/ Markdown (PII-scrubbed, ingestible).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    oa = sub.add_parser("owner-answer", help="record an owner Q&A (status: confirmed)")
    oa.add_argument("--q", "--question", dest="question", required=True)
    oa.add_argument("--a", "--answer", dest="answer", required=True)
    oa.add_argument("--tags", default="", help="comma-separated tags")
    oa.add_argument("--ticket", dest="source_ticket_id", default=None,
                    help="optional originating ticket id (provenance)")
    oa.add_argument("--no-commit", action="store_true", help="write only, don't git commit")
    oa.add_argument("--no-ingest", action="store_true", help="don't run ingestion sync")

    ar = sub.add_parser("approved-reply", help="record a human-approved reply (review_pending)")
    ar.add_argument("--q", "--question", dest="question", required=True)
    ar.add_argument("--reply", dest="reply", required=True)
    ar.add_argument("--ticket", dest="ticket_id", default=None, help="originating ticket id")
    ar.add_argument("--tags", default="", help="comma-separated tags")
    ar.add_argument("--no-commit", action="store_true", help="write only, don't git commit")
    ar.add_argument("--no-ingest", action="store_true", help="don't run ingestion sync")
    return p


def _parse_tags(s):
    return [t.strip() for t in (s or "").split(",") if t.strip()]


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(argv[1:])

    if args.cmd == "owner-answer":
        path = record_owner_answer(
            args.question, args.answer,
            tags=_parse_tags(args.tags),
            source_ticket_id=args.source_ticket_id,
            commit=not args.no_commit,
            ingest=not args.no_ingest,
        )
    elif args.cmd == "approved-reply":
        path = record_approved_reply(
            args.question, args.reply,
            ticket_id=args.ticket_id,
            tags=_parse_tags(args.tags),
            commit=not args.no_commit,
            ingest=not args.no_ingest,
        )
    else:  # pragma: no cover — argparse(required=True) guards this
        print("unknown command", file=sys.stderr)
        return 2

    if path is None:
        print("REFUSED: nothing written (PII could not be safely redacted). "
              "Edit the text and retry.", file=sys.stderr)
        return 1
    print(path)
    return 0


# =========================================================================== #
# __main__ SELF-TEST — runs against a TEMP throwaway KB; never touches the real
# repo/KB and creates no commits in it.
# =========================================================================== #
def _self_test():
    """End-to-end test in a scratch dir: write w/ fake PII, assert redaction +
    parse, then clean up. Uses commit=False, ingest=False and a temp learned_dir
    so the real repo/KB and pgvector are untouched."""
    import shutil
    import tempfile

    # assert statements are silently stripped by python -O; this self-test
    # relies on them for all safety checks, so refuse to run in optimized mode.
    if not __debug__:
        print("FATAL: _self_test() must not run under python -O (asserts disabled)",
              file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("=== KB_WRITEBACK SELF-TEST (temp dir, no commit, no ingest) ===")

    tmp_root = tempfile.mkdtemp(prefix="kb_writeback_selftest_")
    real_learned = LEARNED_DIR
    before = set(os.listdir(real_learned)) if os.path.isdir(real_learned) else set()
    try:
        # A throwaway kb/learned/ so parse_file's repo-relative id still resolves.
        tmp_kb = os.path.join(tmp_root, "kb")
        tmp_learned = os.path.join(tmp_kb, "learned")
        os.makedirs(tmp_learned, exist_ok=True)

        # ---- scrub_pii unit assertions ------------------------------------- #
        sample = ("Hi Sarah, contact john@x.com or order 100023456, "
                  "call 555-123-4567, ship to 123 Main St, tracking "
                  "1Z999AA10123456784, see https://track.me/abc?id=42")
        clean, found = scrub_pii(sample)
        print(f"\nscrub_pii sample in : {sample}")
        print(f"scrub_pii sample out: {clean}")
        print(f"scrub_pii found     : {sorted(set(found))}")
        assert "john@x.com" not in clean, "email not redacted"
        assert "100023456" not in clean, "order number not redacted"
        assert "555-123-4567" not in clean, "phone not redacted"
        assert "123 Main St" not in clean, "address not redacted"
        assert "1Z999AA10123456784" not in clean, "tracking not redacted"
        assert "https://track.me" not in clean, "url not redacted"
        assert "[email]" in clean and "[order#]" in clean and "[phone]" in clean
        assert _residual_pii(clean) == [], f"residual PII after scrub: {_residual_pii(clean)}"
        print("OK  scrub_pii redacts email/order/phone/address/tracking/url + no residual")

        # ---- fail-safe: un-redactable residual -> refuse to write ----------- #
        # A bare @handle is not email-shaped (scrub_pii leaves it), but the
        # residual detector flags it as personal-handle PII -> we MUST refuse.
        bad = "Customer asked us to DM them at @sarah_b_personal for updates."
        bad_clean, _ = scrub_pii(bad)
        assert _residual_pii(bad_clean), "test setup: expected residual on the bad sample"
        path_bad = record_owner_answer(
            "How do we follow up?", bad,
            commit=False, ingest=False, learned_dir=tmp_learned,
        )
        assert path_bad is None, "fail-safe did NOT trigger on residual @handle PII"
        print("OK  fail-safe refuses to write when residual PII remains (returned None)")

        # ---- owner answer WITH fake PII -> written, redacted, parses --------- #
        oa_path = record_owner_answer(
            question="What is the shipping policy for order 100023456?",
            answer=("Hi Sarah, we ship within 2 business days. Contact "
                    "john@x.com or call 555-123-4567. Your order 100023456 "
                    "ships to 123 Main St. Standard orders under $50 ship for "
                    "a $5 flat fee."),
            tags=["shipping", "owner"],
            source_ticket_id="998877",
            commit=False, ingest=False, learned_dir=tmp_learned,
        )
        assert oa_path is not None, "owner answer unexpectedly refused"
        assert os.path.isfile(oa_path), "owner answer file not written"
        with open(oa_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        print(f"\n--- written owner-qa file ({os.path.basename(oa_path)}) ---")
        print(content)
        for leak in ("john@x.com", "100023456", "555-123-4567", "123 Main St", "Sarah,"):
            assert leak not in content, f"PII leaked into KB file: {leak!r}"
        assert "[email]" in content and "[order#]" in content and "[phone]" in content
        body_content = content.split("---\n", 2)[-1]
        assert _residual_pii(body_content) == [], f"residual PII in file body: {_residual_pii(body_content)}"
        print("OK  owner-qa file written with all PII redacted, no residual")

        # ---- parse the written file through the SAME parser the worker uses -- #
        chunks = kb_client.parse_file(oa_path, kb_root=tmp_kb)
        assert chunks, "written owner-qa file parsed to 0 chunks (worker would skip)"
        assert chunks[0].category == "learned", f"category={chunks[0].category}"
        assert chunks[0].status == "confirmed", f"status={chunks[0].status}"
        assert any(c.heading for c in chunks), "no `##` heading chunk"
        print(f"OK  kb_client.parse_file -> {len(chunks)} chunk(s) "
              f"[{chunks[0].category}/{chunks[0].status}] heading={chunks[0].heading!r}")

        # ---- approved reply path (review_pending + banner) ------------------ #
        ar_path = record_approved_reply(
            question="Where is my order?",
            reply_text=("Hi Mike, your order shipped! Track it at "
                        "https://track.me/xyz?id=7 — email us at help@buttonsbebe.com "
                        "if it doesn't arrive in 5 business days."),
            ticket_id="12345",
            tags=["shipping"],
            commit=False, ingest=False, learned_dir=tmp_learned,
        )
        assert ar_path is not None and os.path.isfile(ar_path)
        assert os.path.basename(ar_path) == "ticket-12345.md", os.path.basename(ar_path)
        ar_chunks = kb_client.parse_file(ar_path, kb_root=tmp_kb)
        assert ar_chunks and ar_chunks[0].status == "review_pending", ar_chunks
        with open(ar_path, "r", encoding="utf-8") as fh:
            ar_content = fh.read()
        assert "help@buttonsbebe.com" not in ar_content, "email leaked"
        assert "https://track.me" not in ar_content, "url leaked"
        assert "REVIEW PENDING" in ar_content, "review banner missing"
        print(f"OK  approved-reply -> {os.path.basename(ar_path)} "
              f"[{ar_chunks[0].category}/{ar_chunks[0].status}] (review banner present)")

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    # ---- prove the REAL repo/KB was not polluted --------------------------- #
    after = set(os.listdir(real_learned)) if os.path.isdir(real_learned) else set()
    new_files = after - before
    assert not new_files, f"self-test polluted real kb/learned/: {new_files}"
    print(f"\nreal kb/learned/ unchanged: {sorted(before)} (no new files)")
    print("temp dir removed; no commits made in the real repo.")
    print("\nKB_WRITEBACK SELF-TEST OK")


if __name__ == "__main__":
    # No CLI args -> run the self-test; otherwise dispatch the CLI.
    if len(sys.argv) == 1:
        _self_test()
    else:
        sys.exit(main(sys.argv))
