from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE_TOOLS = ROOT / "deploy" / "warehouse"
SAFETY_PATCH = WAREHOUSE_TOOLS / "safety_patch.py"
APPLY = WAREHOUSE_TOOLS / "apply-safety-patch.sh"
CHECK = WAREHOUSE_TOOLS / "check-safety-patch.sh"
SYNC_SCRIPT = ROOT / "kb" / "sync-products.sh"
DEPLOY_RECEIVER = ROOT / "deploy" / "cd" / "buttonsbebe-deploy-receive.sh"


SERVER_FIXTURE = """
const PORT = process.env.PORT || 4000;
app.listen(PORT, () => console.log(`Warehouse on :${PORT}`));
""".lstrip()


SHOPIFY_FIXTURE = """
export async function shopifyGraphQL(query, variables = {}, attempt = 0) {
  if (!shopConfigured()) {
    throw new Error('not configured');
  }
  return fetch(endpoint(), { body: JSON.stringify({ query, variables }) });
}

async function listProducts() {
  return [];
}

const VARIANTS_UPDATE = `mutation SetPrices { productVariantsBulkUpdate { userErrors { message } } }`;

export async function setVariantPrices(productId, variants) {
  const data = await shopifyGraphQL(VARIANTS_UPDATE, {
    productId,
    variants,
  });
  return data;
}

const PRODUCT_STATUS = `mutation SetStatus { productUpdate { userErrors { message } } }`;

export async function setProductStatus(productId, status) {
  const want = String(status).toUpperCase();
  if (!['ACTIVE', 'DRAFT', 'ARCHIVED'].includes(want)) {
    throw new Error(`Unknown status: ${status}`);
  }
  return shopifyGraphQL(PRODUCT_STATUS, { productId, status: want });
}

const VARIANT_BARCODES = `mutation SetBarcodes { productVariantsBulkUpdate { userErrors { message } } }`;

export async function setVariantBarcodes(productId, variants) {
  const data = await shopifyGraphQL(VARIANT_BARCODES, {
    productId,
    variants,
  });
  return data;
}
""".lstrip()

V1_GUARD = """// Live-store content mutations are fail-closed.
export const contentWritesEnabled = () =>
  /^(1|true|yes|on)$/i.test(String(process.env.SHOPIFY_CONTENT_WRITES_ENABLED || '').trim());

function requireContentWrites(action) {
  if (!contentWritesEnabled()) throw new Error(`disabled: ${action}`);
}
"""


def make_fixture(root: Path) -> None:
    source = root / "src"
    source.mkdir(parents=True)
    (source / "server.js").write_text(SERVER_FIXTURE, encoding="utf-8")
    (source / "shopify.js").write_text(SHOPIFY_FIXTURE, encoding="utf-8")


def make_v1_hardened_fixture(root: Path) -> None:
    make_fixture(root)
    server_path = root / "src/server.js"
    server_path.write_text(
        server_path.read_text(encoding="utf-8").replace(
            "app.listen(PORT, () => console.log(`Warehouse on :${PORT}`));",
            "app.listen(PORT, '127.0.0.1', () => console.log(`Warehouse on 127.0.0.1:${PORT}`));",
        ),
        encoding="utf-8",
    )
    shopify_path = root / "src/shopify.js"
    source = shopify_path.read_text(encoding="utf-8")
    source = source.replace("const VARIANTS_UPDATE = `", V1_GUARD + "\nconst VARIANTS_UPDATE = `")
    source = source.replace(
        "export async function setVariantPrices(productId, variants) {\n",
        "export async function setVariantPrices(productId, variants) {\n"
        "  requireContentWrites('variant price update');\n",
    )
    source = source.replace(
        "export async function setProductStatus(productId, status) {\n",
        "export async function setProductStatus(productId, status) {\n"
        "  requireContentWrites('product status update');\n",
    )
    source = source.replace(
        "export async function setVariantBarcodes(productId, variants) {\n",
        "export async function setVariantBarcodes(productId, variants) {\n"
        "  requireContentWrites('variant barcode update');\n",
    )
    shopify_path.write_text(source, encoding="utf-8")


class WarehouseSafetyAssetTests(unittest.TestCase):
    def run_tool(self, tool: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(tool), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_patch_applies_to_fixture_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_fixture(root)

            first = self.run_tool(APPLY, str(root))
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("backup preserved", first.stdout)
            self.assertEqual(self.run_tool(CHECK, str(root)).returncode, 0)

            second = self.run_tool(APPLY, str(root))
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("Already hardened", second.stdout)
            backups = list((root / ".codex-safety-backups").iterdir())
            self.assertEqual(len(backups), 1)

            server = (root / "src/server.js").read_text(encoding="utf-8")
            shopify = (root / "src/shopify.js").read_text(encoding="utf-8")
            self.assertIn("app.listen(PORT, '127.0.0.1'", server)
            self.assertIn("SHOPIFY_CONTENT_WRITES_ENABLED || ''", shopify)
            self.assertIn("SHOPIFY_MUTATIONS_ENABLED || ''", shopify)
            self.assertIn("requireShopifyMutationApproval(query);", shopify)
            self.assertEqual(shopify.count("requireContentWrites('"), 3)

    def test_existing_v1_content_guards_upgrade_to_central_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_v1_hardened_fixture(root)

            result = self.run_tool(APPLY, str(root))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.run_tool(CHECK, str(root)).returncode, 0)
            source = (root / "src/shopify.js").read_text(encoding="utf-8")
            self.assertIn("SHOPIFY_MUTATIONS_ENABLED || ''", source)
            self.assertIn("requireShopifyMutationApproval(query);", source)
            self.assertEqual(source.count("requireContentWrites('"), 3)

    def test_dry_run_does_not_write_or_create_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_fixture(root)
            before = {
                path: path.read_bytes()
                for path in (root / "src/server.js", root / "src/shopify.js")
            }

            result = self.run_tool(APPLY, "--dry-run", str(root))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.count("DRY RUN: would patch"), result.stdout)
            self.assertFalse((root / ".codex-safety-backups").exists())
            for path, contents in before.items():
                self.assertEqual(path.read_bytes(), contents)

    def test_unexpected_source_aborts_without_touching_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_fixture(root)
            server_path = root / "src/server.js"
            server_path.write_text(
                SERVER_FIXTURE.replace(
                    "app.listen(PORT, () => console.log(`Warehouse on :${PORT}`));",
                    "app.listen(PORT, () => console.log('unexpected source'));",
                ),
                encoding="utf-8",
            )
            before = server_path.read_bytes()

            result = self.run_tool(APPLY, str(root))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SAFETY PATCH ABORTED", result.stderr)
            self.assertEqual(server_path.read_bytes(), before)
            self.assertFalse((root / ".codex-safety-backups").exists())

    def test_patch_tools_are_executable(self) -> None:
        for path in (APPLY, CHECK, WAREHOUSE_TOOLS / "rollback-safety-patch.sh"):
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR, path)

    def test_kb_sync_service_targets_an_executable_script(self) -> None:
        self.assertTrue(SYNC_SCRIPT.stat().st_mode & stat.S_IXUSR, SYNC_SCRIPT)
        self.assertEqual(SYNC_SCRIPT.stat().st_mode & 0o111, 0o111)
        for unit in (
            ROOT / "deploy/systemd/buttonsbebe-kb-sync.service",
        ):
            text = unit.read_text(encoding="utf-8")
            exec_lines = [line for line in text.splitlines() if line.startswith("ExecStart=")]
            self.assertEqual(len(exec_lines), 1, unit)
            self.assertTrue(exec_lines[0].endswith("/sync-products.sh\""), unit)

    def test_kb_sync_timer_copies_use_daily_cadence(self) -> None:
        for unit in (
            ROOT / "deploy/systemd/buttonsbebe-kb-sync.timer",
        ):
            text = unit.read_text(encoding="utf-8")
            self.assertIn("OnActiveSec=1d", text, unit)
            self.assertIn("OnUnitActiveSec=1d", text, unit)
            self.assertNotIn("=3d", text, unit)

    def test_deploy_receiver_restores_kb_sync_execute_mode(self) -> None:
        helper = (ROOT / "deploy/cd/source_release.py").read_text()
        self.assertIn("0o755 if path.suffix == '.sh' else 0o644", helper)
        self.assertIn("change['entry']['mode']", helper)


if __name__ == "__main__":
    unittest.main()
