"""Configuration for the Fable server.

Reads ``fable/.env.fable`` (a plain KEY=VALUE file) with sane defaults from the
API contract. Real environment variables override the file. Everything works
with zero setup.
"""
from __future__ import annotations

import os
from pathlib import Path

# Path anchors ---------------------------------------------------------------
#   config.py lives at   fable/server/app/config.py
#   parents[0]=app  [1]=server  [2]=fable  [3]=repo root
_HERE = Path(__file__).resolve()
FABLE_DIR = _HERE.parents[2]
REPO_ROOT = _HERE.parents[3]
ENV_FILE = FABLE_DIR / ".env.fable"
CONSOLE_DIR = FABLE_DIR / "console"

_DEFAULTS = {
    "FABLE_DB": "fable/server/data/fable.db",
    "FABLE_BRAIN": "mock",
    "SHOPIFY_BASE": "http://127.0.0.1:9601",
    "SHOPIFY_SHOP": "buttons-bebe.myshopify.com",
    "SHOPIFY_CLIENT_ID": "test-client-id",
    "SHOPIFY_CLIENT_SECRET": "test-client-secret",
    "SHOPIFY_API_VERSION": "2026-07",
    "REDO_BASE": "http://127.0.0.1:9602",
    "REDO_API_KEY": "test-redo-key",
    "REDO_STORE_ID": "bb-store-1",
    "MAILBOX_BASE": "http://127.0.0.1:9603",
    "SUPPORT_EMAIL": "care@buttonsbebe.com",
    "FABLE_HOST": "127.0.0.1",
    "FABLE_PORT": "9600",
}


def _parse_env_file(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        # strip inline comments and surrounding quotes
        val = val.split(" #", 1)[0].strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


_FILE_ENV = _parse_env_file(ENV_FILE)


def get(key: str) -> str:
    """Resolution order: real env var > .env.fable file > contract default."""
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    if key in _FILE_ENV:
        return _FILE_ENV[key]
    return _DEFAULTS.get(key, "")


def db_path() -> str:
    raw = get("FABLE_DB")
    p = Path(raw)
    if not p.is_absolute():
        p = REPO_ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


# Convenience accessors ------------------------------------------------------
BRAIN = get("FABLE_BRAIN")
SHOPIFY_BASE = get("SHOPIFY_BASE").rstrip("/")
SHOPIFY_SHOP = get("SHOPIFY_SHOP")
SHOPIFY_CLIENT_ID = get("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = get("SHOPIFY_CLIENT_SECRET")
SHOPIFY_API_VERSION = get("SHOPIFY_API_VERSION")
REDO_BASE = get("REDO_BASE").rstrip("/")
REDO_API_KEY = get("REDO_API_KEY")
REDO_STORE_ID = get("REDO_STORE_ID")
MAILBOX_BASE = get("MAILBOX_BASE").rstrip("/")
SUPPORT_EMAIL = get("SUPPORT_EMAIL")
HOST = get("FABLE_HOST")
PORT = int(get("FABLE_PORT") or "9600")
