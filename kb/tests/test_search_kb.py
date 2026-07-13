from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

fake_lancedb = sys.modules.setdefault("lancedb", types.ModuleType("lancedb"))
fake_lancedb.connect = None
fake_kb_lib = sys.modules.setdefault("kb_lib", types.ModuleType("kb_lib"))
fake_kb_lib.DB_DIR = Path("/tmp/buttonsbebe-search-test")
fake_kb_lib.TABLE = "kb"
fake_kb_lib.embed_query = lambda _query: [0.1]

import search_kb  # noqa: E402


def hit(row_id: str, file: str) -> dict:
    return {
        "id": row_id,
        "file": file,
        "title": file,
        "category": file.split("/", 1)[0],
        "status": "confirmed",
        "sensitive": False,
        "heading": row_id,
        "text": row_id,
    }


class FakeQuery:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def metric(self, _metric: str):
        return self

    def limit(self, count: int):
        self.rows = self.rows[:count]
        return self

    def to_list(self) -> list[dict]:
        return self.rows


class FakeTable:
    def __init__(self, vector_rows: list[dict], keyword_rows: list[dict]) -> None:
        self.vector_rows = vector_rows
        self.keyword_rows = keyword_rows

    def search(self, _query, query_type=None):
        rows = self.keyword_rows if query_type == "fts" else self.vector_rows
        return FakeQuery(list(rows))


class FakeDB:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def open_table(self, _name: str) -> FakeTable:
        return self.table


class SearchDiversificationTests(unittest.TestCase):
    def _search(self, rows: list[dict], k: int) -> list[dict]:
        table = FakeTable(rows, rows)
        with patch.object(search_kb.lancedb, "connect", return_value=FakeDB(table)):
            with patch.object(search_kb, "_notice_results", return_value=[]):
                return search_kb.search("selected shipping but wants pickup", k=k)

    def test_repeated_policy_chunks_do_not_crowd_out_exact_intents(self) -> None:
        rows = [
            *(hit(f"return-{index}", "policies/return-and-exchange-policy.md") for index in range(6)),
            hit("intent-03", "intents/intent-03-shipping-to-pickup.md"),
            hit("intent-04", "intents/intent-04-sizing-help-multiple-items.md"),
            hit("shipping", "policies/shipping-policy.md"),
        ]

        results = self._search(rows, k=4)

        self.assertEqual(
            [result["file"] for result in results],
            [
                "policies/return-and-exchange-policy.md",
                "intents/intent-03-shipping-to-pickup.md",
                "intents/intent-04-sizing-help-multiple-items.md",
                "policies/shipping-policy.md",
            ],
        )

    def test_second_chunks_fill_remaining_slots_after_unique_files(self) -> None:
        rows = [
            hit("returns-1", "policies/returns.md"),
            hit("returns-2", "policies/returns.md"),
            hit("shipping-1", "policies/shipping.md"),
            hit("shipping-2", "policies/shipping.md"),
        ]

        results = self._search(rows, k=4)

        self.assertEqual(
            [result["heading"] for result in results],
            ["returns-1", "shipping-1", "returns-2", "shipping-2"],
        )

    def test_search_holds_read_lock_while_opening_and_querying_index(self) -> None:
        state = {"locked": False}
        table = FakeTable([hit("shipping", "policies/shipping.md")], [])

        @contextmanager
        def tracked_lock():
            state["locked"] = True
            try:
                yield
            finally:
                state["locked"] = False

        def connect(_path):
            self.assertTrue(state["locked"])
            return FakeDB(table)

        with patch.object(search_kb, "_index_read_lock", tracked_lock), patch.object(
            search_kb.lancedb, "connect", side_effect=connect
        ), patch.object(search_kb, "_notice_results", return_value=[]):
            search_kb.search("shipping", k=1)

        self.assertFalse(state["locked"])

    def test_zero_result_limit_returns_no_index_hits(self) -> None:
        rows = [hit("shipping", "policies/shipping.md")]
        self.assertEqual(self._search(rows, k=0), [])


if __name__ == "__main__":
    unittest.main()
