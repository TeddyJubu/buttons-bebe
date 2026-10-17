"""Revocable sessions; actor role is resolved from console_users on each use."""
from __future__ import annotations

import time
from pathlib import Path
from .db import Database
from .console_auth import SessionClaims
from . import user_store

_SCHEMA = """CREATE TABLE IF NOT EXISTS console_sessions (
    token_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    username TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    revoked_at INTEGER,
    version INTEGER NOT NULL
)"""


async def initialize(path: Path | str):
    await Database(path).execute(_SCHEMA, operation="session_schema")
    await user_store.initialize(path)


async def register(claims: SessionClaims, path: Path | str, *, actor_id: str | None = None):
    # Ignore conflicts, particularly revocation tombstones. Never reactivate a
    # copied legacy token when it arrives after its owner's logout.
    resolved = actor_id or ("admin:" + claims.username)
    await Database(path).execute(
        "INSERT OR IGNORE INTO console_sessions(token_id,actor_id,username,expires_at,created_at,version) VALUES(?,?,?,?,?,?)",
        (claims.token_id, resolved, claims.username, claims.expires_at, int(time.time()), claims.version),
        operation="session_register",
    )


async def authenticate(claims: SessionClaims, path: Path | str) -> dict | None:
    if claims.version == 1:
        await register(claims, path)
    rows = await Database(path).fetch(
        "SELECT actor_id,username,expires_at,revoked_at FROM console_sessions WHERE token_id=?",
        (claims.token_id,), operation="session_lookup",
    )
    if not rows:
        return None
    row = rows[0]
    if row["revoked_at"] is not None or row["expires_at"] <= int(time.time()) or row["expires_at"] != claims.expires_at or row["username"] != claims.username:
        return None
    user = await user_store.get_active(claims.username, path)
    if not user:
        return None
    return {
        "actor_id": user_store.actor_id_for(user["role"], user["username"]),
        "actor_role": user["role"],
        "session_id": claims.token_id,
        "username": user["username"],
        "gorgias_email": user["gorgias_email"],
    }


async def revoke(claims: SessionClaims, path: Path | str):
    # Registration before UPDATE covers logout of a valid but unseen v1 token.
    await register(claims, path)
    await Database(path).execute("UPDATE console_sessions SET revoked_at=COALESCE(revoked_at,?) WHERE token_id=?",
                                 (int(time.time()), claims.token_id), operation="session_revoke")


async def revoke_username(username: str, path: Path | str) -> int:
    """Revoke every non-revoked session for a username (password reset / disable)."""
    key = (username or "").strip().casefold()
    if not key:
        return 0
    return int(await Database(path).execute(
        "UPDATE console_sessions SET revoked_at=COALESCE(revoked_at,?) WHERE username=? AND revoked_at IS NULL",
        (int(time.time()), key),
        operation="session_revoke_user",
        return_rowcount=True,
    ) or 0)
