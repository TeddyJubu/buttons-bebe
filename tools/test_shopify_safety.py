from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.shopify_safety import (
    CommandResult,
    HttpResponse,
    SafetyConfig,
    SafetyDependencies,
    inspect_shopify_write_guard,
    parse_listening_endpoints,
    run_monitor,
)


GUARDED_SHOPIFY_SOURCE = r"""
const VARIANT_PRICES = `mutation SetPrices { productVariantsBulkUpdate { userErrors { message } } }`;
const PRODUCT_STATUS = `mutation SetStatus { productUpdate { userErrors { message } } }`;
const VARIANT_BARCODES = `mutation SetBarcodes { productVariantsBulkUpdate { userErrors { message } } }`;
export const contentWritesEnabled = () =>
  /^(1|true|yes|on)$/i.test(String(process.env.SHOPIFY_CONTENT_WRITES_ENABLED || '').trim());
export const shopifyMutationsEnabled = () =>
  /^(1|true|yes|on)$/i.test(String(process.env.SHOPIFY_MUTATIONS_ENABLED || '').trim());
function requireShopifyMutationApproval(document) {
  if (/mutation/i.test(document) && !shopifyMutationsEnabled()) throw new Error('disabled');
}
function requireContentWrites(action) {
  if (!contentWritesEnabled()) throw new Error(`disabled: ${action}`);
}
export async function shopifyGraphQL(query) {
  requireShopifyMutationApproval(query);
  return fetch('https://example.invalid');
}
export async function setVariantPrices() {
  requireContentWrites('price');
  return shopifyGraphQL(VARIANT_PRICES);
}
export async function setProductStatus() {
  requireContentWrites('status');
  return shopifyGraphQL(PRODUCT_STATUS);
}
export async function setVariantBarcodes() {
  requireContentWrites('barcode');
  return shopifyGraphQL(VARIANT_BARCODES);
}
"""


def expected_webhooks(base: str) -> dict[str, str]:
    base = base.rstrip("/")
    return {
        "PRODUCTS_CREATE": f"{base}/api/shopify/webhook/products-create",
        "PRODUCTS_UPDATE": f"{base}/api/shopify/webhook/products-update",
        "PRODUCTS_DELETE": f"{base}/api/shopify/webhook/products-delete",
    }


class FakeRunner:
    def __init__(self, *, runtime_env: bytes = b"PATH=/usr/bin\0") -> None:
        self.runtime_env = runtime_env
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> CommandResult:
        self.calls.append(argv)
        if argv[:3] == ("systemctl", "is-active", "--quiet"):
            return CommandResult(0)
        if argv[:3] == ("systemctl", "show", "-p"):
            return CommandResult(0, "42\n")
        if argv == ("ss", "-H", "-ltn"):
            return CommandResult(0, "LISTEN 0 511 127.0.0.1:4000 0.0.0.0:*\n")
        raise AssertionError(f"unexpected command: {argv}")


class FakeHTTP:
    def __init__(self, *, webhooks: dict[str, str] | None = None) -> None:
        self.webhooks = webhooks or expected_webhooks("https://wh.buttonsbebe.com")
        self.calls: list[str] = []

    def __call__(self, url: str, _timeout: float) -> HttpResponse:
        self.calls.append(url)
        if url == "http://127.0.0.1:4000/api/shopify/webhooks":
            body = json.dumps(
                {
                    "live": [
                        {"topic": topic, "url": callback}
                        for topic, callback in self.webhooks.items()
                    ],
                    "secretSet": True,
                }
            )
            return HttpResponse(200, {}, body)
        if url == "https://wh.buttonsbebe.com/api/health":
            return HttpResponse(401, {"WWW-Authenticate": 'Basic realm="restricted"'})
        if url == "https://wh.buttonsbebe.com/api/shopify/webhook/products-update":
            return HttpResponse(404, {"X-Powered-By": "Express"}, "Cannot GET")
        if url == "https://support.buttonsbebe.com/health":
            return HttpResponse(200)
        raise AssertionError(f"unexpected URL: {url}")


class ShopifySafetyTests(unittest.TestCase):
    def make_fixture(
        self,
        *,
        runtime_env: bytes = b"PATH=/usr/bin\0",
        webhooks: dict[str, str] | None = None,
        source: str = GUARDED_SHOPIFY_SOURCE,
    ) -> tuple[SafetyConfig, SafetyDependencies, tempfile.TemporaryDirectory[str]]:
        temp_dir = tempfile.TemporaryDirectory()
        warehouse_root = Path(temp_dir.name) / "warehouse"
        (warehouse_root / "src").mkdir(parents=True)
        source_path = warehouse_root / "src/shopify.js"
        source_path.write_text(source, encoding="utf-8")
        server_source = "import { shopifyGraphQL } from './shopify.js';\n"
        server_path = warehouse_root / "src/server.js"
        server_path.write_text(server_source, encoding="utf-8")
        warehouse_manifest = warehouse_root / ".codex-safety-source.sha256"
        warehouse_manifest.write_text(
            "\n".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(warehouse_root)}"
                for path in (server_path, source_path)
            )
            + "\n",
            encoding="utf-8",
        )
        caddy_root = Path(temp_dir.name) / "caddy"
        (caddy_root / "sites").mkdir(parents=True)
        caddy_files = {
            "Caddyfile": (
                "import sites/support.caddy\n"
                "import sites/exchange.caddy\n"
                "import sites/warehouse.caddy\n"
                "import sites/receiving.caddy\n"
            ),
            "sites/support.caddy": "support.buttonsbebe.com { respond 200 }\n",
            "sites/exchange.caddy": "exchange.buttonsbebe.com { respond 200 }\n",
            "sites/warehouse.caddy": "wh.buttonsbebe.com { respond 200 }\n",
            "sites/receiving.caddy": "support.buttonsbebe.com:8443 { respond 200 }\n",
        }
        manifest_lines = []
        for relative, contents in caddy_files.items():
            path = caddy_root / relative
            path.write_text(contents, encoding="utf-8")
            manifest_lines.append(
                f"{hashlib.sha256(contents.encode()).hexdigest()}  {relative}"
            )
        manifest_path = caddy_root / "buttonsbebe-caddy.sha256"
        manifest_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
        runner = FakeRunner(runtime_env=runtime_env)
        http = FakeHTTP(webhooks=webhooks)
        def read_text(path: Path) -> str:
            if path.parts[-2:] == ("42", "stat"):
                return "42 (node) S " + " ".join(["0"] * 18 + ["50000"]) + "\n"
            if path.name == "uptime" and "proc" in path.parts:
                return "1000.00 0.00\n"
            return path.read_text(encoding="utf-8")

        deps = SafetyDependencies(
            run_command=runner,
            http_get=http,
            read_text=read_text,
            read_bytes=lambda path: (
                runtime_env if "proc" in path.parts else path.read_bytes()
            ),
            stat_mtime=lambda _path: 1000.0,
            now=lambda: 2000.0,
            clock_ticks=100,
        )
        config = SafetyConfig(
            shopify_source_path=source_path,
            caddy_manifest_path=manifest_path,
            warehouse_source_manifest_path=warehouse_manifest,
        )
        return config, deps, temp_dir

    def test_parse_listening_endpoints_finds_loopback_and_public_bindings(self) -> None:
        output = (
            "LISTEN 0 511 127.0.0.1:4000 0.0.0.0:*\n"
            "LISTEN 0 511 10.0.0.4:4000 0.0.0.0:*\n"
            "LISTEN 0 511 [::1]:4000 [::]:*\n"
        )
        self.assertEqual(
            parse_listening_endpoints(output, 4000),
            ("127.0.0.1", "10.0.0.4", "::1"),
        )

    def test_guard_inspection_accepts_all_fail_closed_mutations(self) -> None:
        ok, detail = inspect_shopify_write_guard(GUARDED_SHOPIFY_SOURCE)
        self.assertTrue(ok, detail)
        self.assertIn("all detected", detail)

    def test_guard_inspection_rejects_unprotected_mutation(self) -> None:
        source = GUARDED_SHOPIFY_SOURCE.replace(
            "  requireContentWrites('barcode');\n  return shopifyGraphQL(VARIANT_BARCODES);",
            "  return shopifyGraphQL(VARIANT_BARCODES);",
        )
        ok, detail = inspect_shopify_write_guard(source)
        self.assertFalse(ok)
        self.assertIn("unguarded", detail)

    def test_healthy_report_is_green_and_uses_only_expected_get_probes(self) -> None:
        config, deps, temp_dir = self.make_fixture()
        self.addCleanup(temp_dir.cleanup)
        report = run_monitor(config, deps)
        self.assertTrue(report.ok, report.as_dict())
        self.assertEqual(len(report.checks), 10)
        self.assertTrue(all(check.ok for check in report.checks))
        http = deps.http_get
        self.assertTrue(all(url.startswith(("http://", "https://")) for url in http.calls))  # type: ignore[attr-defined]

    def test_enabled_runtime_flag_fails_closed(self) -> None:
        config, deps, temp_dir = self.make_fixture(runtime_env=b"SHOPIFY_CONTENT_WRITES_ENABLED=true\0")
        self.addCleanup(temp_dir.cleanup)
        report = run_monitor(config, deps)
        runtime = next(check for check in report.checks if check.name == "shopify.write_flags_runtime")
        self.assertFalse(runtime.ok)
        self.assertEqual(runtime.status, "DRIFT")
        self.assertNotIn("true", runtime.detail.lower())

    def test_enabled_central_mutation_flag_fails_closed(self) -> None:
        config, deps, temp_dir = self.make_fixture(
            runtime_env=b"SHOPIFY_MUTATIONS_ENABLED=1\0"
        )
        self.addCleanup(temp_dir.cleanup)
        report = run_monitor(config, deps)
        runtime = next(check for check in report.checks if check.name == "shopify.write_flags_runtime")
        self.assertFalse(runtime.ok)
        self.assertEqual(runtime.status, "DRIFT")

    def test_non_loopback_listener_fails_closed(self) -> None:
        config, deps, temp_dir = self.make_fixture()
        self.addCleanup(temp_dir.cleanup)
        original = deps.run_command

        def runner(argv: tuple[str, ...]) -> CommandResult:
            result = original(argv)
            if argv == ("ss", "-H", "-ltn"):
                return CommandResult(0, "LISTEN 0 511 0.0.0.0:4000 0.0.0.0:*\n")
            return result

        deps = SafetyDependencies(
            runner,
            deps.http_get,
            deps.read_text,
            deps.read_bytes,
            deps.stat_mtime,
            deps.now,
            deps.clock_ticks,
        )
        report = run_monitor(config, deps)
        port = next(check for check in report.checks if check.name == "warehouse.port_loopback")
        self.assertFalse(port.ok)

    def test_public_health_exposure_fails_closed(self) -> None:
        config, deps, temp_dir = self.make_fixture()
        self.addCleanup(temp_dir.cleanup)
        original = deps.http_get

        def http_get(url: str, timeout: float) -> HttpResponse:
            if url == "https://wh.buttonsbebe.com/api/health":
                return HttpResponse(200)
            return original(url, timeout)

        deps = SafetyDependencies(
            deps.run_command,
            http_get,
            deps.read_text,
            deps.read_bytes,
            deps.stat_mtime,
            deps.now,
            deps.clock_ticks,
        )
        report = run_monitor(config, deps)
        health = next(check for check in report.checks if check.name == "warehouse.https_route")
        self.assertFalse(health.ok)
        self.assertIn("protected", health.detail)

    def test_webhook_set_must_be_exact_and_callback_urls_must_match(self) -> None:
        callbacks = expected_webhooks("https://wh.buttonsbebe.com")
        callbacks["PRODUCTS_UPDATE"] = "https://wh.buttonsbebe.com/wrong"
        config, deps, temp_dir = self.make_fixture(webhooks=callbacks)
        self.addCleanup(temp_dir.cleanup)
        report = run_monitor(config, deps)
        webhooks = next(check for check in report.checks if check.name == "shopify.webhooks_exact")
        self.assertFalse(webhooks.ok)
        self.assertNotIn("wrong", webhooks.detail)

    def test_local_webhook_endpoint_cannot_be_repointed_to_public_host(self) -> None:
        config, deps, temp_dir = self.make_fixture()
        self.addCleanup(temp_dir.cleanup)
        unsafe = SafetyConfig(
            shopify_source_path=config.shopify_source_path,
            webhook_endpoint="https://wh.buttonsbebe.com/api/shopify/webhooks",
        )
        report = run_monitor(unsafe, deps)
        webhooks = next(check for check in report.checks if check.name == "shopify.webhooks_exact")
        self.assertFalse(webhooks.ok)
        self.assertIn("unsafe scheme", webhooks.detail)

    def test_caddy_manifest_drift_fails_closed(self) -> None:
        config, deps, temp_dir = self.make_fixture()
        self.addCleanup(temp_dir.cleanup)
        support = config.caddy_manifest_path.parent / "sites/support.caddy"
        support.write_text("support.buttonsbebe.com { respond 500 }\n", encoding="utf-8")
        report = run_monitor(config, deps)
        caddy = next(check for check in report.checks if check.name == "caddy.config_manifest")
        self.assertFalse(caddy.ok)
        self.assertEqual(caddy.status, "DRIFT")

    def test_unreadable_caddy_manifest_is_unknown(self) -> None:
        config, deps, temp_dir = self.make_fixture()
        self.addCleanup(temp_dir.cleanup)
        missing = SafetyConfig(
            shopify_source_path=config.shopify_source_path,
            caddy_manifest_path=config.caddy_manifest_path.with_name("missing.sha256"),
        )
        report = run_monitor(missing, deps)
        caddy = next(check for check in report.checks if check.name == "caddy.config_manifest")
        self.assertFalse(caddy.ok)
        self.assertEqual(caddy.status, "UNKNOWN")

    def test_process_older_than_hardened_source_is_drift(self) -> None:
        config, deps, temp_dir = self.make_fixture()
        self.addCleanup(temp_dir.cleanup)
        older = SafetyDependencies(
            deps.run_command,
            deps.http_get,
            deps.read_text,
            deps.read_bytes,
            lambda _path: 1800.0,
            deps.now,
            deps.clock_ticks,
        )
        report = run_monitor(config, older)
        loaded = next(check for check in report.checks if check.name == "warehouse.process_source")
        self.assertFalse(loaded.ok)
        self.assertEqual(loaded.status, "DRIFT")

    def test_unreviewed_warehouse_javascript_is_drift(self) -> None:
        config, deps, temp_dir = self.make_fixture()
        self.addCleanup(temp_dir.cleanup)
        extra = config.warehouse_source_manifest_path.parent / "src/alternate.js"
        extra.write_text("export const unsafe = true;\n", encoding="utf-8")
        report = run_monitor(config, deps)
        inventory = next(
            check for check in report.checks if check.name == "warehouse.source_manifest"
        )
        self.assertFalse(inventory.ok)
        self.assertEqual(inventory.status, "DRIFT")


if __name__ == "__main__":
    unittest.main()
