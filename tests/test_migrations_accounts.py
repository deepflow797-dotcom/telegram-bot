"""Upgrade path: an existing single-owner database becomes account 1."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pgserver
import psycopg
import pytest
from psycopg.rows import dict_row

from app.db.migrate import MIGRATIONS_DIR, apply_migrations


@pytest.fixture()
def legacy_dsn(tmp_path: Path):
    """A database that only has the original 0001 schema, with real data."""
    only_0001 = tmp_path / "migrations"
    only_0001.mkdir()
    shutil.copy(MIGRATIONS_DIR / "0001_init.sql", only_0001 / "0001_init.sql")

    data_dir = tempfile.mkdtemp(prefix="rental-legacy-pg-")
    server = pgserver.get_server(data_dir)
    dsn = server.get_uri()
    asyncio.run(apply_migrations(dsn, directory=only_0001))

    async def seed() -> None:
        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
            await conn.execute(
                "INSERT INTO buildings (name, address) VALUES ('مبنى قديم', 'شارع 1')"
            )
            await conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ('currency', 'MRU') "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            )
            await conn.commit()

    asyncio.run(seed())
    yield dsn
    try:
        server.cleanup()
    except Exception:  # noqa: BLE001 - best effort
        pass


def test_0002_moves_the_owner_data_into_acct_1(legacy_dsn: str) -> None:
    applied = asyncio.run(apply_migrations(legacy_dsn))
    assert applied == ["0002_accounts"]

    async def check() -> None:
        async with await psycopg.AsyncConnection.connect(
            legacy_dsn, row_factory=dict_row
        ) as conn:
            owner = await (
                await conn.execute("SELECT * FROM public.accounts WHERE id = 1")
            ).fetchone()
            assert owner is not None
            assert owner["plan"] == "owner"
            assert owner["status"] == "active"

            building = await (
                await conn.execute("SELECT * FROM acct_1.buildings")
            ).fetchone()
            assert building["name"] == "مبنى قديم"

            legacy = await (
                await conn.execute("SELECT to_regclass('public.buildings') AS t")
            ).fetchone()
            assert legacy["t"] is None

            currency = await (
                await conn.execute(
                    "SELECT value FROM acct_1.app_settings WHERE key = 'currency'"
                )
            ).fetchone()
            assert currency["value"] == "MRU"

            phone = await (
                await conn.execute(
                    "SELECT value FROM public.platform_settings WHERE key = 'owner_phone'"
                )
            ).fetchone()
            assert phone["value"] == "22222222"

    asyncio.run(check())


def test_new_account_schema_is_created_empty(legacy_dsn: str) -> None:
    asyncio.run(apply_migrations(legacy_dsn))

    async def check() -> None:
        async with await psycopg.AsyncConnection.connect(
            legacy_dsn, row_factory=dict_row
        ) as conn:
            await conn.execute("SELECT app_provision_account(7)")
            count = await (
                await conn.execute("SELECT count(*) AS n FROM acct_7.buildings")
            ).fetchone()
            assert count["n"] == 0
            currency = await (
                await conn.execute(
                    "SELECT value FROM acct_7.app_settings WHERE key = 'currency'"
                )
            ).fetchone()
            assert currency["value"] == "MRU"
            prompt = await (
                await conn.execute(
                    "SELECT value FROM acct_7.app_settings WHERE key = 'prepayment_prompt'"
                )
            ).fetchone()
            assert prompt["value"] == "true"
            await conn.commit()

    asyncio.run(check())
