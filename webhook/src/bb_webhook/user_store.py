"""Durable console accounts; env credentials bootstrap the first admin only."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import aiosqlite

from .db import Database
from .logging_utils import get_logger

logger = get_logger(__name__)

ROLES = frozenset({"admin", "staff"})
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,128}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_SCHEMA = """CREATE TABLE IF NOT EXISTS console_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','staff')),
    gorgias_email TEXT NOT NULL DEFAULT '',
    disabled_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)"""


def normalize_username(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > 128 or not _USERNAME_RE.fullmatch(stripped):
        return None
    return stripped.casefold()


def normalize_gorgias_email(value: str | None) -> str | None:
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    stripped = value.strip().casefold()
    if not stripped:
        return ""
    if len(stripped) > 254 or not _EMAIL_RE.fullmatch(stripped):
        return None
    return stripped


def actor_id_for(role: str, username: str) -> str:
    return f"{role}:{username}"


async def initialize(path: Path | str):
    await Database(path).execute(_SCHEMA, operation="user_schema")


async def bootstrap_from_settings(settings, path: Path | str) -> None:
    """Seed the first admin from env when the table is empty."""
    await initialize(path)
    rows = await Database(path).fetch(
        "SELECT 1 AS ok FROM console_users LIMIT 1", operation="user_bootstrap_check"
    )
    if rows:
        return
    username = normalize_username(getattr(settings, "console_username", None))
    password_hash = getattr(settings, "console_password_hash", "") or ""
    if not username or not password_hash:
        return
    raw_email = os.environ.get("INBOX_OPERATOR_GORGIAS_EMAIL", "")
    email = normalize_gorgias_email(raw_email)
    if email is None:
        logger.warning(
            "Bootstrap admin %s: INBOX_OPERATOR_GORGIAS_EMAIL is malformed; seeding empty. "
            "Set the Gorgias email via the users API after login.",
            username,
        )
        email = ""
    elif not email:
        logger.warning(
            "Bootstrap admin %s: INBOX_OPERATOR_GORGIAS_EMAIL unset; seeding empty. "
            "Set the Gorgias email via the users API after login.",
            username,
        )
    now = int(time.time())
    await Database(path).execute(
        "INSERT INTO console_users(username,password_hash,role,gorgias_email,disabled_at,created_at,updated_at) "
        "VALUES(?,?,?,?,NULL,?,?)",
        (username, password_hash, "admin", email, now, now),
        operation="user_bootstrap",
    )


async def get_active(username: str | None, path: Path | str) -> dict | None:
    key = normalize_username(username)
    if not key:
        return None
    rows = await Database(path).fetch(
        "SELECT username,password_hash,role,gorgias_email,disabled_at FROM console_users WHERE username=?",
        (key,),
        operation="user_lookup",
    )
    if not rows:
        return None
    row = rows[0]
    if row["disabled_at"] is not None:
        return None
    if row["role"] not in ROLES:
        return None
    return {
        "username": row["username"],
        "password_hash": row["password_hash"],
        "role": row["role"],
        "gorgias_email": (row["gorgias_email"] or "").casefold(),
    }


async def list_users(path: Path | str) -> list[dict]:
    rows = await Database(path).fetch(
        "SELECT username,role,gorgias_email,disabled_at,created_at,updated_at FROM console_users ORDER BY username",
        operation="user_list",
    )
    return [
        {
            "username": row["username"],
            "role": row["role"],
            "gorgiasEmail": row["gorgias_email"] or "",
            "disabled": row["disabled_at"] is not None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


async def count_active_admins(path: Path | str, *, excluding: str | None = None) -> int:
    if excluding:
        rows = await Database(path).fetch(
            "SELECT COUNT(*) AS count FROM console_users WHERE role='admin' AND disabled_at IS NULL AND username!=?",
            (excluding,),
            operation="user_admin_count",
        )
    else:
        rows = await Database(path).fetch(
            "SELECT COUNT(*) AS count FROM console_users WHERE role='admin' AND disabled_at IS NULL",
            operation="user_admin_count",
        )
    return int(rows[0]["count"]) if rows else 0


async def create_user(
    path: Path | str,
    *,
    username: str,
    password_hash: str,
    role: str,
    gorgias_email: str = "",
) -> dict:
    key = normalize_username(username)
    email = normalize_gorgias_email(gorgias_email)
    if not key or not password_hash or role not in ROLES or email is None:
        raise ValueError("invalid_user")
    if role == "staff" and not email:
        raise ValueError("staff_email_required")
    now = int(time.time())
    try:
        await Database(path).execute(
            "INSERT INTO console_users(username,password_hash,role,gorgias_email,disabled_at,created_at,updated_at) "
            "VALUES(?,?,?,?,NULL,?,?)",
            (key, password_hash, role, email, now, now),
            operation="user_create",
        )
    except aiosqlite.IntegrityError as exc:
        raise ValueError("username_taken") from exc
    return {"username": key, "role": role, "gorgiasEmail": email, "disabled": False}


async def update_user(
    path: Path | str,
    username: str,
    *,
    role: str | None = None,
    gorgias_email: str | None = None,
    password_hash: str | None = None,
    disabled: bool | None = None,
) -> dict:
    key = normalize_username(username)
    if not key:
        raise ValueError("invalid_user")
    if role is not None and role not in ROLES:
        raise ValueError("invalid_role")
    if gorgias_email is not None:
        normalized_email = normalize_gorgias_email(gorgias_email)
        if normalized_email is None:
            raise ValueError("invalid_email")
    else:
        normalized_email = None
    if password_hash is not None and not password_hash:
        raise ValueError("invalid_password")

    async def txn(conn):
        cursor = await conn.execute(
            "SELECT username,role,gorgias_email,disabled_at,password_hash FROM console_users WHERE username=?",
            (key,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        if not rows:
            raise ValueError("not_found")
        current = rows[0]
        new_role = role if role is not None else current["role"]
        if new_role not in ROLES:
            raise ValueError("invalid_role")
        new_email = (current["gorgias_email"] or "") if normalized_email is None else normalized_email
        new_hash = password_hash if password_hash is not None else current["password_hash"]
        if not new_hash:
            raise ValueError("invalid_password")
        if new_role == "staff" and not new_email:
            raise ValueError("staff_email_required")
        currently_disabled = current["disabled_at"] is not None
        will_disable = currently_disabled if disabled is None else bool(disabled)
        was_active_admin = current["role"] == "admin" and not currently_disabled
        remains_active_admin = new_role == "admin" and not will_disable
        if was_active_admin and not remains_active_admin:
            count_cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM console_users WHERE role='admin' AND disabled_at IS NULL AND username!=?",
                (key,),
            )
            count_row = await count_cursor.fetchone()
            await count_cursor.close()
            if int(count_row["count"] if count_row else 0) < 1:
                raise ValueError("last_admin")
        now = int(time.time())
        if disabled is None:
            disabled_at = current["disabled_at"]
        elif disabled:
            disabled_at = current["disabled_at"] or now
        else:
            disabled_at = None
        await conn.execute(
            "UPDATE console_users SET password_hash=?, role=?, gorgias_email=?, disabled_at=?, updated_at=? WHERE username=?",
            (new_hash, new_role, new_email, disabled_at, now, key),
        )
        password_changed = password_hash is not None and password_hash != current["password_hash"]
        newly_disabled = disabled_at is not None and current["disabled_at"] is None
        if password_changed or newly_disabled:
            await conn.execute(
                "UPDATE console_sessions SET revoked_at=COALESCE(revoked_at,?) WHERE username=? AND revoked_at IS NULL",
                (now, key),
            )
        return {
            "username": key,
            "role": new_role,
            "gorgiasEmail": new_email,
            "disabled": disabled_at is not None,
        }

    return await Database(path).transaction(txn, operation="user_update")
