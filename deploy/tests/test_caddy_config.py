from __future__ import annotations

import unittest
from pathlib import Path


CADDY = Path(__file__).resolve().parents[1] / "caddy" / "Caddyfile.redacted"
ROOT = Path(__file__).resolve().parents[2]


class CaddyConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = CADDY.read_text()

    def test_whatsapp_token_matches_exact_and_child_routes(self) -> None:
        self.assertIn(
            "@whatsapp path /connect-whatsapp/<WA_TOKEN> /connect-whatsapp/<WA_TOKEN>/*",
            self.text,
        )
        self.assertIn("handle @whatsapp", self.text)
        self.assertNotIn("handle /connect-whatsapp/<WA_TOKEN> {", self.text)

    def test_console_and_admin_routes_are_basic_auth_gated(self) -> None:
        self.assertEqual(self.text.count("basicauth {"), 4)
        self.assertEqual(self.text.count("chaim <CONSOLE_PASSWORD_HASH>"), 4)
        for route in ("/console/api/*", "/console/waapi/*", "/console/kbapi/*", "/console*"):
            self.assertIn(route, self.text)

    def test_internal_dashboard_namespace_cannot_fall_through_public_proxy(self) -> None:
        self.assertIn("@directdashboard path /dashboard /dashboard/*", self.text)
        self.assertIn('respond "Not found" 404', self.text)
        direct_block = self.text.index("handle @directdashboard")
        public_catch_all = self.text.index("\n\thandle {", direct_block)
        self.assertLess(direct_block, public_catch_all)
        self.assertLess(self.text.index("handle @consoleapi"), direct_block)
        self.assertIn("@publicwebhook path /webhook/gorgias/*", self.text)
        self.assertIn("handle @publicwebhook", self.text)
        self.assertIn("@health path /health /ready", self.text)
        self.assertIn("handle @health", self.text)
        catch_all = self.text[public_catch_all:]
        self.assertIn('respond "Not found" 404', catch_all)
        self.assertNotIn("reverse_proxy", catch_all)

    def test_legacy_component_caddyfiles_are_retired_not_deployable(self) -> None:
        for relative in ("webhook/Caddyfile", "whatsapp-connect/Caddyfile"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("RETIRED — DO NOT DEPLOY", text)
            self.assertNotIn("reverse_proxy", text)

    def test_proxy_targets_are_local_and_secrets_are_placeholders(self) -> None:
        self.assertNotIn("bcrypt", self.text.lower())
        self.assertIn("<WA_TOKEN>", self.text)
        self.assertIn("<CONSOLE_PASSWORD_HASH>", self.text)
        for target in ("127.0.0.1:8000", "127.0.0.1:8085", "127.0.0.1:8087"):
            self.assertIn(target, self.text)


if __name__ == "__main__":
    unittest.main()
