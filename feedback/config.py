"""config.py — settings for the feedback capture loop.

Reads the same .env the rest of the agent uses (Gorgias creds), plus a handful of
feedback-specific knobs. No secrets are hard-coded. Everything has a safe default.

Env resolution order (first hit wins), matching tools/_common.py on the VPS but
also falling back to a local .env so the package is testable off the box:
    /root/Buttonsbebe Agent/.env
    /root/Buttonsbebe Agent/webhook/.env
    <repo-root>/.env
"""
from __future__ import annotations

import os
import pathlib
import re

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
PKG_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parent

ENV_CANDIDATES = [
    pathlib.Path("/root/Buttonsbebe Agent/.env"),
    pathlib.Path("/root/Buttonsbebe Agent/webhook/.env"),
    REPO_ROOT / ".env",
]


def _clean(v: str) -> str:
    """Strip paste artifacts (surrounding quotes/space, trailing backslash, CR)."""
    return re.sub(r'^[\s"\']+|[\s"\'\\]+$', "", v).replace("\r", "")


def load_env() -> dict:
    env: dict = {}
    for fp in ENV_CANDIDATES:
        try:
            if not fp.exists():
                continue
            lines = fp.read_text().splitlines()
        except OSError:
            # unreadable (e.g. root-owned .env on a dev box) — skip quietly
            continue
        for line in lines:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k, v = k.strip(), _clean(v)
            if v and not env.get(k):
                env[k] = v
    # process env overrides file values
    for k, v in os.environ.items():
        if k.startswith(("GORGIAS_", "FEEDBACK_")):
            env[k] = v
    return env


_env = load_env()


def _bare_subdomain(sub: str) -> str:
    s = _clean(sub).replace("https://", "").replace("http://", "").strip("/").split("/")[0]
    return s[: -len(".gorgias.com")] if s.endswith(".gorgias.com") else s


# --------------------------------------------------------------------------- #
# Gorgias (read-only)
# --------------------------------------------------------------------------- #
GORGIAS_SUBDOMAIN = _bare_subdomain(_env.get("GORGIAS_SUBDOMAIN", ""))
GORGIAS_EMAIL = _env.get("GORGIAS_API_EMAIL", "")
GORGIAS_KEY = _env.get("GORGIAS_API_KEY", "")
GORGIAS_BASE = f"https://{GORGIAS_SUBDOMAIN}.gorgias.com/api" if GORGIAS_SUBDOMAIN else ""
USER_AGENT = "ButtonsBebe-Feedback/1.0"  # Gorgias WAF 403s the default urllib UA

# --------------------------------------------------------------------------- #
# KB folders
# --------------------------------------------------------------------------- #
KB_ROOT = pathlib.Path(_env.get("FEEDBACK_KB_ROOT", str(REPO_ROOT / "kb")))
LEARNED_DIR = KB_ROOT / "learned"            # holding pen — NOT indexed
TICKETS_DIR = KB_ROOT / "tickets"            # indexed; promote target (must be PII-free)
ARCHIVE_DIR = KB_ROOT / "_archive_learned"   # leading underscore => never indexed

# --------------------------------------------------------------------------- #
# Local state (cursor + processed markers)
# --------------------------------------------------------------------------- #
STATE_DB = pathlib.Path(_env.get("FEEDBACK_STATE_DB", str(PKG_DIR / "feedback_state.db")))

# --------------------------------------------------------------------------- #
# Behaviour knobs
# --------------------------------------------------------------------------- #
# SHADOW mode: the collector still runs and writes learned/ files, but this flag is
# a reminder that NOTHING reaches the live KB until a human promotes it AND we have
# a passing before/after check. Do NOT flip CLAUDE.md STUB->LIVE until validated.
ENABLED = _env.get("FEEDBACK_ENABLED", "shadow").lower()  # shadow | live

# The Gorgias identity that posts the AI draft internal notes. Setting this makes
# draft detection exact. If empty, we fall back to "first agent internal note".
AGENT_BOT_EMAIL = _env.get("FEEDBACK_BOT_EMAIL", "").lower()
AGENT_BOT_USER_ID = _env.get("FEEDBACK_BOT_USER_ID", "")

# Poll window overlap (seconds) so boundary tickets are never skipped.
POLL_OVERLAP_SECONDS = int(_env.get("FEEDBACK_POLL_OVERLAP_SECONDS", "120"))

# v1 scope guard: only auto-capture clean single-exchange tickets. Multi-turn
# threads are flagged and skipped by default (set to "1" to capture-and-flag).
CAPTURE_MULTI_TURN = _env.get("FEEDBACK_CAPTURE_MULTI_TURN", "0") == "1"

# Optional file of known macro/template signatures (one substring per line). Any
# human reply containing one of these is treated as a macro and skipped.
MACRO_SIGNATURES_FILE = pathlib.Path(
    _env.get("FEEDBACK_MACRO_FILE", str(PKG_DIR / "macro_signatures.txt"))
)


def gorgias_configured() -> bool:
    return bool(GORGIAS_BASE and GORGIAS_EMAIL and GORGIAS_KEY)
