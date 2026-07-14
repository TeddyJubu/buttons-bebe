from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import notices_lib  # noqa: E402


class TestNotices(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.notices_dir = root / "notices"
        self.notices_file = self.notices_dir / "notices.json"
        self.lock_dir = self.notices_dir / ".notices.lock"
        self.patches = [
            patch.object(notices_lib, "NOTICES_DIR", self.notices_dir),
            patch.object(notices_lib, "NOTICES_FILE", self.notices_file),
            patch.object(notices_lib, "LOCK_DIR", self.lock_dir),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_add_and_remove_use_canonical_schema(self) -> None:
        notice = notices_lib.add_notice("Ship today", expires_at="2030-01-01T00:00:00Z")
        self.assertTrue(notices_lib._valid_notice(notice))
        self.assertEqual(notices_lib.active_notices()[0]["text"], "Ship today")
        self.assertTrue(notices_lib.remove_notice(notice["id"]))
        self.assertEqual(notices_lib.load_all(), [])

    def test_invalid_expiry_is_rejected_without_creating_store(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid expires_at"):
            notices_lib.add_notice("Bad deadline", expires_at="not-a-date")
        self.assertFalse(self.notices_file.exists())

    def test_created_by_is_normalized_to_a_canonical_string(self) -> None:
        notice = notices_lib.add_notice("Normalized", created_by="   ")
        self.assertEqual(notice["created_by"], "owner")
        self.assertTrue(notices_lib._valid_notice(notice))

    def test_malformed_store_is_fail_safe_for_reads_and_refused_for_writes(self) -> None:
        self.notices_dir.mkdir(parents=True)
        self.notices_file.write_text(json.dumps([{"id": "bad", "text": "missing timestamps"}]))
        self.assertEqual(notices_lib.load_all(), [])
        with self.assertRaisesRegex(ValueError, "malformed"):
            notices_lib.add_notice("Do not erase malformed data")
        self.assertIn("missing timestamps", self.notices_file.read_text())

    def test_lock_contention_fails_fast(self) -> None:
        self.notices_dir.mkdir(parents=True)
        self.lock_dir.mkdir()
        with self.assertRaises(notices_lib.NoticeBusy):
            notices_lib.add_notice("Busy")

    def test_failed_atomic_replace_preserves_previous_store(self) -> None:
        notice = {
            "id": "n_existing",
            "text": "Existing",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": None,
            "created_by": "owner",
        }
        self.notices_dir.mkdir(parents=True)
        self.notices_file.write_text(json.dumps([notice]))
        original = self.notices_file.read_text()
        with patch.object(notices_lib.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                notices_lib.add_notice("New")
        self.assertEqual(self.notices_file.read_text(), original)
        self.assertFalse(list(self.notices_dir.glob(".notices-*.tmp")))

    def test_purge_expired_is_locked_and_keeps_live_items(self) -> None:
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        self.notices_dir.mkdir(parents=True)
        self.notices_file.write_text(json.dumps([
            {"id": "n_old", "text": "Old", "created_at": "2026-01-01T00:00:00+00:00", "expires_at": "2026-01-01T12:00:00+00:00", "created_by": "owner"},
            {"id": "n_live", "text": "Live", "created_at": "2026-01-01T00:00:00+00:00", "expires_at": "2026-01-03T00:00:00+00:00", "created_by": "owner"},
        ]))
        self.assertEqual(notices_lib.purge_expired(now), 1)
        self.assertEqual([n["id"] for n in notices_lib.load_all()], ["n_live"])


if __name__ == "__main__":
    unittest.main()
