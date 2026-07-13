from __future__ import annotations

import fcntl
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
fake_lancedb = types.ModuleType("lancedb")
fake_lancedb.connect = None
sys.modules.setdefault("lancedb", fake_lancedb)
fake_kb_lib = types.ModuleType("kb_lib")
fake_kb_lib.DB_DIR = Path("/tmp/buttonsbebe-index-test")
fake_kb_lib.TABLE = "kb"
fake_kb_lib.KBChunk = object
fake_kb_lib.load_rows = None
fake_kb_lib.embed_passages = None
fake_kb_lib.embed_query = None
sys.modules.setdefault("kb_lib", fake_kb_lib)

import index_kb  # noqa: E402


class FakeTable:
    def __init__(self, root: Path, fail: bool = False, corrupt: bool = False) -> None:
        self.root = root
        self.fail = fail
        self.corrupt = corrupt
        self.rows: list[dict] = []

    def add(self, rows: list[dict]) -> None:
        self.rows = [dict(row) for row in rows]
        (self.root / "rows.txt").write_text(str(len(rows)))
        (self.root / "rows.json").write_text(json.dumps(self.rows))

    def create_fts_index(self, *_args, **_kwargs) -> None:
        if self.fail:
            raise RuntimeError("simulated FTS failure")
        (self.root / "fts.ready").write_text("ok")

    def to_arrow(self):
        rows = [dict(row) for row in self.rows]
        if self.corrupt and rows:
            rows[0]["sensitive"] = not rows[0]["sensitive"]
        return FakeArrow(rows)


class FakeArrow:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def select(self, fields: list[str]):
        return FakeArrow([{field: row[field] for field in fields} for row in self.rows])

    def to_pylist(self) -> list[dict]:
        return self.rows


class FakeDB:
    def __init__(self, root: Path, fail: bool = False, corrupt: bool = False) -> None:
        self.root = root
        self.fail = fail
        self.corrupt = corrupt

    def create_table(self, *_args, **_kwargs) -> FakeTable:
        return FakeTable(self.root, self.fail, self.corrupt)


class TestIndexKB(unittest.TestCase):
    def _patch_paths(self, root: Path):
        db_dir = root / "lancedb"
        lock_path = root / ".index_kb.lock"
        return patch.object(index_kb, "DB_DIR", db_dir), patch.object(index_kb, "LOCK_PATH", lock_path)

    def test_empty_input_does_not_open_or_replace_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "lancedb"
            db_dir.mkdir()
            marker = db_dir / "last-known-good"
            marker.write_text("keep")
            with self._patch_paths(root)[0], self._patch_paths(root)[1]:
                with patch.object(index_kb, "load_rows", return_value=[]), patch.object(index_kb.lancedb, "connect") as connect:
                    with self.assertRaisesRegex(SystemExit, "last-known-good"):
                        index_kb.main()
            connect.assert_not_called()
            self.assertEqual(marker.read_text(), "keep")

    def test_lock_contention_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / ".index_kb.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("w") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(index_kb, "LOCK_PATH", lock_path):
                    with self.assertRaisesRegex(SystemExit, "already running"):
                        with index_kb._index_lock():
                            pass

    def test_embedding_mismatch_preserves_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "lancedb"
            db_dir.mkdir()
            marker = db_dir / "last-known-good"
            marker.write_text("keep")
            paths = self._patch_paths(root)
            with paths[0], paths[1]:
                with patch.object(index_kb, "load_rows", return_value=[{"text": "one"}, {"text": "two"}]), patch.object(index_kb, "embed_passages", return_value=[[0.1]]):
                    with self.assertRaisesRegex(SystemExit, "embedding count mismatch"):
                        index_kb.main()
            self.assertEqual(marker.read_text(), "keep")
            self.assertFalse(list(root.glob(".lancedb-staging-*")))

    def test_failed_staging_preserves_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "lancedb"
            db_dir.mkdir()
            marker = db_dir / "last-known-good"
            marker.write_text("keep")
            fake_db = lambda path: FakeDB(Path(path), fail=True)
            paths = self._patch_paths(root)
            with paths[0], paths[1]:
                with patch.object(index_kb, "load_rows", return_value=[{"text": "one"}]), patch.object(index_kb, "embed_passages", return_value=[[0.1]]), patch.object(index_kb.lancedb, "connect", side_effect=fake_db):
                    with self.assertRaisesRegex(RuntimeError, "FTS failure"):
                        index_kb.main()
            self.assertEqual(marker.read_text(), "keep")
            self.assertFalse(list(root.glob(".lancedb-staging-*")))

    def test_successful_build_promotes_staged_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "lancedb"
            db_dir.mkdir()
            (db_dir / "last-known-good").write_text("old")
            fake_db = lambda path: FakeDB(Path(path))
            row = {
                "id": "one",
                "file": "policies/one.md",
                "title": "One",
                "category": "policies",
                "status": "confirmed",
                "source": "owner",
                "tags": "shipping",
                "heading": "One",
                "sensitive": False,
                "text": "one",
            }
            paths = self._patch_paths(root)
            with paths[0], paths[1]:
                with patch.object(index_kb, "load_rows", return_value=[row]), patch.object(index_kb, "embed_passages", return_value=[[0.1]]), patch.object(index_kb.lancedb, "connect", side_effect=fake_db):
                    index_kb.main()
            self.assertTrue((db_dir / "rows.txt").exists())
            self.assertTrue((db_dir / "fts.ready").exists())
            self.assertFalse((db_dir / "last-known-good").exists())
            self.assertFalse(list(root.glob(".lancedb-backup-*")))

    def test_rebuild_preserves_content_and_sensitive_labels_exactly(self) -> None:
        rows = [
            {
                "id": "refund-row",
                "file": "policies/refunds.md",
                "title": "Refunds",
                "category": "policies",
                "status": "confirmed",
                "source": "owner",
                "tags": "refund",
                "heading": "Refund requests",
                "sensitive": True,
                "text": "Refunds -- Refund requests\n\nEscalate for review.",
            },
            {
                "id": "shipping-row",
                "file": "policies/shipping.md",
                "title": "Shipping",
                "category": "policies",
                "status": "confirmed",
                "source": "owner",
                "tags": "shipping",
                "heading": "Delivery times",
                "sensitive": False,
                "text": "Shipping -- Delivery times\n\nAllow 7-14 days.",
            },
        ]
        vectors = [[0.1], [0.2]]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._patch_paths(root)
            with paths[0], paths[1]:
                with patch.object(index_kb, "load_rows", return_value=rows), patch.object(
                    index_kb, "embed_passages", return_value=vectors
                ), patch.object(
                    index_kb.lancedb,
                    "connect",
                    side_effect=lambda path: FakeDB(Path(path)),
                ):
                    index_kb.main()

            rebuilt = json.loads((root / "lancedb" / "rows.json").read_text())
            for row in rebuilt:
                row.pop("vector")
            self.assertEqual(rebuilt, rows)
            self.assertEqual(sum(bool(row["sensitive"]) for row in rebuilt), 1)

    def test_staged_content_mismatch_preserves_last_known_good_index(self) -> None:
        row = {
            "id": "refund-row",
            "file": "policies/refunds.md",
            "title": "Refunds",
            "category": "policies",
            "status": "confirmed",
            "source": "owner",
            "tags": "refund",
            "heading": "Refund requests",
            "sensitive": True,
            "text": "Escalate for review.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "lancedb"
            db_dir.mkdir()
            marker = db_dir / "last-known-good"
            marker.write_text("keep")
            paths = self._patch_paths(root)
            with paths[0], paths[1]:
                with patch.object(index_kb, "load_rows", return_value=[row]), patch.object(
                    index_kb, "embed_passages", return_value=[[0.1]]
                ), patch.object(
                    index_kb.lancedb,
                    "connect",
                    side_effect=lambda path: FakeDB(Path(path), corrupt=True),
                ):
                    with self.assertRaisesRegex(SystemExit, "staged index content mismatch"):
                        index_kb.main()
            self.assertEqual(marker.read_text(), "keep")
            self.assertFalse(list(root.glob(".lancedb-staging-*")))


if __name__ == "__main__":
    unittest.main()
