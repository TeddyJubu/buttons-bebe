import copy
import json
from pathlib import Path
import tempfile
import unittest

from intake_export.mcp_read import ExportError, ReadClient
from intake_export.snapshot import capture, full_ticket
from intake import import_store
from intake.policy import Invalid
from intake.rehearse import fidelity, read_snapshot
from intake.store import Store


class FakeClient:
    def __init__(self):
        self.ticket = json.loads((Path(__file__).resolve().parents[1] / "fixtures/gorgias-synthetic.json").read_text())["tickets"][0]
        self.calls = []
        self.detail_reads = 0
        self.change = False
        self.repeat = False

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "list_inbox_tickets":
            return {"data": [{"id": self.ticket["id"]}], "meta": {"next_cursor": "more-tickets"}}
        if name == "get_ticket":
            self.detail_reads += 1
            result = copy.deepcopy(self.ticket)
            if self.change and self.detail_reads > 1:
                result["updated_datetime"] = "2026-09-22T00:00:00Z"
            return result
        if name == "get_ticket_messages":
            messages = self.ticket["messages"]
            return {"data": messages[:2] if not args.get("cursor") or self.repeat else messages[2:],
                    "meta": {"next_cursor": "next-page" if not args.get("cursor") or self.repeat else None}}
        raise AssertionError("Unexpected tool")


class ExportTests(unittest.TestCase):
    def test_capture_collects_all_pages_and_private_manifest(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            report = capture(client, Path(directory), limit=1, delay=0)
            self.assertTrue(report["sample_capture_complete"])
            self.assertFalse(report["account_export_complete"])
            self.assertEqual(report["counts"]["messages"], 3)
            self.assertEqual(report["counts"]["notes"], 1)
            self.assertEqual(report["provider_writes"], 0)
            self.assertTrue(any(args.get("cursor") for name, args in client.calls if name == "get_ticket_messages"))
            for path in Path(directory).iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            saved = json.loads((Path(directory) / report["files"][0]["file"]).read_text())
            self.assertEqual(saved["tickets"][0]["messages"], client.ticket["messages"])

    def test_changed_ticket_is_reported_and_not_exported_as_complete(self):
        client = FakeClient()
        client.change = True
        with tempfile.TemporaryDirectory() as directory:
            report = capture(client, Path(directory), limit=1, delay=0)
            self.assertFalse(report["sample_capture_complete"])
            self.assertEqual(len(report["failed"]), 1)
            self.assertEqual(report["files"], [])

    def test_repeated_message_pages_fail_closed(self):
        client = FakeClient()
        client.repeat = True
        with self.assertRaises(ExportError):
            full_ticket(client, client.ticket["id"], lambda: None)

    def test_message_count_mismatch_is_not_papered_over(self):
        client = FakeClient()
        client.ticket["messages_count"] = 5
        with self.assertRaises(ExportError):
            full_ticket(client, client.ticket["id"], lambda: None)

    def test_export_client_rejects_every_non_read_tool_without_network(self):
        client = ReadClient()
        for name in ("send_reply", "create_ticket", "delete_ticket", "get_customer", "search_customer"):
            with self.assertRaises(ExportError):
                client.call(name, {})

    def test_reconciliation_detects_modified_snapshot_and_imported_content(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture(client, root, limit=1, delay=0)
            manifest, payloads = read_snapshot(root)
            dbdir = root / "database"
            dbdir.mkdir()
            store = Store(dbdir)
            preview = import_store.preview(store, payloads[0], "test")
            import_store.apply_import(store, payloads[0], "test", preview["digest"])
            self.assertTrue(fidelity(store, payloads, "test")["passed"])
            with store.connection(write=True) as db:
                db.execute("UPDATE messages SET body='corrupted'")
            report = fidelity(store, payloads, "test")
            self.assertFalse(report["passed"])
            self.assertEqual(report["failures"]["message_display_text"], 3)
            (root / manifest["files"][0]["file"]).write_text('{}')
            with self.assertRaises(Invalid):
                read_snapshot(root)


if __name__ == "__main__":
    unittest.main()
