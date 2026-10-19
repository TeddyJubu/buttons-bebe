"""The shared root .env reaches the processor's os.environ only by allowlist."""
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PROCESSOR = Path(__file__).resolve().parent
WEBHOOK_SRC = PROCESSOR.parent / "webhook" / "src"
sys.path[:0] = [str(PROCESSOR), str(WEBHOOK_SRC)]

from bb_webhook.hermes_permissions import PROVIDER_ENVIRONMENT  # noqa: E402
from config import PROCESSOR_ENV_KEYS, load_processor_env  # noqa: E402

SYNTHETIC_ENV = (
    "SHOPIFY_CLIENT_SECRET=synthetic-shopify-secret\n"
    "GORGIAS_API_KEY=synthetic-gorgias-key\n"
    "GORGIAS_API_EMAIL=synthetic@example.test\n"
    "SHOPIFY_SHOP=synthetic-shop\n"
    "WA_SEND_SECRET=synthetic-wa-secret\n"
    "DEMO_MODE\n"  # bare key: dotenv yields None, which must be skipped
)
# Pinned literally so dropping a key from PROCESSOR_ENV_KEYS fails a test.
EXPECTED_KEYS = frozenset({
    "DEMO_MODE",
    "WHATSAPP_SEND_URL",
    "WA_SEND_SECRET",
    "WHATSAPP_TICKET_BASE_URL",
    "SUPPORT_TICKET_BASE_URL",
    "DASHBOARD_RESULT_URL",
    "OPENAI_API_KEY",
    "OLLAMA_API_KEY",
})
FULL_ENV = "".join(f"{key}=synthetic-{key.lower()}\n" for key in sorted(EXPECTED_KEYS))


def _run_imports(env_text, extra_env=None):
    """Import the processor and webhook config chain in an isolated copied tree."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".env").write_text(env_text, encoding="utf-8")
        shutil.copytree(
            PROCESSOR,
            root / "processor",
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "test_*.py"),
        )
        shutil.copytree(
            WEBHOOK_SRC / "bb_webhook",
            root / "webhook" / "src" / "bb_webhook",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        # Refuse the whole-file load outright, then report exactly which keys the
        # imports added to os.environ. orchestrator pulls in the processor's
        # modules and the webhook modules they import.
        script = textwrap.dedent(
            """
            import os
            import dotenv
            def refuse(*args, **kwargs):
                raise SystemExit("load_dotenv called at import")
            dotenv.load_dotenv = refuse
            baseline = set(os.environ)
            import orchestrator, bb_webhook.config, bb_webhook.db, bb_webhook.logging_utils
            print(",".join(sorted(set(os.environ) - baseline)))
            """
        )
        env = {
            "PYTHONPATH": f"{root / 'processor'}:{root / 'webhook' / 'src'}",
            "PATH": "/usr/bin:/bin",
            **(extra_env or {}),
        }
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=root / "processor",
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )


class EnvAllowlistTests(unittest.TestCase):
    def test_every_allowlisted_key_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(FULL_ENV, encoding="utf-8")
            environ = {}
            load_processor_env(env_file, environ)
        self.assertEqual(PROCESSOR_ENV_KEYS, EXPECTED_KEYS)
        self.assertEqual(set(environ), EXPECTED_KEYS)

    def test_secret_stays_out_while_needed_key_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(SYNTHETIC_ENV, encoding="utf-8")
            environ = {}
            load_processor_env(env_file, environ)
            preset = {"WA_SEND_SECRET": "from-process-env"}
            load_processor_env(env_file, preset)
        self.assertEqual(environ, {"WA_SEND_SECRET": "synthetic-wa-secret"})
        self.assertEqual(preset, {"WA_SEND_SECRET": "from-process-env"})

    def test_processor_imports_keep_secrets_out_of_os_environ(self):
        result = _run_imports(SYNTHETIC_ENV)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "WA_SEND_SECRET")

    def test_demo_mode_skips_the_root_env_copy(self):
        result = _run_imports(SYNTHETIC_ENV, {"DEMO_MODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "")

    def test_hermes_provider_keys_are_forwarded_by_the_allowlist(self):
        forwarded = PROVIDER_ENVIRONMENT - {"LANG", "LC_ALL", "TERM"}
        self.assertLessEqual(forwarded, PROCESSOR_ENV_KEYS)


if __name__ == "__main__":
    unittest.main()
