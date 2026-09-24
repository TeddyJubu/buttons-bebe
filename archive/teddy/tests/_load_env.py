"""Load /root/.env for test harnesses."""
import sys

sys.path.insert(0, "/root/gorgias-webhook")
import dotenv_loader  # noqa: E402

dotenv_loader.load()
