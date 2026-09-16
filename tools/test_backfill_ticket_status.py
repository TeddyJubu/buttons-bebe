"""Synthetic status backfill tests. No live Gorgias or production files."""
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
import backfill_ticket_status as backfill


SCHEMA = """
CREATE TABLE parsed_messages (
    message_id TEXT PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    ticket_status TEXT,
    ticket_tags TEXT,
    ticket_assignee TEXT,
    received_at TEXT NOT NULL
);
CREATE TABLE job_queue (id INTEGER PRIMARY KEY, status TEXT NOT NULL);
"""


class BackfillTicketStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "webhook.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def add(self, message_id, ticket_id, received_at, status=None, tags='[]'):
        self.conn.execute(
            "INSERT INTO parsed_messages VALUES (?,?,?,?,?,?)",
            (message_id, ticket_id, status, tags, None, received_at),
        )
        self.conn.execute("INSERT INTO job_queue(status) VALUES ('done')")
        self.conn.commit()

    def test_fills_empty_rows_and_keeps_existing(self):
        self.add("old", 1, "2026-09-01", None, '["auto-close"]')
        self.add("new", 1, "2026-09-15", None, '["auto-close"]')
        self.add("kept", 2, "2026-09-15", "closed")
        updates = backfill.plan(self.conn, {1: "open", 2: "open"})
        self.assertEqual({item["message_id"] for item in updates}, {"old", "new"})
        changed = backfill.apply_updates(self.conn, updates)
        self.assertEqual(changed, 2)
        rows = {row["message_id"]: dict(row) for row in self.conn.execute("SELECT * FROM parsed_messages")}
        self.assertEqual(rows["old"]["ticket_status"], "open")
        self.assertEqual(rows["new"]["ticket_status"], "open")
        self.assertEqual(rows["kept"]["ticket_status"], "closed")
        self.assertEqual(rows["old"]["ticket_tags"], '["auto-close"]')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0], 3)

    def test_offline_json_dry_run_does_not_write(self):
        self.add("m1", 7, "2026-09-15")
        mapping = Path(self.tmp.name) / "statuses.json"
        mapping.write_text(json.dumps({"7": "open", "8": "nope!"}))
        self.conn.close()
        with patch("builtins.print"):
            rc = backfill.main(["--source", str(self.path), "--statuses-json", str(mapping)])
        self.assertEqual(rc, 0)
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT ticket_status FROM parsed_messages").fetchone()[0])

    def test_normalize_rejects_junk(self):
        self.assertEqual(backfill.normalize_status(" Open "), "open")
        self.assertIsNone(backfill.normalize_status("<script>"))


if __name__ == "__main__":
    unittest.main()
