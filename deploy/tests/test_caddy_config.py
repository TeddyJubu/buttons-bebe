from __future__ import annotations

import re
import unittest
from pathlib import Path


CADDY_DIR = Path(__file__).resolve().parents[1] / "caddy"
CADDY = CADDY_DIR / "Caddyfile.redacted"
SITES_DIR = CADDY_DIR / "sites"
ROOT = Path(__file__).resolve().parents[2]


class CaddyConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root_text = CADDY.read_text(encoding="utf-8")
        self.fragments = {
            path.stem: path.read_text(encoding="utf-8")
            for path in sorted(SITES_DIR.glob("*.caddy"))
        }

    def test_root_is_an_import_only_interface(self) -> None:
        imports = re.findall(r"^import (.+)$", self.root_text, re.MULTILINE)
        self.assertEqual(
            imports,
            ["sites/support.caddy", "sites/exchange.caddy", "sites/warehouse.caddy"],
        )
        non_comment = "\n".join(
            line
            for line in self.root_text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        self.assertEqual(
            non_comment.splitlines(),
            [f"import {path}" for path in imports],
        )
        self.assertNotIn("reverse_proxy", self.root_text)
        self.assertNotIn("basic_auth", self.root_text)
        self.assertNotIn("handle ", self.root_text)

    def test_each_fragment_owns_only_its_declared_hosts(self) -> None:
        expected = {
            "support": {
                "hermes.buttonsbebe.com",
                "srv1766050.hstgr.cloud",
                "support.buttonsbebe.com",
            },
            "exchange": {"exchange.buttonsbebe.com"},
            "warehouse": {"wh.buttonsbebe.com"},
        }
        self.assertEqual(set(self.fragments), set(expected))
        declared: dict[str, str] = {}
        for name, text in self.fragments.items():
            hosts = set()
            depth = 0
            for line in text.splitlines():
                stripped = line.strip()
                if depth == 0 and stripped.endswith("{") and not stripped.startswith("#"):
                    candidate = stripped[:-1].strip()
                    host_parts = [part.strip() for part in candidate.split(",")]
                    if (
                        host_parts
                        and all(re.fullmatch(r"[A-Za-z0-9.-]+", part) for part in host_parts)
                        and not candidate.startswith(("@", "handle", "forward", "basic"))
                    ):
                        hosts.update(host_parts)
                depth += stripped.count("{") - stripped.count("}")
            self.assertEqual(hosts, expected[name], msg=name)
            for host in hosts:
                self.assertNotIn(host, declared, msg=f"duplicate host {host}")
                declared[host] = name
        self.assertEqual(set(declared), set().union(*expected.values()))

    def test_support_preserves_cookie_forward_auth_and_login_routes(self) -> None:
        text = self.fragments["support"]
        self.assertNotIn("basicauth", text.lower())
        self.assertIn(
            "@whatsapp path /connect-whatsapp/<WA_TOKEN> /connect-whatsapp/<WA_TOKEN>/*",
            text,
        )
        self.assertNotIn("handle /connect-whatsapp/<WA_TOKEN> {", text)
        self.assertIn("@consoleauth path /console/api/auth/*", text)
        self.assertIn("uri replace /console/api/auth /auth", text)
        self.assertEqual(text.count("uri /auth/check"), 3)
        self.assertIn("uri /auth/page-check", text)
        self.assertIn("@consolelogin path /console/login /console/login/*", text)
        self.assertIn("rewrite * /login.html", text)
        for route in ("/console/api/*", "/console/waapi/*", "/console/kbapi/*", "/console*"):
            self.assertIn(route, text)
        self.assertIn("@directdashboard path /dashboard /dashboard/*", text)
        self.assertIn("@health path /health /ready", text)

    def test_support_preserves_hermes_dashboard_route(self) -> None:
        text = self.fragments["support"]
        self.assertIn("hermes.buttonsbebe.com {", text)
        self.assertIn("reverse_proxy 127.0.0.1:9119", text)
        self.assertIn("output file /var/log/bb-webhook/caddy-hermes.log", text)

    def test_warehouse_bypasses_auth_only_for_webhook_path(self) -> None:
        text = self.fragments["warehouse"]
        self.assertIn("@protected not path /api/shopify/webhook/*", text)
        self.assertIn("basicauth @protected {", text)
        self.assertIn("warehouse <WAREHOUSE_PASSWORD_HASH>", text)
        self.assertNotIn("/api/shopify/webhook/*", text.split("basicauth", 1)[1])
        self.assertIn("X-Shopify-Hmac-Sha256", text)
        self.assertIn("request_body", text)
        self.assertIn("max_size 16MB", text)
        self.assertIn("read_timeout 120s", text)

    def test_all_upstreams_are_loopback(self) -> None:
        all_text = "\n".join(self.fragments.values())
        upstreams = re.findall(r"(?:reverse_proxy|forward_auth)\s+([^\s{]+)", all_text)
        self.assertTrue(upstreams)
        for upstream in upstreams:
            self.assertTrue(
                upstream.startswith("127.0.0.1:"),
                msg=f"non-loopback upstream: {upstream}",
            )
        for target in (
            "127.0.0.1:8000",
            "127.0.0.1:8085",
            "127.0.0.1:8087",
            "127.0.0.1:9119",
            "127.0.0.1:4100",
            "127.0.0.1:4000",
        ):
            self.assertIn(target, all_text)

    def test_redacted_fragments_keep_placeholders_and_no_secrets(self) -> None:
        all_text = "\n".join(self.fragments.values())
        for placeholder in ("<WA_TOKEN>", "<WAREHOUSE_PASSWORD_HASH>"):
            self.assertIn(placeholder, all_text)
        self.assertNotRegex(all_text, r"\$2[aby]\$\d{2}\$")
        self.assertNotRegex(all_text, r"(?i)(password|token)\s*[:=]\s*[^<\s#]+")

    def test_caddy_readme_requires_fragment_atomicity(self) -> None:
        readme = (SITES_DIR.parent / "README.md").read_text(encoding="utf-8")
        for phrase in (
            "import-only entrypoint",
            "one owned fragment per service boundary",
            "Stage the complete reviewed fragment set",
            "atomic symlink rename",
            "Never bypass validation",
        ):
            self.assertIn(phrase, readme)
        self.assertNotRegex(
            readme,
            r"(?m)^\s*(?:cp|rsync|install)\b.*Caddyfile\.redacted.*?/etc/caddy/Caddyfile",
        )
        self.assertNotRegex(readme, r"(?m)^\s*rsync\b.*deploy/caddy.*?/etc/caddy")

    def test_internal_dashboard_namespace_cannot_fall_through_public_proxy(self) -> None:
        text = self.fragments["support"]
        direct_block = text.index("handle @directdashboard")
        public_catch_all = text.index("\n\thandle {", direct_block)
        self.assertLess(direct_block, public_catch_all)
        self.assertLess(text.index("handle @consoleapi"), direct_block)
        self.assertIn("@publicwebhook path /webhook/gorgias/*", text)
        self.assertIn("handle @publicwebhook", text)
        catch_all = text[public_catch_all:]
        self.assertIn('respond "Not found" 404', catch_all)
        self.assertNotIn("reverse_proxy", catch_all)

    def test_legacy_component_caddyfiles_are_retired_not_deployable(self) -> None:
        for relative in ("webhook/Caddyfile", "whatsapp-connect/Caddyfile"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("RETIRED — DO NOT DEPLOY", text)
            self.assertNotIn("reverse_proxy", text)


if __name__ == "__main__":
    unittest.main()
