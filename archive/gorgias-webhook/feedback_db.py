#!/usr/bin/env python3
"""
feedback_db.py — Single access point to feedback.db for the Hermes agent.

feedback.db is the OPERATIONAL store for the Buttons Bebe AI support agent.
It holds ONLY agent-vs-AI performance data across three tables:

  * drafts       — every draft the AI produces       (Workflow A)
  * replies      — every real human-agent reply       (Workflow B)
  * comparisons  — draft <-> reply difflib similarity  (Workflow B)

It is NOT the knowledge base. There is no kb_entries table — knowledge
storage and retrieval live in Supermemory (fed from a Git repo of Markdown).
See PHASE1_KB_ARCHITECTURE.md and SYSTEM_WORKFLOW.md for the canonical spec.

Stdlib only (sqlite3, json, datetime, os). No external dependencies.

SAFETY:
  * Every value reaches SQL through a ? placeholder. SQL strings are static;
    user/runtime data is NEVER formatted into the SQL text.
  * init_db() applies feedback_schema.sql idempotently (CREATE ... IF NOT EXISTS).

Usage as a module:
    import feedback_db
    feedback_db.init_db()
    draft_id = feedback_db.record_draft(ticket_id=123, customer_message="...",
                                        draft_text="...", priority="high")

Run directly for a self-test (uses a throwaway db, never the real one):
    python3 feedback_db.py
"""

import datetime
import json
import os
import sqlite3

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Default db lives next to this module. Overridable via arg or FEEDBACK_DB_PATH.
DB_PATH = os.environ.get("FEEDBACK_DB_PATH", os.path.join(SCRIPT_DIR, "feedback.db"))

SCHEMA_PATH = os.path.join(SCRIPT_DIR, "feedback_schema.sql")


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #
def utc_now_iso():
    """Current UTC time as an ISO8601 string, e.g. '2026-06-26T15:10:00+00:00'."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Connection / init
# --------------------------------------------------------------------------- #
def get_conn(path=DB_PATH):
    """Open a connection with Row access and foreign keys enforced."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path=DB_PATH):
    """Apply feedback_schema.sql to `path`. Idempotent — safe to run repeatedly.

    Returns the path of the database that was initialized.
    """
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        schema_sql = fh.read()
    conn = get_conn(path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()
    return path


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _as_json(value):
    """Serialize lists/dicts to a JSON string; pass through None and str."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Inserts — every value goes through a ? placeholder (never str-formatted).
# --------------------------------------------------------------------------- #
def record_draft(
    ticket_id,
    customer_message,
    draft_text,
    priority,
    classification_reason=None,
    kb_sources=None,
    kb_gap=0,
    kb_gap_question=None,
    kb_gap_answer=None,
    customer_email=None,
    order_context=None,
    conversation_snippet=None,
    model_used=None,
    confidence=None,
    dry_run=1,
    posted_note_id=None,
    status="drafted",
    matched_reply_id=None,
    created_at=None,
    path=DB_PATH,
    conn=None,
):
    """Insert one AI draft (Workflow A, step A10). Returns the new draft id.

    kb_sources and order_context may be passed as Python lists/dicts; they are
    JSON-encoded for storage.
    """
    created_at = created_at or utc_now_iso()
    sql = (
        "INSERT INTO drafts ("
        "ticket_id, customer_message, draft_text, priority, classification_reason, "
        "kb_sources, kb_gap, kb_gap_question, kb_gap_answer, customer_email, "
        "order_context, conversation_snippet, model_used, confidence, dry_run, "
        "posted_note_id, status, matched_reply_id, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    params = (
        ticket_id,
        customer_message,
        draft_text,
        priority,
        classification_reason,
        _as_json(kb_sources),
        int(kb_gap) if kb_gap is not None else 0,
        kb_gap_question,
        kb_gap_answer,
        customer_email,
        _as_json(order_context),
        conversation_snippet,
        model_used,
        confidence,
        int(dry_run),
        posted_note_id,
        status,
        matched_reply_id,
        created_at,
    )
    return _insert(sql, params, path=path, conn=conn)


def record_reply(
    ticket_id,
    reply_text,
    message_id=None,
    agent_user_id=None,
    sender_email=None,
    channel=None,
    created_at=None,
    path=DB_PATH,
    conn=None,
):
    """Insert one captured human-agent reply (Workflow B, step B2).

    Returns the new reply id. Note: dedup on message_id is the caller's job
    (Workflow B does a SELECT first); this function just inserts.
    """
    created_at = created_at or utc_now_iso()
    sql = (
        "INSERT INTO replies ("
        "ticket_id, message_id, reply_text, agent_user_id, sender_email, "
        "channel, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    params = (
        ticket_id,
        message_id,
        reply_text,
        agent_user_id,
        sender_email,
        channel,
        created_at,
    )
    return _insert(sql, params, path=path, conn=conn)


def record_comparison(
    ticket_id,
    draft_id,
    reply_id,
    similarity_score=None,
    exact_match=0,
    edit_ops=None,
    response_time_sec=None,
    notes=None,
    created_at=None,
    path=DB_PATH,
    conn=None,
):
    """Insert a draft<->reply comparison (Workflow B, step B4).

    Returns the new comparison id. edit_ops may be a dict; it is JSON-encoded.
    """
    created_at = created_at or utc_now_iso()
    sql = (
        "INSERT INTO comparisons ("
        "ticket_id, draft_id, reply_id, similarity_score, exact_match, "
        "edit_ops, response_time_sec, notes, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    params = (
        ticket_id,
        draft_id,
        reply_id,
        similarity_score,
        int(exact_match) if exact_match is not None else 0,
        _as_json(edit_ops),
        response_time_sec,
        notes,
        created_at,
    )
    return _insert(sql, params, path=path, conn=conn)


def _insert(sql, params, path=DB_PATH, conn=None):
    """Run a parameterized INSERT and return lastrowid.

    If `conn` is given, use it (caller owns the transaction). Otherwise open a
    short-lived connection, commit, and close.
    """
    if conn is not None:
        cur = conn.execute(sql, params)
        return cur.lastrowid
    own = get_conn(path)
    try:
        cur = own.execute(sql, params)
        own.commit()
        return cur.lastrowid
    finally:
        own.close()


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def get_draft(draft_id, path=DB_PATH, conn=None):
    """Return the draft row (sqlite3.Row) for `draft_id`, or None."""
    return _query_one("SELECT * FROM drafts WHERE id = ?", (draft_id,), path, conn)


def get_reply(reply_id, path=DB_PATH, conn=None):
    """Return the reply row (sqlite3.Row) for `reply_id`, or None."""
    return _query_one("SELECT * FROM replies WHERE id = ?", (reply_id,), path, conn)


def get_comparison(comparison_id, path=DB_PATH, conn=None):
    """Return the comparison row (sqlite3.Row) for `comparison_id`, or None."""
    return _query_one(
        "SELECT * FROM comparisons WHERE id = ?", (comparison_id,), path, conn
    )


def recent_drafts(limit=20, path=DB_PATH, conn=None):
    """Return up to `limit` most-recent drafts (newest first) as a list of Rows."""
    return _query_all(
        "SELECT * FROM drafts ORDER BY id DESC LIMIT ?",
        (int(limit) if limit is not None else 20,), path, conn
    )


def drafts_for_ticket(ticket_id, path=DB_PATH, conn=None):
    """Return all drafts for a ticket, newest first."""
    return _query_all(
        "SELECT * FROM drafts WHERE ticket_id = ? ORDER BY id DESC",
        (ticket_id,),
        path,
        conn,
    )


def reply_exists(message_id, path=DB_PATH, conn=None):
    """True if a reply with this Gorgias message_id is already stored (dedup)."""
    if message_id is None:
        return False
    row = _query_one(
        "SELECT id FROM replies WHERE message_id = ?", (message_id,), path, conn
    )
    return row is not None


def _query_one(sql, params, path=DB_PATH, conn=None):
    if conn is not None:
        return conn.execute(sql, params).fetchone()
    own = get_conn(path)
    try:
        return own.execute(sql, params).fetchone()
    finally:
        own.close()


def _query_all(sql, params, path=DB_PATH, conn=None):
    if conn is not None:
        return conn.execute(sql, params).fetchall()
    own = get_conn(path)
    try:
        return own.execute(sql, params).fetchall()
    finally:
        own.close()


# --------------------------------------------------------------------------- #
# Self-test — uses a throwaway db, never the real feedback.db.
# --------------------------------------------------------------------------- #
def _selftest():
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="feedback_selftest_")
    test_path = os.path.join(tmpdir, "feedback_selftest.db")

    init_db(test_path)

    # Insert a sample draft.
    draft_id = record_draft(
        ticket_id=4242,
        customer_message="Where is my order? It says shipped 5 days ago.",
        draft_text="Hi! Your order shipped and is on its way. Tracking: ...",
        priority="high",
        classification_reason="order status question, fulfilled",
        kb_sources=["kb/policies/shipping-policy.md", "kb/faq/faq.md"],
        kb_gap=0,
        customer_email="parent@example.com",
        order_context={"order": "#1001", "fulfillment_status": "fulfilled"},
        conversation_snippet="customer: where is my order?",
        model_used="ollama-cloud/llama3",
        confidence=0.81,
        dry_run=1,
        posted_note_id=None,
        status="drafted",
        path=test_path,
    )

    # Insert a sample agent reply.
    reply_id = record_reply(
        ticket_id=4242,
        reply_text="Hi! Your order is on its way, here is the tracking link: ...",
        message_id=99001,
        agent_user_id=777419526,
        sender_email="agent@buttons-bebe.com",
        channel="email",
        path=test_path,
    )

    # Insert a sample comparison.
    comparison_id = record_comparison(
        ticket_id=4242,
        draft_id=draft_id,
        reply_id=reply_id,
        similarity_score=0.74,
        exact_match=0,
        edit_ops={"added": 3, "removed": 1, "replaced": 2},
        response_time_sec=812,
        notes="agent kept the gist, added the real tracking link",
        path=test_path,
    )

    # Read them back and assert round-trip.
    d = get_draft(draft_id, path=test_path)
    assert d is not None, "draft not found"
    assert d["ticket_id"] == 4242
    assert d["priority"] == "high"
    assert d["status"] == "drafted"
    assert d["dry_run"] == 1
    assert json.loads(d["kb_sources"]) == [
        "kb/policies/shipping-policy.md",
        "kb/faq/faq.md",
    ]
    assert json.loads(d["order_context"])["order"] == "#1001"

    r = get_reply(reply_id, path=test_path)
    assert r is not None, "reply not found"
    assert r["ticket_id"] == 4242
    assert r["message_id"] == 99001
    assert r["agent_user_id"] == 777419526

    c = get_comparison(comparison_id, path=test_path)
    assert c is not None, "comparison not found"
    assert c["draft_id"] == draft_id
    assert c["reply_id"] == reply_id
    assert abs(c["similarity_score"] - 0.74) < 1e-9
    assert json.loads(c["edit_ops"]) == {"added": 3, "removed": 1, "replaced": 2}

    # Read helpers.
    assert reply_exists(99001, path=test_path) is True
    assert reply_exists(12345, path=test_path) is False
    recent = recent_drafts(limit=5, path=test_path)
    assert len(recent) == 1 and recent[0]["id"] == draft_id
    by_ticket = drafts_for_ticket(4242, path=test_path)
    assert len(by_ticket) == 1

    # Foreign key enforcement: a comparison pointing at a missing draft fails.
    fk_ok = False
    try:
        record_comparison(
            ticket_id=4242,
            draft_id=999999,  # does not exist
            reply_id=reply_id,
            similarity_score=0.5,
            path=test_path,
        )
    except sqlite3.IntegrityError:
        fk_ok = True
    assert fk_ok, "foreign key constraint was not enforced"

    # Clean up the throwaway db.
    try:
        os.remove(test_path)
        os.rmdir(tmpdir)
    except OSError:
        pass

    print("SELF-TEST OK")


if __name__ == "__main__":
    # Create / verify the real feedback.db next to this module...
    init_db()
    print(f"init_db OK -> {DB_PATH}")
    # ...then run the self-test against a throwaway db (never the real one).
    _selftest()
