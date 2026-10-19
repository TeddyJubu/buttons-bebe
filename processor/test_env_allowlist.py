"""The shared root .env reaches the processor's os.environ only by allowlist."""
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from config import load_processor_env

PROCESSOR = Path(__file__).resolve().parent
WEBHOOK_SRC = PROCESSOR.parent / "webhook" / "src"


class EnvAllowlistTests(unittest.TestCase):
    def test_secret_stays_out_while_needed_key_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "SHOPIFY_CLIENT_SECRET=synthetic-shopify-secret\n"
                "GORGIAS_API_KEY=synthetic-gorgias-key\n"
                "WA_SEND_SECRET=synthetic-wa-secret\n",
                encoding="utf-8",
            )
            environ = {}
            load_processor_env(env_file, environ)
        self.assertEqual(environ, {"WA_SEND_SECRET": "synthetic-wa-secret"})

    def test_processor_imports_never_copy_whole_dotenv(self):
        # bb_webhook.config is imported by the processor too; neither module may
        # call load_dotenv at import (only the webhook app entrypoint does).
        script = textwrap.dedent(
            """
            import dotenv
            def refuse(*args, **kwargs):
                raise SystemExit("load_dotenv called at import")
            dotenv.load_dotenv = refuse
            import config, bb_webhook.config, bb_webhook.db, bb_webhook.logging_utils
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROCESSOR,
            env={"PYTHONPATH": f"{PROCESSOR}:{WEBHOOK_SRC}", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
