import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from intake import import_store
from intake.imports import parse_export
from intake.policy import Conflict, Invalid
from intake.records import canonical
from intake.store import Store

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/gorgias-synthetic.json"


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.data = json.loads(FIXTURE.read_text())
        self.payload = canonical(self.data).encode()

    def preview(self, payload=None, account="test-store"):
        return import_store.preview(self.store, payload or self.payload, account)

    def commit(self, payload=None, account="test-store"):
        payload = payload or self.payload
        return import_store.apply_import(self.store, payload, account, self.preview(payload, account)["digest"])

    def count(self, table):
        with self.store.connection() as db:
            return db.execute("SELECT count(*) FROM " + table).fetchone()[0]

    def test_preview_has_no_record_side_effects(self):
        report = self.preview()
        self.assertEqual(report["source"], {"tickets": 2, "messages": 4, "notes": 1, "attachments": 1})
        self.assertTrue(report["canImport"])
        for table in ("tickets", "messages", "contacts", "events", "import_batches", "source_records"):
            self.assertEqual(self.count(table), 0)

    def test_import_preserves_history_headers_and_attachment_metadata(self):
        report = self.commit()
        self.assertEqual(report["new"], {"tickets": 2, "messages": 4})
        with self.store.connection() as db:
            tid = db.execute("SELECT internal_id FROM source_records WHERE kind='ticket' AND external_id='900001'").fetchone()[0]
            message_source = db.execute("SELECT payload_json FROM source_records WHERE kind='message' AND external_id='910003'").fetchone()[0]
        ticket = self.store.get_ticket(tid)
        self.assertEqual([m["kind"] for m in ticket["messages"]], ["incoming", "note", "outgoing"])
        self.assertEqual(json.loads(ticket["messages"][2]["headers_json"])["In-Reply-To"], "<sample-incoming-1@example.test>")
        self.assertEqual(json.loads(message_source), self.data["tickets"][0]["messages"][2])
        self.assertEqual(ticket["created_at"], "2026-09-20T09:00:00Z")
        self.assertEqual(ticket["assignee"], "operator@example.test")
        self.assertNotEqual(tid, "900001")
        self.assertEqual(self.count("attachments"), 1)
        self.assertEqual(report["outboundActions"], 0)

    def test_reimport_and_restart_are_idempotent(self):
        first = self.commit()
        ids = [t["id"] for t in self.store.list_tickets()["tickets"]]
        self.store = Store(self.temp.name)
        second = self.commit()
        self.assertTrue(second["alreadyImported"])
        self.assertEqual(first["batchId"], second["batchId"])
        self.assertEqual(second["new"], {"tickets": 0, "messages": 0})
        self.assertEqual(second["duplicates"], {"tickets": 2, "messages": 4})
        self.assertEqual(ids, [t["id"] for t in self.store.list_tickets()["tickets"]])
        self.assertEqual(self.count("events"), 2)
        self.assertEqual(self.count("import_batches"), 1)

    def test_concurrent_imports_have_one_winner(self):
        report = self.preview()
        def work(_):
            return import_store.apply_import(self.store, self.payload, "test-store", report["digest"])
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(work, range(6)))
        self.assertEqual(sum(not r["alreadyImported"] for r in results), 1)
        self.assertEqual(self.count("messages"), 4)

    def test_account_namespaces_keep_identical_ids_separate(self):
        self.commit(account="first")
        self.commit(account="second")
        self.assertEqual(self.count("tickets"), 4)
        self.assertEqual(self.count("messages"), 8)

    def test_changed_source_is_a_conflict_without_overwrite(self):
        self.commit()
        self.data["tickets"][0]["messages"][0]["body_text"] = "Changed content"
        payload = canonical(self.data).encode()
        report = self.preview(payload)
        self.assertFalse(report["canImport"])
        with self.assertRaises(Conflict):
            self.commit(payload)
        self.assertEqual(self.count("messages"), 4)
        self.assertEqual(self.count("import_batches"), 1)

    def test_preview_digest_is_required(self):
        with self.assertRaises(Conflict):
            import_store.apply_import(self.store, self.payload, "test-store", "wrong")
        self.assertEqual(self.count("tickets"), 0)

    def test_duplicate_source_message_cannot_move_to_another_ticket(self):
        self.commit()
        ticket = copy.deepcopy(self.data["tickets"][0])
        ticket["id"] = 999999
        for message in ticket["messages"]:
            message["ticket_id"] = 999999
        payload = canonical([ticket]).encode()
        self.assertFalse(self.preview(payload)["canImport"])
        with self.assertRaises(Conflict):
            self.commit(payload)
        self.assertEqual(self.count("tickets"), 2)

    def test_bad_last_ticket_rolls_back_whole_batch(self):
        self.data["tickets"][1]["messages"][0]["public"] = "false"
        with self.assertRaises(Invalid):
            self.commit(canonical(self.data).encode())
        self.assertEqual(self.count("tickets"), 0)

    def test_storage_failure_rolls_back_and_retry_succeeds(self):
        calls = 0
        original = self.store.event
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError("simulated disk failure")
            return original(*args, **kwargs)
        with patch.object(self.store, "event", side_effect=fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                self.commit()
        for table in ("tickets", "contacts", "messages", "source_records", "events", "attachments", "import_batches"):
            self.assertEqual(self.count(table), 0)
        self.assertEqual(self.commit()["new"]["tickets"], 2)

    def test_partial_and_ambiguous_exports_rejected(self):
        variants = []
        for mutation in (lambda t: t.pop("messages"), lambda t: t.update(messages_count=99),
                         lambda t: t["messages"][0].pop("public"), lambda t: t.update(created_datetime="yesterday"),
                         lambda t: t["messages"][0].update(ticket_id=9999),
                         lambda t: t["messages"].append(copy.deepcopy(t["messages"][0]))):
            data = copy.deepcopy(self.data)
            mutation(data["tickets"][0])
            variants.append(data)
        variants.append({"data": self.data["tickets"], "meta": {"next_cursor": "another-page"}})
        data = copy.deepcopy(self.data)
        data["tickets"][0]["messages"] = {"data": data["tickets"][0]["messages"], "meta": {"next_cursor": "more"}}
        variants.append(data)
        for data in variants:
            with self.subTest(data_type=type(data).__name__), self.assertRaises(Invalid):
                parse_export(canonical(data).encode())

    def test_supported_envelopes_and_no_synthetic_fallback(self):
        for data in (self.data, self.data["tickets"], {"data": self.data["tickets"], "meta": {"next_cursor": None}}):
            self.assertEqual(len(parse_export(canonical(data).encode())[0]), 2)
        for payload in (b"id,subject\n1,hello", b"[]", b"{bad", b'{"tickets":NaN}', b'{"tickets":[],"tickets":[]}'):
            with self.assertRaises(Invalid):
                parse_export(payload)

    def test_html_is_displayed_as_text_without_active_content(self):
        tickets, _ = parse_export(self.payload)
        body = tickets[1]["messages"][0]["body"]
        self.assertIn("button arrived damaged", body)
        self.assertNotIn("script", body)
        self.assertNotIn("https://", body)
        self.assertIn("https://", tickets[1]["messages"][0]["raw"]["body_html"])

    def test_manual_ticket_note_and_stale_update(self):
        request = {"operation_id": "create-one", "subject": "Test", "body": "Private test description", "name": "Example", "email": "example@example.test"}
        result = self.store.create_ticket(request)
        self.assertEqual(result, self.store.create_ticket(request))
        tid = result["id"]
        self.assertEqual(self.store.get_ticket(tid)["messages"][0]["kind"], "note")
        note = {"operation_id": "note-one", "body": "Private note", "revision": 1}
        self.assertEqual(self.store.add_note(tid, note), self.store.add_note(tid, note))
        with self.assertRaises(Conflict):
            self.store.update_ticket(tid, {"operation_id": "update-one", "revision": 1, "status": "closed", "priority": "high", "assignee": "Test"})
        changed = self.store.update_ticket(tid, {"operation_id": "update-two", "revision": 2, "status": "closed", "priority": "high", "assignee": "Test"})
        self.assertEqual(changed["revision"], 3)
        self.assertEqual(Store(self.temp.name).get_ticket(tid)["status"], "closed")
        self.assertEqual(self.count("messages"), 2)

    def test_reused_operation_with_different_payload_is_rejected(self):
        request = {"operation_id": "same", "subject": "A", "body": "One"}
        self.store.create_ticket(request)
        with self.assertRaises(Conflict):
            self.store.create_ticket({**request, "body": "Two"})
        self.assertEqual(self.count("tickets"), 1)

    def test_import_does_not_reset_operator_changes(self):
        self.commit()
        tid = self.store.list_tickets()["tickets"][0]["id"]
        self.store.update_ticket(tid, {"operation_id": "edit", "revision": 1, "status": "closed", "priority": "low", "assignee": "Local operator"})
        self.commit()
        self.assertEqual(self.store.get_ticket(tid)["assignee"], "Local operator")

    def test_unrecognized_database_and_links_are_refused(self):
        with tempfile.TemporaryDirectory() as other:
            path = Path(other) / "intake.sqlite3"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE production_marker(value)")
            db.close()
            with self.assertRaises(Invalid):
                Store(other)
            path.unlink()
            path.symlink_to(self.store.path)
            with self.assertRaises(Invalid):
                Store(other)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
