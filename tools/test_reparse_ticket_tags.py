"""Synthetic tags-only reparse tests. No production files or credentials."""
from contextlib import closing
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT.parent / "webhook" / "src"))
import reparse_ticket_tags as reparse


SCHEMA = """
CREATE TABLE webhook_events (
    message_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    ticket_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    author_type TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TABLE parsed_messages (
    message_id TEXT PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    author_type TEXT NOT NULL,
    ticket_status TEXT,
    ticket_assignee TEXT,
    ticket_tags TEXT,
    ticket_priority TEXT,
    ticket_spam INTEGER NOT NULL DEFAULT 0,
    message_text TEXT,
    received_at TEXT NOT NULL
);
CREATE TABLE job_queue (
    id INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


def payload(tags):
    return json.dumps({
        "trigger": "ticket-message-created",
        "ticket": {"id": 1, "tags": tags, "status": "open", "assignee_user_id": "9"},
        "message": {"id": "m"},
    })


class ReparseTicketTagsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "webhook.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def add(self, message_id, received_at, tags, stored="[]", ticket_id=1, extra_parsed=None):
        raw = payload(tags)
        self.conn.execute(
            "INSERT INTO webhook_events VALUES (?,?,?,?,?,?,?)",
            (message_id, "buttonsbebe", ticket_id, "ticket-message-created", "customer", raw, received_at),
        )
        parsed = extra_parsed or {}
        self.conn.execute(
            "INSERT INTO parsed_messages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                message_id, ticket_id, "ticket-message-created", "customer",
                parsed.get("ticket_status"), parsed.get("ticket_assignee"), stored,
                parsed.get("ticket_priority"), 0, "hello", received_at,
            ),
        )
        self.conn.execute("INSERT INTO job_queue(message_id, status) VALUES (?, 'done')", (message_id,))
        self.conn.commit()

    def test_fills_auto_close_json_objects_on_empty_rows(self):
        self.add("old", "2026-09-15T08:34:00Z", '[{"id":257474,"name":"auto-close"}]')
        self.add("new", "2026-09-15T10:55:00Z", "[]")
        updates = reparse.plan(self.conn)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["tags"], ["auto-close"])
        self.assertFalse(updates[0]["latest"])

    def test_skips_empty_and_existing_tags(self):
        self.add("kept", "2026-09-15T08:00:00Z", '[{"name":"vip"}]', stored='["vip"]')
        self.add("blank", "2026-09-15T09:00:00Z", "[]")
        self.assertEqual(reparse.plan(self.conn), [])

    def test_apply_is_fill_only_and_leaves_other_columns(self):
        self.add(
            "m1", "2026-09-15T08:34:00Z", '[{"name":"auto-close"}]',
            extra_parsed={"ticket_status": None, "ticket_assignee": None},
        )
        self.add("m2", "2026-09-15T10:55:00Z", "[]")
        updates = reparse.plan(self.conn)
        changed = reparse.apply_updates(self.conn, updates)
        self.assertEqual(changed, 1)
        row = self.conn.execute("SELECT * FROM parsed_messages WHERE message_id='m1'").fetchone()
        self.assertEqual(json.loads(row["ticket_tags"]), ["auto-close"])
        self.assertIsNone(row["ticket_status"])
        self.assertIsNone(row["ticket_assignee"])
        later = self.conn.execute("SELECT ticket_tags FROM parsed_messages WHERE message_id='m2'").fetchone()
        self.assertEqual(later["ticket_tags"], "[]")
        jobs = [tuple(row) for row in self.conn.execute("SELECT COUNT(*), status FROM job_queue GROUP BY status")]
        self.assertEqual(jobs, [(2, "done")])

    def test_malformed_raw_is_skipped(self):
        self.conn.execute(
            "INSERT INTO webhook_events VALUES (?,?,?,?,?,?,?)",
            ("bad", "buttonsbebe", 1, "ticket-message-created", "customer", "{not json", "2026-09-15T08:00:00Z"),
        )
        self.conn.execute(
            "INSERT INTO parsed_messages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("bad", 1, "ticket-message-created", "customer", None, None, "[]", None, 0, "hello", "2026-09-15T08:00:00Z"),
        )
        self.conn.commit()
        self.assertEqual(reparse.plan(self.conn), [])

    def test_dry_run_main_does_not_write(self):
        self.add("m1", "2026-09-15T08:34:00Z", '[{"name":"auto-close"}]')
        self.conn.close()
        with patch("builtins.print"):
            rc = reparse.main(["--source", str(self.path)])
        self.assertEqual(rc, 0)
        with closing(sqlite3.connect(self.path)) as db:
            stored = db.execute("SELECT ticket_tags FROM parsed_messages").fetchone()[0]
        self.assertEqual(stored, "[]")


if __name__ == "__main__":
    unittest.main()
