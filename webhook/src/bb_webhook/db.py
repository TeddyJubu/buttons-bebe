"""Small retrying SQLite access wrapper used by the webhook database layer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

import aiosqlite

from .config import get_settings
from .logging_utils import get_logger

logger = get_logger(__name__)

_T = TypeVar("_T")

_LOCK_RETRY_ATTEMPTS = 5
_LOCK_RETRY_DELAY = 0.15  # seconds


class Database:
    """Short-lived SQLite statements and explicit atomic transactions."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else get_settings().db_path_absolute

    async def execute(
        self,
        sql: str,
        params: tuple = (),
        *,
        operation: str = "database",
        fetch: bool = False,
        return_rowcount: bool = False,
    ) -> Any | None:
        """Execute *sql*, retrying transient SQLite lock errors.

        Reads return ``list[aiosqlite.Row]``. Writes return ``lastrowid`` by
        default, or ``cursor.rowcount`` when ``return_rowcount`` is requested.
        The latter is required for idempotency and atomic queue claims.
        """

        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            try:
                async with aiosqlite.connect(str(self.path)) as conn:
                    await conn.execute("PRAGMA busy_timeout=5000")
                    if fetch:
                        conn.row_factory = aiosqlite.Row
                        cursor = await conn.execute(sql, params)
                        result = await cursor.fetchall()
                        await cursor.close()
                        return result

                    cursor = await conn.execute(sql, params)
                    affected = cursor.rowcount
                    row = cursor.lastrowid
                    await conn.commit()
                    await cursor.close()
                    return affected if return_rowcount else row
            except aiosqlite.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < _LOCK_RETRY_ATTEMPTS - 1:
                    logger.warning(
                        "DB locked on %s — retry %d/%d",
                        operation,
                        attempt + 1,
                        _LOCK_RETRY_ATTEMPTS,
                    )
                    await asyncio.sleep(_LOCK_RETRY_DELAY)
                    continue
                raise
        return None

    async def transaction(
        self,
        callback: Callable[[aiosqlite.Connection], Awaitable[_T]],
        *,
        operation: str = "transaction",
    ) -> _T:
        """Commit a short database-only callback atomically, or roll it all back.

        Retry the entire transaction on contention. Callbacks must contain no
        external side effects: they may run more than once. BEGIN IMMEDIATE
        serializes competing writers before they make deduplication decisions.
        Cancellation is also rolled back; an acknowledgement is safe only
        after this method returns from commit.
        """
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            try:
                async with aiosqlite.connect(str(self.path)) as conn:
                    conn.row_factory = aiosqlite.Row
                    await conn.execute("PRAGMA busy_timeout=5000")
                    try:
                        await conn.execute("BEGIN IMMEDIATE")
                        result = await callback(conn)
                        await conn.commit()
                        return result
                    except BaseException:
                        await conn.rollback()
                        raise
            except aiosqlite.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == _LOCK_RETRY_ATTEMPTS - 1:
                    raise
                logger.warning("DB locked on %s — transaction retry %d/%d",
                               operation, attempt + 1, _LOCK_RETRY_ATTEMPTS)
                await asyncio.sleep(_LOCK_RETRY_DELAY)
        raise RuntimeError("Database transaction retry exhausted")

    async def fetch(self, sql: str, params: tuple = (), *, operation: str = "database") -> list[Any]:
        """Fetch all rows for a SELECT statement."""

        return await self.execute(sql, params, operation=operation, fetch=True) or []
