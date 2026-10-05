from __future__ import annotations

import os
import io
import json
import tempfile
import sys
import types
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

fake_lancedb = sys.modules.setdefault("lancedb", types.ModuleType("lancedb"))
fake_lancedb.connect = None
fake_kb_lib = sys.modules.setdefault("kb_lib", types.ModuleType("kb_lib"))
fake_kb_lib.DB_DIR = Path("/tmp/buttonsbebe-search-test")
fake_kb_lib.PROMOTE_LOCK_PATH = fake_kb_lib.DB_DIR.parent / ".index_kb.promote.lock"
fake_kb_lib.TABLE = "kb"
fake_kb_lib.embed_query = lambda _query: [0.1]
fake_kb_lib.CATEGORY_WEIGHT = {
    "intents": 1.0,
    "faq": 1.0,
    "policies": 1.0,
    "tickets": 1.0,
    "products": 0.85,
    "shopify": 0.70,
}
fake_kb_lib.CONTENT_FOLDERS = list(fake_kb_lib.CATEGORY_WEIGHT)
fake_kb_lib._get_model = lambda: None

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
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for replacement in (
            patch.object(search_kb, "DB_DIR", Path(folder.name) / "lancedb"),
            patch.object(search_kb, "PROMOTE_LOCK_PATH", Path(folder.name) / ".index_kb.promote.lock"),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def _search(self, rows: list[dict], k: int) -> list[dict]:
        table = FakeTable(rows, rows)
        with patch.object(search_kb.lancedb, "connect", return_value=FakeDB(table)):
            with patch.object(search_kb, "_notice_results", return_value=[]):
                return search_kb.search("selected shipping but wants pickup", k=k)["results"]

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

    def test_candidate_pool_reaches_intent_beyond_twenty_duplicate_chunks(self) -> None:
        rows = [
            *(hit(f"repeat-{index}", "policies/repeated.md") for index in range(40)),
            hit("intent-17", "intents/intent-17-when-will-order-ship.md"),
        ]

        results = self._search(rows, k=2)

        self.assertEqual(
            [result["file"] for result in results],
            ["policies/repeated.md", "intents/intent-17-when-will-order-ship.md"],
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

    def test_shopify_background_cannot_outrank_same_query_policy(self) -> None:
        rows = [
            hit("shopify-refund", "shopify/shopify-refunds-how-they-work.md"),
            hit("store-policy", "policies/refunds-and-disputes.md"),
        ]

        results = self._search(rows, k=2)

        self.assertEqual(
            [result["file"] for result in results],
            [
                "policies/refunds-and-disputes.md",
                "shopify/shopify-refunds-how-they-work.md",
            ],
        )

    def test_exact_platform_identifier_surfaces_shopify_explainer_in_default_top_five(self) -> None:
        shopify = hit("shopify-status", "shopify/shopify-refunds-how-they-work.md")
        shopify["text"] = "Shopify financial status: partially_refunded means some payment was returned."
        policies = [hit(f"policy-{index}", f"policies/policy-{index}.md") for index in range(1, 6)]

        table = FakeTable(
            [shopify, *policies],
            [*policies, shopify],
        )
        with patch.object(search_kb.lancedb, "connect", return_value=FakeDB(table)):
            with patch.object(search_kb, "_notice_results", return_value=[]):
                results = search_kb.search("what is partially_refunded")["results"]

        files = [result["file"] for result in results]
        self.assertIn("shopify/shopify-refunds-how-they-work.md", files[:5])

    def test_platform_identifier_evidence_is_bounded(self) -> None:
        query = " ".join(f"field_{index}" for index in range(100))

        identifiers = search_kb._platform_identifiers(query)

        self.assertEqual(len(identifiers), search_kb.MAX_PLATFORM_IDENTIFIERS)
        self.assertTrue(
            all(
                len(identifier) <= search_kb.MAX_PLATFORM_IDENTIFIER_CHARS
                for identifier in identifiers
            )
        )

    def test_unknown_category_keeps_neutral_weight(self) -> None:
        score = search_kb._weighted_score(0.5, {"category": "future-category"})
        self.assertEqual(score, 0.5)

    def test_products_are_penalized_against_same_query_policy(self) -> None:
        rows = [
            hit("product", "products/red-dress.md"),
            hit("store-policy", "policies/sizing-guide.md"),
        ]

        results = self._search(rows, k=2)

        self.assertEqual(
            [result["file"] for result in results],
            ["policies/sizing-guide.md", "products/red-dress.md"],
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


@contextmanager
def _no_lock():
    yield


class MissingIndexSelfHealTests(unittest.TestCase):
    """3.9: crash-mid-swap heals from backup; first-run fails friendly, never raw."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        replacement = patch.object(search_kb, "DB_DIR", Path(folder.name) / "lancedb")
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_missing_index_without_backup_returns_empty(self) -> None:
        with patch.object(search_kb, "_index_read_lock") as lock, patch.object(
            search_kb, "_notice_results", return_value=[]
        ):
            lock.side_effect = _no_lock
            with patch.object(
                search_kb.lancedb, "connect", side_effect=FileNotFoundError("gone")
            ):
                outcome = search_kb.search("shipping")
                self.assertEqual(outcome["results"], [])
                self.assertEqual(outcome["index"], {"state": "unavailable", "codes": ["index_open_failed"]})

    def test_missing_index_restores_newest_backup(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="bb-heal-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        (tmp / ".lancedb-backup-older").mkdir()
        (tmp / ".lancedb-backup-older" / "origin.txt").write_text("older")
        newest = tmp / ".lancedb-backup-newest"
        newest.mkdir()
        (newest / "origin.txt").write_text("newest")
        # Creation can share one filesystem timestamp tick; make recency explicit.
        os.utime(tmp / ".lancedb-backup-older", (1000, 1000))
        os.utime(newest, (2000, 2000))
        table = FakeTable([hit("shipping", "policies/shipping.md")], [])
        connected_paths: list[str] = []
        # Prove the restored directory is what LanceDB opens — the marker file
        # pins which backup was restored, not just that some dir appeared.
        def connect(path):
            connected_paths.append(str(path))
            return FakeDB(table)

        with patch.object(search_kb, "_index_read_lock") as lock, patch.object(
            search_kb, "_notice_results", return_value=[]
        ), patch.object(search_kb, "DB_DIR", tmp / "lancedb"), patch.object(
            search_kb.lancedb, "connect", side_effect=connect
        ):
            lock.side_effect = _no_lock
            results = search_kb.search("shipping", k=1)["results"]
        self.assertTrue((tmp / "lancedb").is_dir())
        self.assertEqual((tmp / "lancedb" / "origin.txt").read_text(), "newest")
        self.assertFalse(newest.exists())
        self.assertEqual(connected_paths, [str(tmp / "lancedb")])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["file"], "policies/shipping.md")

    def test_heal_restores_newest_by_mtime_not_name(self) -> None:
        import os
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="bb-heal-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        # mkdtemp backup names carry random suffixes, so name order is not
        # age order: give the alphabetically-last backup the OLDER mtime and
        # expect the alphabetically-first (fresher) one to win.
        stale = tmp / ".lancedb-backup-zzz"
        stale.mkdir()
        (stale / "origin.txt").write_text("stale")
        fresh = tmp / ".lancedb-backup-aaa"
        fresh.mkdir()
        (fresh / "origin.txt").write_text("fresh")
        two_hours = 2 * 60 * 60
        os.utime(stale, (two_hours, two_hours))

        with patch.object(search_kb, "_index_read_lock") as lock, patch.object(
            search_kb, "_notice_results", return_value=[]
        ), patch.object(search_kb, "DB_DIR", tmp / "lancedb"), patch.object(
            search_kb.lancedb, "connect", side_effect=FileNotFoundError("gone")
        ):
            lock.side_effect = _no_lock
            search_kb.search("shipping", k=1)
        self.assertEqual((tmp / "lancedb" / "origin.txt").read_text(), "fresh")
        self.assertFalse(fresh.exists())

    def test_index_unavailable_still_returns_owner_notices(self) -> None:
        notice = {"title": "recall", "text": "stop answering sizing questions"}
        with patch.object(search_kb, "_index_read_lock") as lock, patch.object(
            search_kb, "_notice_results", return_value=[notice]
        ):
            lock.side_effect = _no_lock
            with patch.object(
                search_kb.lancedb, "connect", side_effect=FileNotFoundError("gone")
            ):
                outcome = search_kb.search("shipping")
                self.assertEqual(outcome["results"], [notice])
                self.assertEqual(outcome["notice_board"]["active_count"], 1)
                self.assertEqual(outcome["status"], "degraded")


class SearchOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.row = hit("shipping", "policies/shipping.md")
        self.table = FakeTable([self.row], [self.row])
        for replacement in (
            patch.object(search_kb, "_index_read_lock", _no_lock),
            patch.object(search_kb, "_heal_missing_index", return_value=None),
            patch.object(search_kb, "_notice_results", return_value=[]),
            patch.object(search_kb.lancedb, "connect", return_value=FakeDB(self.table)),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_all_notice_and_index_availability_combinations(self) -> None:
        notice = {"title": "NOTICE BOARD", "text": "Current owner guidance", "source": "owner"}
        for board_ok, index_ok, status in (
            (True, True, "healthy"), (False, True, "degraded"),
            (True, False, "degraded"), (False, False, "unavailable"),
        ):
            with self.subTest(board_ok=board_ok, index_ok=index_ok):
                search_kb._notice_results.side_effect = None if board_ok else ValueError("private-customer-text")
                search_kb._notice_results.return_value = [notice]
                search_kb.lancedb.connect.side_effect = None if index_ok else OSError("synthetic-secret")
                outcome = search_kb.search("shipping")
                self.assertEqual(outcome["status"], status)
                self.assertEqual(outcome["notice_board"]["active_count"], 1 if board_ok else None)
                self.assertEqual([r["title"] for r in outcome["results"]],
                                 (["NOTICE BOARD"] if board_ok else []) + (["policies/shipping.md"] if index_ok else []))

    def test_empty_success_has_healthy_sources_and_exact_zero_count(self) -> None:
        self.table.vector_rows = []
        self.table.keyword_rows = []
        outcome = search_kb.search("no matching topic")
        self.assertEqual(outcome, {"status": "healthy", "notice_board": {
            "state": "healthy", "active_count": 0, "codes": [], "operator_action": ""},
            "index": {"state": "healthy", "codes": []}, "results": []})

    def test_notice_count_matches_rows_from_one_snapshot(self) -> None:
        import notices_lib
        from datetime import datetime, timezone
        now = datetime(2026, 10, 4, tzinfo=timezone.utc)
        items = [{"id": "current", "text": "Current owner guidance", "expires_at": None},
                 {"id": "expired", "text": "Expired guidance", "expires_at": now.isoformat()}]
        with patch.object(search_kb, "_notice_results", notices_lib.as_search_results), \
                patch.object(notices_lib, "_read_items", return_value=items) as read, \
                patch.object(notices_lib, "_now", side_effect=[now]):
            outcome = search_kb.search("shipping")
        notice_rows = [r for r in outcome["results"] if r["category"] == "notices"]
        self.assertEqual(outcome["notice_board"]["active_count"], 1)
        self.assertEqual(len(notice_rows), 1)
        self.assertEqual(notice_rows[0]["text"].split("\n")[-1], "Current owner guidance")
        read.assert_called_once_with(strict=True)

    def test_lock_recovery_and_open_failures_preserve_notices(self) -> None:
        notice = {"title": "NOTICE BOARD", "text": "Owner guidance"}
        search_kb._notice_results.return_value = [notice]
        for name, code in (("_index_read_lock", "index_lock_failed"),
                           ("_heal_missing_index", "index_recovery_failed")):
            with self.subTest(boundary=name), patch.object(search_kb, name, side_effect=OSError("synthetic-secret")):
                outcome = search_kb.search("shipping")
                self.assertEqual(outcome["index"], {"state": "unavailable", "codes": [code]})
                self.assertEqual(outcome["results"], [notice])
        with patch.object(FakeDB, "open_table", side_effect=OSError("synthetic-secret")):
            self.assertEqual(search_kb.search("shipping")["index"],
                             {"state": "unavailable", "codes": ["index_open_failed"]})

    def test_embedding_or_vector_failure_keeps_keyword_matches(self) -> None:
        for boundary in ("embedding_failed", "vector_lookup_failed"):
            with self.subTest(boundary=boundary):
                real_search = self.table.search
                def lookup(query, query_type=None):
                    if query_type != "fts" and boundary == "vector_lookup_failed":
                        raise OSError("private-vector-text")
                    return real_search(query, query_type)
                with patch.object(search_kb, "embed_query", side_effect=OSError("secret") if boundary == "embedding_failed" else None,
                                  return_value=[0.1]), patch.object(self.table, "search", side_effect=lookup):
                    outcome = search_kb.search("shipping")
                self.assertEqual(outcome["index"], {"state": "degraded", "codes": [boundary]})
                self.assertEqual(outcome["results"][0]["file"], "policies/shipping.md")

    def test_keyword_failure_keeps_vector_matches_and_both_failures_are_unavailable(self) -> None:
        real_search = self.table.search
        def lookup(query, query_type=None):
            if query_type == "fts":
                raise OSError("private-keyword-text")
            return real_search(query, query_type)
        with patch.object(self.table, "search", side_effect=lookup):
            outcome = search_kb.search("shipping")
            self.assertEqual(outcome["index"], {"state": "degraded", "codes": ["keyword_lookup_failed"]})
            self.assertEqual(outcome["results"][0]["file"], "policies/shipping.md")
            with patch.object(search_kb, "embed_query", side_effect=OSError("secret")):
                failed = search_kb.search("shipping")
            self.assertEqual(failed["index"], {"state": "unavailable", "codes": ["embedding_failed", "keyword_lookup_failed"]})
            self.assertEqual(failed["results"], [])

    def test_unlock_failure_keeps_retrieved_passages_with_degraded_health(self) -> None:
        @contextmanager
        def failed_unlock():
            yield
            raise OSError("private-unlock-text")
        with patch.object(search_kb, "_index_read_lock", failed_unlock):
            outcome = search_kb.search("shipping")
        self.assertEqual(outcome["index"], {"state": "degraded", "codes": ["index_lock_failed"]})
        self.assertEqual(outcome["results"][0]["file"], "policies/shipping.md")

    def test_malformed_index_rows_do_not_escape_or_become_answer_text(self) -> None:
        self.table.vector_rows = self.table.keyword_rows = [{"text": "private-invalid-row"}]
        with redirect_stderr(io.StringIO()) as captured:
            outcome = search_kb.search("shipping")
        self.assertEqual(outcome["index"], {"state": "unavailable", "codes": ["index_rows_invalid"]})
        self.assertIn("kind=unexpected error_type=KeyError", captured.getvalue())
        self.assertNotIn("private-invalid-row", json.dumps(outcome) + captured.getvalue())

    def test_diagnostics_hide_exception_text_and_identify_unexpected_defects(self) -> None:
        captured = io.StringIO()
        search_kb._notice_results.side_effect = PermissionError("synthetic-secret customer@example.test")
        with redirect_stderr(captured), patch.object(search_kb, "embed_query", side_effect=TypeError("private-customer-text")):
            outcome = search_kb.search("shipping")
        self.assertIn("code=embedding_failed kind=unexpected error_type=TypeError", captured.getvalue())
        self.assertEqual(outcome["notice_board"]["codes"], ["notice_read_failed"])
        self.assertIn("Active owner overrides may still exist", outcome["notice_board"]["operator_action"])
        serialized = captured.getvalue() + json.dumps(outcome)
        for private in ("synthetic-secret", "customer@example.test", "private-customer-text"):
            self.assertNotIn(private, serialized)
        private_error_type = type("synthetic_private_error_type", (Exception,), {})
        search_kb._notice_results.side_effect = private_error_type("private-error")
        with redirect_stderr(io.StringIO()) as captured:
            search_kb.search("shipping")
        self.assertIn("kind=unexpected error_type=Exception", captured.getvalue())
        self.assertNotIn("synthetic_private_error_type", captured.getvalue())

    def test_policy_and_conflicting_learned_example_keep_actual_provenance_and_ranking(self) -> None:
        learned = {**hit("example", "tickets/example.md"), "text": "Allow returns after 30 days.",
                   "source": "learned-auto", "tags": "approved, exemplar, learned"}
        policy = {**hit("policy", "policies/returns.md"), "text": "Returns must be within 7 days.",
                  "source": "owner-policy", "tags": "returns"}
        self.table.vector_rows = self.table.keyword_rows = [learned, policy]
        results = search_kb.search("return window")["results"]
        self.assertEqual([(r["source"], r["tags"], r["text"]) for r in results], [
            ("learned-auto", "approved, exemplar, learned", "Allow returns after 30 days."),
            ("owner-policy", "returns", "Returns must be within 7 days.")])

    def test_legacy_rows_have_unknown_provenance(self) -> None:
        result = search_kb.search("shipping")["results"][0]
        self.assertEqual((result["source"], result["tags"]), ("", ""))

    def test_cli_reports_unavailable_board_before_readable_passages(self) -> None:
        search_kb._notice_results.side_effect = ValueError("private-customer-text")
        captured = io.StringIO()
        with patch.object(sys, "argv", ["search_kb.py", "shipping"]), redirect_stdout(captured):
            search_kb.main()
        text = captured.getvalue()
        self.assertTrue(text.startswith("notice_board: unavailable (notice_store_invalid)"))
        self.assertIn("policies/shipping.md", text)
        self.assertNotIn("private-customer-text", text)


class MCPOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_serialized_tool_preserves_health_provenance_and_read_only_contract(self) -> None:
        import kb_mcp_server
        tools = await kb_mcp_server.mcp.list_tools()
        self.assertEqual([tool.name for tool in tools], ["search_kb"])
        self.assertTrue(tools[0].annotations.readOnlyHint)
        self.assertFalse(tools[0].annotations.destructiveHint)
        self.assertTrue(tools[0].annotations.idempotentHint)
        learned = {**hit("example", "tickets/example.md"), "source": "learned-auto", "tags": "exemplar, learned"}
        table = FakeTable([learned], [learned])
        with patch.object(search_kb, "_index_read_lock", _no_lock), \
                patch.object(search_kb, "_heal_missing_index", return_value=None), \
                patch.object(search_kb, "_notice_results", return_value=[]), \
                patch.object(search_kb.lancedb, "connect", return_value=FakeDB(table)):
            content, structured = await kb_mcp_server.mcp.call_tool("search_kb", {"query": "return policy"})
        serialized = json.loads(content[0].text)
        self.assertEqual(serialized, structured)
        self.assertEqual(serialized["status"], "healthy")
        self.assertEqual(serialized["notice_board"]["active_count"], 0)
        self.assertEqual(serialized["results"][0]["source"], "learned-auto")
        self.assertEqual(serialized["results"][0]["tags"], "exemplar, learned")
        with patch.object(search_kb, "_index_read_lock", _no_lock), \
                patch.object(search_kb, "_heal_missing_index", return_value=None), \
                patch.object(search_kb, "_notice_results", side_effect=PermissionError("synthetic-secret")), \
                patch.object(search_kb.lancedb, "connect", side_effect=OSError("customer@example.test")), \
                redirect_stderr(io.StringIO()) as diagnostics:
            content, structured = await kb_mcp_server.mcp.call_tool("search_kb", {"query": "return policy"})
        self.assertEqual(json.loads(content[0].text), structured)
        self.assertEqual(structured["status"], "unavailable")
        self.assertEqual(structured["notice_board"]["active_count"], None)
        self.assertEqual(structured["results"], [])
        self.assertNotIn("synthetic-secret", content[0].text + diagnostics.getvalue())
        self.assertNotIn("customer@example.test", content[0].text + diagnostics.getvalue())


if __name__ == "__main__":
    unittest.main()
