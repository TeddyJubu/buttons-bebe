"""The shared root .env reaches the processor's os.environ only by allowlist."""
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from config import load_processor_env

PROCESSOR = Path(__file__).resolve().parent
WEBHOOK_SRC = PROCESSOR.parent / "webhook" / "src"
SYNTHETIC_ENV = (
    "SHOPIFY_CLIENT_SECRET=synthetic-shopify-secret\n"
    "GORGIAS_API_KEY=synthetic-gorgias-key\n"
    "GORGIAS_API_EMAIL=synthetic@example.test\n"
    "SHOPIFY_SHOP=synthetic-shop\n"
    "WA_SEND_SECRET=synthetic-wa-secret\n"
)


class EnvAllowlistTests(unittest.TestCase):
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
        # A copied tree resolves both config modules' .env to a synthetic file,
        # so the result never depends on the host's real root .env.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(SYNTHETIC_ENV, encoding="utf-8")
            (root / "processor").mkdir()
            shutil.copy(PROCESSOR / "config.py", root / "processor" / "config.py")
            shutil.copytree(
                WEBHOOK_SRC / "bb_webhook",
                root / "webhook" / "src" / "bb_webhook",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            # Refuse the whole-file load outright, then report exactly which keys
            # the imports added to os.environ. Any new copy path shows up here.
            script = textwrap.dedent(
                """
                import os
                import dotenv
                def refuse(*args, **kwargs):
                    raise SystemExit("load_dotenv called at import")
                dotenv.load_dotenv = refuse
                baseline = set(os.environ)
                import config, bb_webhook.config, bb_webhook.db, bb_webhook.logging_utils
                print(",".join(sorted(set(os.environ) - baseline)))
                """
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root / "processor",
                env={
                    "PYTHONPATH": f"{root / 'processor'}:{root / 'webhook' / 'src'}",
                    "PATH": "/usr/bin:/bin",
                },
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "WA_SEND_SECRET")


if __name__ == "__main__":
    unittest.main()
