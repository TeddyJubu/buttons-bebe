from __future__ import annotations

import sys
import tempfile
import types
import unittest
import fcntl
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
fake_requests = types.ModuleType("requests")
fake_requests.get = None
fake_requests.post = None
sys.modules.setdefault("requests", fake_requests)

import sync_products  # noqa: E402


PRODUCT = {
    "id": "gid://shopify/Product/1",
    "title": "Red Dress",
    "handle": "red-dress",
    "productType": "Dress",
    "vendor": "Buttons Bebe",
    "totalInventory": 2,
    "status": "ACTIVE",
    "options": [],
}


class TestSyncProducts(unittest.TestCase):
    def test_numeric_vendor_slug_is_written_as_a_string_tag(self):
        product = {
            "title": "Basic Tee",
            "handle": "basic-tee",
            "vendor": "1758",
            "productType": "Tee",
            "status": "ACTIVE",
        }

        _filename, body = sync_products._render_product(
            product, {}, "gid://shopify/Product/1"
        )
        tags_line = next(line for line in body.splitlines() if line.startswith("tags:"))

        self.assertEqual(tags_line, 'tags: ["product", "tee", "1758"]')

    def test_duplicate_or_unrecognized_export_records_are_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "duplicate Shopify product"):
            sync_products.split_records([PRODUCT, dict(PRODUCT)])
        with self.assertRaisesRegex(SystemExit, "unrecognized Shopify export"):
            sync_products.split_records([{"unexpected": "record"}])

    def test_empty_export_preserves_existing_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            products_dir = Path(tmp) / "products"
            products_dir.mkdir()
            old = products_dir / "product-existing.md"
            old.write_text("old corpus")
            with patch.object(sync_products, "PRODUCTS_DIR", products_dir):
                with self.assertRaisesRegex(SystemExit, "empty export"):
                    sync_products.write_files({}, {})
            self.assertEqual(old.read_text(), "old corpus")

    def test_malformed_product_preserves_existing_corpus(self) -> None:
        malformed = {"id": PRODUCT["id"], "title": "", "handle": ""}
        with tempfile.TemporaryDirectory() as tmp:
            products_dir = Path(tmp) / "products"
            products_dir.mkdir()
            old = products_dir / "product-existing.md"
            old.write_text("old corpus")
            with patch.object(sync_products, "PRODUCTS_DIR", products_dir):
                with self.assertRaisesRegex(SystemExit, "missing title"):
                    sync_products.write_files({PRODUCT["id"]: malformed}, {})
            self.assertEqual(old.read_text(), "old corpus")

    def test_successful_export_replaces_stale_files_after_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            products_dir = Path(tmp) / "products"
            products_dir.mkdir()
            (products_dir / "product-stale.md").write_text("stale")
            with patch.object(sync_products, "PRODUCTS_DIR", products_dir):
                count = sync_products.write_files({PRODUCT["id"]: PRODUCT}, {})
            self.assertEqual(count, 1)
            self.assertFalse((products_dir / "product-stale.md").exists())
            self.assertIn("Red Dress", (products_dir / "product-red-dress.md").read_text())

    def test_failed_commit_restores_the_previous_corpus(self) -> None:
        second = {**PRODUCT, "id": "gid://shopify/Product/2", "title": "Blue Dress", "handle": "blue-dress"}
        with tempfile.TemporaryDirectory() as tmp:
            products_dir = Path(tmp) / "products"
            products_dir.mkdir()
            old = products_dir / "product-red-dress.md"
            old.write_text("old corpus")
            real_replace = sync_products.os.replace
            calls = 0

            def flaky_replace(source: str, destination: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated disk-full")
                real_replace(source, destination)

            with patch.object(sync_products, "PRODUCTS_DIR", products_dir):
                with patch.object(sync_products.os, "replace", side_effect=flaky_replace):
                    with self.assertRaises(OSError):
                        sync_products.write_files(
                            {PRODUCT["id"]: PRODUCT, second["id"]: second},
                            {},
                        )
            self.assertEqual(old.read_text(), "old corpus")
            self.assertFalse((products_dir / "product-blue-dress.md").exists())

    def test_failed_rebuild_restores_previous_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            products_dir = root / "products"
            products_dir.mkdir()
            old = products_dir / "product-existing.md"
            old.write_text("old corpus")

            def fail_rebuild() -> None:
                self.assertTrue((products_dir / "product-red-dress.md").exists())
                raise RuntimeError("simulated index failure")

            with patch.object(sync_products, "PRODUCTS_DIR", products_dir), patch.object(
                sync_products, "INDEX_LOCK_PATH", root / ".index_kb.lock"
            ):
                with self.assertRaisesRegex(RuntimeError, "index failure"):
                    sync_products.write_files(
                        {PRODUCT["id"]: PRODUCT},
                        {},
                        rebuild_index=fail_rebuild,
                    )

            self.assertEqual(old.read_text(), "old corpus")
            self.assertFalse((products_dir / "product-red-dress.md").exists())

    def test_suspiciously_small_export_preserves_existing_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            products_dir = Path(tmp) / "products"
            products_dir.mkdir()
            for index in range(10):
                (products_dir / f"product-existing-{index}.md").write_text("old corpus")
            with patch.object(sync_products, "PRODUCTS_DIR", products_dir):
                with self.assertRaisesRegex(SystemExit, "catalog shrink"):
                    sync_products.write_files({PRODUCT["id"]: PRODUCT}, {})
            self.assertEqual(len(list(products_dir.glob("product-*.md"))), 10)

    def test_active_export_rejects_inactive_or_orphaned_records(self) -> None:
        inactive = {**PRODUCT, "status": "DRAFT"}
        with tempfile.TemporaryDirectory() as tmp:
            products_dir = Path(tmp) / "products"
            products_dir.mkdir()
            with patch.object(sync_products, "PRODUCTS_DIR", products_dir):
                with self.assertRaisesRegex(SystemExit, "non-active product"):
                    sync_products.write_files(
                        {inactive["id"]: inactive},
                        {},
                        require_active=True,
                    )
                with self.assertRaisesRegex(SystemExit, "orphan variants"):
                    sync_products.write_files(
                        {PRODUCT["id"]: PRODUCT},
                        {"gid://shopify/Product/missing": [{"title": "orphan"}]},
                    )

    def test_sync_lock_contention_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / ".products-sync.lock"
            with lock_path.open("w") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(sync_products, "SYNC_LOCK_PATH", lock_path):
                    with self.assertRaisesRegex(SystemExit, "already running"):
                        with sync_products._sync_lock():
                            pass

    def test_bulk_polling_has_a_hard_bound(self) -> None:
        started = {"data": {"bulkOperationRunQuery": {"bulkOperation": {"id": "export-1"}, "userErrors": []}}}
        processing = {"data": {"currentBulkOperation": {"id": "export-1", "status": "RUNNING", "objectCount": 0}}}
        with patch.object(sync_products, "gql", side_effect=[started, processing, processing]) as gql:
            with patch.object(sync_products, "time") as clock:
                with patch.object(sync_products, "MAX_BULK_POLLS", 2):
                    with self.assertRaisesRegex(SystemExit, "after 2 polls"):
                        sync_products.run_bulk_export("shop.myshopify.com", "2026-04", "token", "status:active")
        self.assertEqual(gql.call_count, 3)
        self.assertEqual(clock.sleep.call_count, 2)

    def test_product_query_is_escaped_before_graphql_interpolation(self) -> None:
        responses = [
            {"data": {"bulkOperationRunQuery": {"bulkOperation": {"id": "export-1"}, "userErrors": []}}},
            {"data": {"currentBulkOperation": {"id": "export-1", "status": "COMPLETED", "url": "https://example.test/export"}}},
        ]
        with patch.object(sync_products, "gql", side_effect=responses) as gql:
            with patch.object(sync_products, "time") as clock:
                self.assertEqual(
                    sync_products.run_bulk_export("shop.myshopify.com", "2026-04", "token", 'title:"red"'),
                    "https://example.test/export",
                )
        self.assertIn('title:\\"red\\"', gql.call_args_list[0].args[3])
        clock.sleep.assert_called_once_with(4)

    def test_bulk_export_rejects_missing_start_and_foreign_poll_ids(self) -> None:
        started = {"data": {"bulkOperationRunQuery": {"bulkOperation": {"id": "export-1"}, "userErrors": []}}}
        for missing in (None, "", " ", 17):
            response = {"data": {"bulkOperationRunQuery": {"bulkOperation": {"id": missing}, "userErrors": []}}}
            with patch.object(sync_products, "gql", return_value=response) as gql:
                with self.assertRaisesRegex(SystemExit, "no operation ID"):
                    sync_products.run_bulk_export("shop.myshopify.com", "2026-04", "synthetic", "status:active")
                self.assertEqual(gql.call_count, 1)
        for operation_id in (None, "", "export-foreign"):
            poll = {"data": {"currentBulkOperation": {"id": operation_id, "status": "COMPLETED", "url": "https://example.test/foreign"}}}
            with patch.object(sync_products, "gql", side_effect=[started, poll]), patch.object(sync_products, "time"):
                with self.assertRaisesRegex(SystemExit, "does not match"):
                    sync_products.run_bulk_export("shop.myshopify.com", "2026-04", "synthetic", "status:active")

    def test_gql_blocks_non_bulk_mutations_and_multi_operation_documents(self) -> None:
        blocked_documents = [
            """
                # Named mutations cannot bypass the read-only gate.
                mutation UpdateProduct {
                    productUpdate(product: {id: \"gid://shopify/Product/1\"}) {
                        product { id }
                    }
                }
            """,
            '''
                mutation SpoofBulk {
                    bulkOperationRunQuery(query: """{ products { edges { node { id } } } }""") {
                        bulkOperation { id }
                    }
                    productDelete(input: {id: \"gid://shopify/Product/1\"}) { deletedProductId }
                }
            ''',
            """
                query First { shop { name } }
                query Second { shop { name } }
            """,
        ]
        with patch.object(sync_products.requests, "post") as post:
            for document in blocked_documents:
                with self.subTest(document=document):
                    with self.assertRaisesRegex(ValueError, "mutation|operation|GraphQL"):
                        sync_products.gql("shop.myshopify.com", "2026-04", "token", document)
            post.assert_not_called()

    def test_gql_allows_named_queries_and_exact_bulk_read_wrapper(self) -> None:
        response = Mock()
        response.json.return_value = {"data": {"ok": True}}
        query = """
            # Comments and operation names are valid and harmless.
            query ProductRead {
                products(first: 1, query: "mutation") { edges { node { id } } }
            }
        """
        bulk_read = '''
            # This is the one Shopify mutation permitted for the read-only sync.
            mutation StartBulk {
                bulkOperationRunQuery(
                    query: """{ products { edges { node { id title } } } }"""
                ) {
                    bulkOperation { id status }
                    userErrors { field message }
                }
            }
        '''
        with patch.object(sync_products.requests, "post", return_value=response) as post:
            self.assertEqual(
                sync_products.gql("shop.myshopify.com", "2026-04", "token", query),
                {"data": {"ok": True}},
            )
            self.assertEqual(
                sync_products.gql("shop.myshopify.com", "2026-04", "token", bulk_read),
                {"data": {"ok": True}},
            )
        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
