from __future__ import annotations

import fcntl
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
sys.modules.setdefault("kb_lib", fake_kb_lib)

import index_kb  # noqa: E402


class FakeTable:
    def __init__(self, root: Path, fail: bool = False) -> None:
        self.root = root
        self.fail = fail

    def add(self, rows: list[dict]) -> None:
        (self.root / "rows.txt").write_text(str(len(rows)))

    def create_fts_index(self, *_args, **_kwargs) -> None:
        if self.fail:
            raise RuntimeError("simulated FTS failure")
        (self.root / "fts.ready").write_text("ok")


class FakeDB:
    def __init__(self, root: Path, fail: bool = False) -> None:
        self.root = root
        self.fail = fail

    def create_table(self, *_args, **_kwargs) -> FakeTable:
        return FakeTable(self.root, self.fail)


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
            paths = self._patch_paths(root)
            with paths[0], paths[1]:
                with patch.object(index_kb, "load_rows", return_value=[{"text": "one"}]), patch.object(index_kb, "embed_passages", return_value=[[0.1]]), patch.object(index_kb.lancedb, "connect", side_effect=fake_db):
                    index_kb.main()
            self.assertTrue((db_dir / "rows.txt").exists())
            self.assertTrue((db_dir / "fts.ready").exists())
            self.assertFalse((db_dir / "last-known-good").exists())
            self.assertFalse(list(root.glob(".lancedb-backup-*")))


if __name__ == "__main__":
    unittest.main()
