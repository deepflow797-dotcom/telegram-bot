"""Async PostgreSQL access (psycopg 3 + psycopg_pool).

Design notes
------------
* The pool is created lazily and opened on first use, so importing the module
  never touches the network (important for serverless cold starts and tests).
* ``min_size=0`` keeps no idle connections, which matters on serverless
  platforms and on Supabase's connection limits.
* ``prepare_threshold=None`` disables server-side prepared statements so the
  same URL also works with Supabase's transaction-mode pooler (pgbouncer).
* Rows are returned as ``dict`` objects; NUMERIC becomes ``Decimal``.
* Every connection is pointed at the active account schema (``acct_<id>``)
  through ``search_path`` before a query runs, which keeps the datasets of
  different Telegram accounts physically separated. Connections run in
  autocommit mode so that this session setting is visible to every later
  statement; ``transaction()`` still gives real atomic blocks.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import redact_dsn
from app.db.context import current_account_id, schema_for

logger = logging.getLogger(__name__)


class Database:
    """Thin wrapper around an async connection pool."""

    def __init__(self, dsn: str, *, min_size: int = 0, max_size: int = 4) -> None:
        self.dsn = dsn
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=10,
            max_idle=60,
            check=AsyncConnectionPool.check_connection,
            kwargs={
                "row_factory": dict_row,
                "prepare_threshold": None,
                "connect_timeout": 10,
                "application_name": "rental-bot",
                "autocommit": True,
            },
        )
        self._opened = False

    # -- lifecycle ---------------------------------------------------------
    async def open(self) -> None:
        if not self._opened:
            await self._pool.open(wait=False)
            self._opened = True
            logger.info("database pool opening", extra={"extra_fields": {"dsn": redact_dsn(self.dsn)}})

    async def close(self) -> None:
        if self._opened:
            await self._pool.close()
            self._opened = False

    async def __aenter__(self) -> Database:
        await self.open()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -- queries -----------------------------------------------------------
    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        await self.open()
        async with self._pool.connection() as conn:
            await self._select_account(conn)
            yield conn

    @staticmethod
    async def _select_account(conn: AsyncConnection) -> None:
        """Point this connection at the active account's schema."""
        path = f"{schema_for(current_account_id())}, public"
        await conn.execute("SELECT set_config('search_path', %s, false)", (path,))

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """Run a block in an explicit transaction (commit on success)."""
        async with self.connection() as conn, conn.transaction():
            yield conn

    async def fetch_all(self, sql: str, params: tuple | dict | None = None) -> list[dict]:
        async with self.connection() as conn:
            cur = await conn.execute(sql, params)
            return list(await cur.fetchall())

    async def fetch_one(self, sql: str, params: tuple | dict | None = None) -> dict | None:
        async with self.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def fetch_value(self, sql: str, params: tuple | dict | None = None) -> Any:
        async with self.connection() as conn:
            cur = await conn.execute(sql, params)
            row = await cur.fetchone()
            if row is None:
                return None
            return next(iter(row.values()))

    async def fetch_val(self, sql: str, params: tuple | dict | None = None) -> Any:
        """Alias of :meth:`fetch_value` (shorter name used in tests)."""
        return await self.fetch_value(sql, params)

    async def execute(self, sql: str, params: tuple | dict | None = None) -> int:
        async with self.connection() as conn:
            cur = await conn.execute(sql, params)
            return cur.rowcount

    async def schema_ready(self) -> bool:
        """True when the application tables exist (migrations applied)."""
        try:
            exists = await self.fetch_value(
                """
                SELECT to_regclass('public.accounts') IS NOT NULL
                   AND to_regclass('acct_1.buildings') IS NOT NULL
                """
            )
            return bool(exists)
        except Exception:  # pragma: no cover - depends on network
            logger.exception("schema check failed")
            return False

    async def ping(self) -> bool:
        try:
            await self.fetch_value("SELECT 1")
            return True
        except Exception:  # pragma: no cover - depends on network
            logger.exception("database ping failed")
            return False
