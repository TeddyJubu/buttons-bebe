"""Shared helpers for the Buttons Bebe integration tool modules (Redo, Gorgias).

Each integration is its own module (its own MCP server + systemd service + port +
Hermes tool). They only share this tiny helper for reading the app's .env.
"""
import pathlib
import re

ENV_CANDIDATES = [
    pathlib.Path("/root/Buttonsbebe Agent/.env"),
    pathlib.Path("/root/Buttonsbebe Agent/webhook/.env"),
]


def _clean(v: str) -> str:
    # remove paste artifacts (surrounding quotes/space, trailing backslashes, CR)
    return re.sub(r'^[\s"\']+|[\s"\'\\]+$', "", v).replace("\r", "")


def load_env() -> dict:
    env: dict = {}
    for fp in ENV_CANDIDATES:
        if not fp.exists():
            continue
        for line in fp.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k, v = k.strip(), _clean(v)
            if v and not env.get(k):
                env[k] = v
    return env
