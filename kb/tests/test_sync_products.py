from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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
    "options": [],
}


class TestSyncProducts(unittest.TestCase):
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

    def test_bulk_polling_has_a_hard_bound(self) -> None:
        started = {"data": {"bulkOperationRunQuery": {"userErrors": []}}}
        processing = {"data": {"currentBulkOperation": {"status": "RUNNING", "objectCount": 0}}}
        with patch.object(sync_products, "gql", side_effect=[started, processing, processing]) as gql:
            with patch.object(sync_products, "time") as clock:
                with patch.object(sync_products, "MAX_BULK_POLLS", 2):
                    with self.assertRaisesRegex(SystemExit, "after 2 polls"):
                        sync_products.run_bulk_export("shop.myshopify.com", "2026-04", "token", "status:active")
        self.assertEqual(gql.call_count, 3)
        self.assertEqual(clock.sleep.call_count, 2)

    def test_product_query_is_escaped_before_graphql_interpolation(self) -> None:
        responses = [
            {"data": {"bulkOperationRunQuery": {"userErrors": []}}},
            {"data": {"currentBulkOperation": {"status": "COMPLETED", "url": "https://example.test/export"}}},
        ]
        with patch.object(sync_products, "gql", side_effect=responses) as gql:
            with patch.object(sync_products, "time") as clock:
                self.assertEqual(
                    sync_products.run_bulk_export("shop.myshopify.com", "2026-04", "token", 'title:"red"'),
                    "https://example.test/export",
                )
        self.assertIn('title:\\"red\\"', gql.call_args_list[0].args[3])
        clock.sleep.assert_called_once_with(4)


if __name__ == "__main__":
    unittest.main()
