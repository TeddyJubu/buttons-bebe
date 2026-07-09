"""SQLite schema + connection helpers (WAL mode).

Tables: customers, tickets, messages, drafts, tags, ticket_tags, jobs,
audit_log, chat_sessions, whatsapp_outbox.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import config


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.db_path(), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT,
    name        TEXT,
    firstname   TEXT,
    lastname    TEXT,
    phone       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);

CREATE TABLE IF NOT EXISTS tickets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    subject           TEXT,
    status            TEXT NOT NULL DEFAULT 'open',   -- open|closed|snoozed
    channel           TEXT NOT NULL,                  -- email|chat|whatsapp
    sensitive         INTEGER NOT NULL DEFAULT 0,
    sensitive_reason  TEXT,
    customer_id       INTEGER NOT NULL,
    assignee          TEXT,
    snooze_until      TEXT,
    is_unread         INTEGER NOT NULL DEFAULT 1,
    order_context     TEXT,                           -- JSON {orders,returns} or NULL
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_message_at   TEXT NOT NULL,
    FOREIGN KEY(customer_id) REFERENCES customers(id)
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_channel ON tickets(channel);
CREATE INDEX IF NOT EXISTS idx_tickets_customer ON tickets(customer_id);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    from_agent  INTEGER NOT NULL DEFAULT 0,
    public      INTEGER NOT NULL DEFAULT 1,           -- 0 => internal note
    channel     TEXT NOT NULL,                        -- email|chat|whatsapp|internal-note
    body_text   TEXT NOT NULL,
    sender_name TEXT,
    via         TEXT NOT NULL DEFAULT 'customer',     -- customer|console|ai|api
    created_at  TEXT NOT NULL,
    FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);
CREATE INDEX IF NOT EXISTS idx_messages_ticket ON messages(ticket_id);

CREATE TABLE IF NOT EXISTS drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    body_text   TEXT NOT NULL,
    risk        TEXT NOT NULL DEFAULT 'low',          -- low|sensitive
    risk_reason TEXT,
    brain       TEXT NOT NULL DEFAULT 'mock',
    kb_refs     TEXT NOT NULL DEFAULT '[]',           -- JSON list
    status      TEXT NOT NULL DEFAULT 'proposed',     -- proposed|sent|noted|superseded
    created_at  TEXT NOT NULL,
    FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);
CREATE INDEX IF NOT EXISTS idx_drafts_ticket ON drafts(ticket_id);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS ticket_tags (
    ticket_id INTEGER NOT NULL,
    tag_id    INTEGER NOT NULL,
    PRIMARY KEY(ticket_id, tag_id),
    FOREIGN KEY(ticket_id) REFERENCES tickets(id),
    FOREIGN KEY(tag_id) REFERENCES tags(id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    message_id  INTEGER,
    kind        TEXT NOT NULL DEFAULT 'draft',
    status      TEXT NOT NULL DEFAULT 'queued',       -- queued|running|done|error
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER,
    who         TEXT NOT NULL DEFAULT 'console',
    action      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ticket ON audit_log(ticket_id);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  TEXT PRIMARY KEY,
    customer_id INTEGER,
    ticket_id   INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS whatsapp_outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    phone       TEXT,
    body_text   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
