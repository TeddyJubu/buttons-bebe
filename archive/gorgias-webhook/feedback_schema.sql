-- feedback_schema.sql — schema for feedback.db (Buttons Bebe Hermes agent)
--
-- This is the OPERATIONAL database for the AI support agent. It holds ONLY
-- agent-vs-AI performance data: every AI draft, every captured human-agent
-- reply, and the difflib comparison that links them. It is NOT the knowledge
-- base. There is intentionally NO kb_entries table — knowledge storage and
-- retrieval live in Supermemory (fed from a Git repo of Markdown).
-- See PHASE1_KB_ARCHITECTURE.md ("What stays in SQLite") and
-- SYSTEM_WORKFLOW.md ("DATABASE SCHEMA — feedback.db") for the canonical spec.
--
-- All statements use IF NOT EXISTS so this file is safe to apply repeatedly.

PRAGMA foreign_keys = ON;

-- --------------------------------------------------------------------------
-- drafts — one row per AI-generated draft (Workflow A, step A10)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS drafts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id             INTEGER NOT NULL,
    customer_message      TEXT    NOT NULL,           -- what the customer asked
    draft_text            TEXT    NOT NULL,           -- the AI's drafted reply
    priority              TEXT    NOT NULL,           -- immediate / high / low
    classification_reason TEXT,                       -- why it was classified
    kb_sources            TEXT,                       -- JSON: which KB sources were used
    kb_gap                INTEGER NOT NULL DEFAULT 0,  -- 1 if no KB match found
    kb_gap_question       TEXT,                       -- the question sent to owner
    kb_gap_answer         TEXT,                       -- owner's answer (saved to KB)
    customer_email        TEXT,
    order_context         TEXT,                       -- JSON snapshot
    conversation_snippet  TEXT,                       -- last few messages for context
    model_used            TEXT,                       -- which LLM produced the draft
    confidence            REAL,
    dry_run               INTEGER NOT NULL DEFAULT 1,  -- 1 if not actually posted to Gorgias
    posted_note_id        INTEGER,                     -- Gorgias internal-note id if posted (nullable)
    status                TEXT    NOT NULL DEFAULT 'drafted',
                                  -- drafted / matched / no_reply / superseded
    matched_reply_id      INTEGER,                    -- FK -> replies.id (set in Workflow B)
    created_at            TEXT    NOT NULL,            -- ISO8601 UTC
    FOREIGN KEY (matched_reply_id) REFERENCES replies(id)
);

-- --------------------------------------------------------------------------
-- replies — one row per real human-agent reply (Workflow B, step B2)
--           deduplicated on the Gorgias message_id
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS replies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id     INTEGER NOT NULL,
    message_id    INTEGER,                 -- Gorgias message id (dedup key)
    reply_text    TEXT    NOT NULL,
    agent_user_id INTEGER,                 -- Gorgias user id of the agent who replied
    sender_email  TEXT,                    -- which agent replied (email)
    channel       TEXT,                    -- email / chat / etc.
    created_at    TEXT    NOT NULL         -- ISO8601 UTC
);

-- --------------------------------------------------------------------------
-- comparisons — links a draft to the human reply with difflib metrics
--               (Workflow B, step B4)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS comparisons (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id         INTEGER NOT NULL,
    draft_id          INTEGER NOT NULL,
    reply_id          INTEGER NOT NULL,
    similarity_score  REAL,                -- 0.0..1.0 from difflib.SequenceMatcher.ratio()
    exact_match       INTEGER NOT NULL DEFAULT 0,  -- 1 if normalized texts identical
    edit_ops          TEXT,                -- JSON: {added, removed, replaced}
    response_time_sec INTEGER,             -- seconds from draft posted to agent reply
    notes             TEXT,                -- optional free-form review note
    created_at        TEXT    NOT NULL,    -- ISO8601 UTC
    FOREIGN KEY (draft_id) REFERENCES drafts(id),
    FOREIGN KEY (reply_id) REFERENCES replies(id)
);

-- --------------------------------------------------------------------------
-- Indexes — the hot lookups are "all rows for a ticket" (both workflows) and
-- the dedup check on replies.message_id (Workflow B, step B2).
-- --------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_drafts_ticket_id       ON drafts (ticket_id);
CREATE INDEX IF NOT EXISTS idx_drafts_status          ON drafts (status);
CREATE INDEX IF NOT EXISTS idx_replies_ticket_id      ON replies (ticket_id);
CREATE INDEX IF NOT EXISTS idx_replies_message_id     ON replies (message_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_ticket_id  ON comparisons (ticket_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_draft_id   ON comparisons (draft_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_reply_id   ON comparisons (reply_id);
