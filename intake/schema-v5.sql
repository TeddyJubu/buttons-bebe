PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sandbox_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, email TEXT NOT NULL, source_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    subject TEXT NOT NULL, contact_id TEXT REFERENCES contacts(id),
    status TEXT NOT NULL CHECK(status IN ('open','waiting_customer','waiting_team','snoozed','closed')),
    priority TEXT NOT NULL CHECK(priority IN ('low','normal','high','critical')),
    assignee TEXT NOT NULL, channel TEXT NOT NULL, origin TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
    queue TEXT NOT NULL DEFAULT 'inbox' CHECK(queue IN ('inbox','review','spam','automatic')),
    review_reason TEXT NOT NULL DEFAULT '', status_changed_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    kind TEXT NOT NULL CHECK(kind IN ('incoming','outgoing','note')),
    author_name TEXT NOT NULL, author_email TEXT NOT NULL, body TEXT NOT NULL,
    created_at TEXT NOT NULL, channel TEXT NOT NULL,
    headers_json TEXT NOT NULL DEFAULT '{}', origin TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_ticket ON messages(ticket_id, created_at, sequence);
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id),
    metadata_json TEXT NOT NULL, availability TEXT NOT NULL DEFAULT 'metadata_only'
);
CREATE TABLE IF NOT EXISTS source_records (
    kind TEXT NOT NULL, account TEXT NOT NULL, external_id TEXT NOT NULL,
    internal_id TEXT NOT NULL, ticket_id TEXT NOT NULL REFERENCES tickets(id),
    digest TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(kind, account, external_id)
);
CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY, account TEXT NOT NULL, digest TEXT NOT NULL,
    created_at TEXT NOT NULL, report_json TEXT NOT NULL,
    UNIQUE(account, digest)
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT REFERENCES tickets(id),
    kind TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, digest TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replay_receipts (
    account TEXT NOT NULL, mailbox TEXT NOT NULL, event_id TEXT NOT NULL,
    digest TEXT NOT NULL, result_json TEXT NOT NULL, payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY(account,mailbox,event_id)
);
CREATE TABLE IF NOT EXISTS replay_messages (
    message_id TEXT PRIMARY KEY REFERENCES messages(id),
    account TEXT NOT NULL, mailbox TEXT NOT NULL, provider TEXT NOT NULL,
    external_id TEXT NOT NULL, payload_json TEXT NOT NULL,
    UNIQUE(account,mailbox,provider,external_id)
);
CREATE TABLE IF NOT EXISTS replay_aliases (
    account TEXT NOT NULL, mailbox TEXT NOT NULL, provider TEXT NOT NULL,
    external_id TEXT NOT NULL, message_id TEXT NOT NULL REFERENCES messages(id),
    signature TEXT NOT NULL,
    PRIMARY KEY(account,mailbox,provider,external_id)
);
CREATE TABLE IF NOT EXISTS delivery_reviews (
    id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id),
    revision INTEGER NOT NULL, envelope_json TEXT NOT NULL, body TEXT NOT NULL,
    scenario TEXT NOT NULL, retry_of TEXT, content_key TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, digest TEXT NOT NULL,
    generation TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS delivery_attempts (
    id TEXT PRIMARY KEY, review_id TEXT NOT NULL UNIQUE REFERENCES delivery_reviews(id),
    ticket_id TEXT NOT NULL REFERENCES tickets(id), content_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('attempting','uncertain','failed','simulated_delivered')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    message_id TEXT UNIQUE REFERENCES messages(id), reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_delivery ON delivery_attempts(ticket_id)
    WHERE state IN ('attempting','uncertain');
CREATE UNIQUE INDEX IF NOT EXISTS one_accepted_content ON delivery_attempts(ticket_id,content_key)
    WHERE state='simulated_delivered';
CREATE TABLE IF NOT EXISTS fake_dispatches (
    attempt_id TEXT PRIMARY KEY REFERENCES delivery_attempts(id),
    digest TEXT NOT NULL, outcome TEXT NOT NULL CHECK(outcome IN ('accepted','rejected','unknown')),
    receipt_id TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS simulated_outgoing (
    message_id TEXT PRIMARY KEY REFERENCES messages(id),
    attempt_id TEXT NOT NULL UNIQUE REFERENCES delivery_attempts(id),
    account TEXT NOT NULL, mailbox TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachment_blobs (
    sha256 TEXT PRIMARY KEY, byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 0 AND 26214400),
    data BLOB NOT NULL CHECK(length(data)=byte_size)
);
CREATE TABLE IF NOT EXISTS attachment_files (
    attachment_id TEXT PRIMARY KEY REFERENCES attachments(id),
    sha256 TEXT NOT NULL REFERENCES attachment_blobs(sha256), imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachment_imports (
    id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
    report_json TEXT NOT NULL
);

-- E2 identities are local to one offline workspace. Source identities stay intact.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','agent','viewer')),
    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    audience TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_limits (
    key TEXT PRIMARY KEY, window_start INTEGER NOT NULL, failures INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_assignments (
    ticket_id TEXT PRIMARY KEY REFERENCES tickets(id), user_id TEXT NOT NULL REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS ticket_reads (
    ticket_id TEXT NOT NULL REFERENCES tickets(id), user_id TEXT NOT NULL REFERENCES users(id),
    seen_sequence INTEGER NOT NULL DEFAULT 0 CHECK(seen_sequence>=0),
    forced_unread INTEGER NOT NULL DEFAULT 0 CHECK(forced_unread IN (0,1)),
    version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(ticket_id,user_id)
);
CREATE TABLE IF NOT EXISTS review_owners (
    review_id TEXT PRIMARY KEY REFERENCES delivery_reviews(id), user_id TEXT NOT NULL REFERENCES users(id)
);
