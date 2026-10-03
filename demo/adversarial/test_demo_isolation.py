"""Fail-closed checks for the Cute Things demo runtime boundary."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dotenv
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]
PROCESSOR = ROOT / "processor"
WEBHOOK_SRC = ROOT / "webhook" / "src"
for path in (PROCESSOR, WEBHOOK_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
sys.path.append(str(ROOT))

from demo.adversarial.offline_imports import without_root_dotenv  # noqa: E402

with without_root_dotenv():
    from bb_webhook.config import Settings as WebhookSettings  # noqa: E402
    from bb_webhook.hermes_permissions import CANONICAL_TOOLSETS  # noqa: E402
    from config import ProcessorSettings  # noqa: E402
    from demo.local_only import require_loopback  # noqa: E402
    from demo_safety import demo_url_allowed  # noqa: E402
    import orchestrator  # noqa: E402
    import whatsapp_notifier  # noqa: E402


PROCESSOR_DEMO = {
    "DEMO_MODE": True,
    "SHOPIFY_SHOP": "yznyc1-ez.myshopify.com",
    "GORGIAS_SUBDOMAIN": "cute-things-demo",
    "WEBHOOK_DB_PATH": "./data/cute-things-demo-webhook.db",
    "KB_MCP_URL": "http://127.0.0.1:8177/mcp",
    "HERMES_PROFILE": "cutethingsdemo",
    "HERMES_TOOLSETS": "buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias",
    "HERMES_IGNORE_RULES": True,
    "HERMES_SKIP_APPROVAL": False,
    "SUPPORT_STORE_NAME": "Cute Things",
}

WEBHOOK_DEMO = {
    "DEMO_MODE": True,
    "WEBHOOK_SECRET": "demo-only-isolation-test",
    "SHOPIFY_SHOP": "yznyc1-ez.myshopify.com",
    "GORGIAS_SUBDOMAIN": "cute-things-demo",
    "GORGIAS_BASE_URL": "http://127.0.0.1:8190",
    "WEBHOOK_HOST": "127.0.0.1",
    "WEBHOOK_PORT": 8100,
    "WEBHOOK_DB_PATH": "./data/cute-things-demo-webhook.db",
    "FEEDBACK_KB_ROOT": "./demo/data/kb",
    "HERMES_PROFILE": "cutethingsdemo",
    "HERMES_REWRITE_TOOLSETS": "buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias",
    "HERMES_IGNORE_RULES": True,
    "SUPPORT_STORE_NAME": "Cute Things",
}


class DemoIsolationTests(unittest.TestCase):
    def test_processor_demo_settings_accept_only_the_isolated_profile(self) -> None:
        settings = ProcessorSettings(_env_file=None, **PROCESSOR_DEMO)
        self.assertTrue(settings.demo_mode)
        for key, value in (
            ("SHOPIFY_SHOP", "buttonsbebe"),
            ("WEBHOOK_DB_PATH", "./data/webhook.db"),
            ("WEBHOOK_DB_PATH", "../client-data/demo-webhook.db"),
            ("KB_MCP_URL", "http://127.0.0.1:8077/mcp"),
            ("HERMES_PROFILE", ""),
            ("HERMES_IGNORE_RULES", False),
        ):
            unsafe = {**PROCESSOR_DEMO, key: value}
            with self.subTest(key=key), self.assertRaises(ValidationError):
                ProcessorSettings(_env_file=None, **unsafe)

    def test_webhook_demo_settings_reject_client_or_shared_storage(self) -> None:
        settings = WebhookSettings(_env_file=None, **WEBHOOK_DEMO)
        self.assertTrue(settings.demo_mode)
        for key, value in (
            ("SHOPIFY_SHOP", "buttonsbebe"),
            ("GORGIAS_BASE_URL", "https://buttonsbebe.gorgias.com"),
            ("WEBHOOK_HOST", "0.0.0.0"),
            ("WEBHOOK_DB_PATH", "./data/webhook.db"),
            ("WEBHOOK_DB_PATH", "../client-data/demo-webhook.db"),
            ("FEEDBACK_KB_ROOT", "./kb"),
            ("FEEDBACK_KB_ROOT", "../client-data/demo-kb"),
            ("HERMES_REWRITE_TOOLSETS", "terminal"),
            ("HERMES_REWRITE_TOOLSETS", "todo"),
        ):
            unsafe = {**WEBHOOK_DEMO, key: value}
            with self.subTest(key=key), self.assertRaises(ValidationError):
                WebhookSettings(_env_file=None, **unsafe)

    def test_webhook_demo_rewrite_toolsets_are_exactly_the_three_read_only_tools(self) -> None:
        reordered = {**WEBHOOK_DEMO, "HERMES_REWRITE_TOOLSETS":
                     " buttonsbebe_gorgias , buttonsbebe_kb,buttonsbebe_redo "}
        self.assertEqual(WebhookSettings(_env_file=None, **reordered).hermes_rewrite_toolsets,
                         CANONICAL_TOOLSETS)
        for label, value in (
            ("empty", ""),
            ("blank", "   "),
            ("subset", "buttonsbebe_kb,buttonsbebe_redo"),
            ("single", "buttonsbebe_gorgias"),
            ("unknown", "buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_shopify"),
            ("superset", CANONICAL_TOOLSETS + ",terminal"),
            ("duplicate", "buttonsbebe_kb,buttonsbebe_kb,buttonsbebe_redo"),
            ("duplicate-extra", CANONICAL_TOOLSETS + ",buttonsbebe_kb"),
            ("empty-member", "buttonsbebe_kb,,buttonsbebe_redo,buttonsbebe_gorgias"),
            ("case", CANONICAL_TOOLSETS.upper()),
            ("demo-prefix", "demo_kb,demo_redo,demo_gorgias"),
        ):
            unsafe = {**WEBHOOK_DEMO, "HERMES_REWRITE_TOOLSETS": value}
            with self.subTest(label=label), self.assertRaisesRegex(
                    ValidationError, "HERMES_REWRITE_TOOLSETS must be exactly the three approved"):
                WebhookSettings(_env_file=None, **unsafe)

    def test_non_demo_webhook_settings_keep_their_rewrite_toolset_default(self) -> None:
        settings = WebhookSettings(_env_file=None, WEBHOOK_SECRET="non-demo-isolation-test")
        self.assertFalse(settings.demo_mode)
        self.assertEqual(settings.hermes_rewrite_toolsets, "")

    def test_demo_url_guard_rejects_external_or_credentialed_destinations(self) -> None:
        with patch.dict(os.environ, {"DEMO_MODE": "1"}, clear=False):
            self.assertTrue(demo_url_allowed(
                "http://127.0.0.1:8100/dashboard/api/results",
                port=8100,
                exact_path="/dashboard/api/results",
            ))
            for url in (
                "https://example.com/dashboard/api/results",
                "http://127.0.0.1:8000/dashboard/api/results",
                "http://user:pass@127.0.0.1:8100/dashboard/api/results",
                "http://127.0.0.1:8100/other",
            ):
                with self.subTest(url=url):
                    self.assertFalse(demo_url_allowed(
                        url,
                        port=8100,
                        exact_path="/dashboard/api/results",
                    ))

    def test_result_callback_never_opens_an_external_url_in_demo_mode(self) -> None:
        with (
            patch.dict(os.environ, {
                "DEMO_MODE": "1",
                "DASHBOARD_RESULT_URL": "https://client.example/results",
            }, clear=False),
            patch("urllib.request.urlopen") as urlopen,
            self.assertRaisesRegex(RuntimeError, "Demo result persistence destination is blocked"),
        ):
            orchestrator._save_result_to_webhook(
                1,
                "2",
                3,
                {"priority": "normal", "action": "drafted"},
            )
        urlopen.assert_not_called()

    def test_whatsapp_never_opens_an_external_url_in_demo_mode(self) -> None:
        with (
            patch.dict(os.environ, {
                "DEMO_MODE": "1",
                "WHATSAPP_SEND_URL": "https://client.example/send",
                "WHATSAPP_TICKET_BASE_URL": "https://client.example/tickets",
                "WA_SEND_SECRET": "demo-secret",
            }, clear=False),
            patch("urllib.request.urlopen") as urlopen,
        ):
            sent = whatsapp_notifier.send_whatsapp(1, "subject", "demo@example.com", "summary", "reason")
        self.assertFalse(sent)
        urlopen.assert_not_called()

    def test_all_fake_server_hosts_must_be_loopback(self) -> None:
        self.assertEqual(require_loopback("127.0.0.1", "test"), "127.0.0.1")
        for host in ("0.0.0.0", "192.0.2.10", "example.com"):
            with self.subTest(host=host), self.assertRaises(RuntimeError):
                require_loopback(host, "test")

    def test_supported_launcher_cannot_source_an_arbitrary_environment_file(self) -> None:
        launcher = (ROOT / "demo" / "run_real_stack.sh").read_text(encoding="utf-8")
        self.assertNotIn("DEMO_ENV_FILE", launcher)
        self.assertIn('env_file="demo/.env"', launcher)



class OfflineImportGuardTests(unittest.TestCase):
    SENTINEL = "OFFLINE_IMPORT_GUARD_SENTINEL"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="offline-import-guard-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.env_file = self.root / "synthetic.env"
        self.env_file.write_text(f"{self.SENTINEL}=from-synthetic-dotenv\n", encoding="utf-8")
        self.addCleanup(os.environ.pop, self.SENTINEL, None)
        os.environ.pop(self.SENTINEL, None)

    def _settings_class(self) -> type[BaseSettings]:
        class SyntheticSettings(BaseSettings):
            model_config = SettingsConfigDict(env_file=str(self.env_file), extra="ignore")
            sentinel: str = Field(default="", alias=OfflineImportGuardTests.SENTINEL)

        return SyntheticSettings

    def test_both_dotenv_readers_are_effective_without_the_guard(self) -> None:
        self.assertEqual(self._settings_class()().sentinel, "from-synthetic-dotenv")
        self.assertTrue(dotenv.load_dotenv(self.env_file))
        self.assertEqual(os.environ[self.SENTINEL], "from-synthetic-dotenv")

    def test_first_import_and_settings_build_read_no_dotenv_file(self) -> None:
        module = "offline_guard_synthetic_config"
        (self.root / f"{module}.py").write_text(
            "from pathlib import Path\n"
            "from dotenv import load_dotenv\n"
            "from pydantic import Field\n"
            "from pydantic_settings import BaseSettings, SettingsConfigDict\n"
            f"ENV = Path({str(self.env_file)!r})\n"
            "LOADED = load_dotenv(ENV)\n"
            "class Settings(BaseSettings):\n"
            "    model_config = SettingsConfigDict(env_file=str(ENV), extra='ignore')\n"
            f"    sentinel: str = Field(default='', alias={self.SENTINEL!r})\n"
            "SETTINGS = Settings()\n",
            encoding="utf-8",
        )
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        self.addCleanup(sys.modules.pop, module, None)
        with without_root_dotenv():
            imported = importlib.import_module(module)
            built_inside = imported.Settings()
        self.assertFalse(imported.LOADED)
        self.assertNotIn(self.SENTINEL, os.environ)
        self.assertEqual(imported.SETTINGS.sentinel, "")
        self.assertEqual(built_inside.sentinel, "")
        self.assertEqual(imported.Settings().sentinel, "from-synthetic-dotenv")

    def test_processor_settings_built_after_import_have_no_env_file(self) -> None:
        self.assertIsNone(ProcessorSettings.model_config["env_file"])

    def test_guard_restores_both_readers_after_failed_import(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "synthetic import failure"):
            with without_root_dotenv():
                self.assertFalse(dotenv.load_dotenv(self.env_file))
                self.assertEqual(self._settings_class()().sentinel, "")
                raise RuntimeError("synthetic import failure")
        self.assertIsNone(ProcessorSettings.model_config["env_file"])
        self.assertEqual(self._settings_class()().sentinel, "from-synthetic-dotenv")
        self.assertTrue(dotenv.load_dotenv(self.env_file))


if __name__ == "__main__":
    unittest.main(verbosity=2)
