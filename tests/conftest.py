"""Test fixtures: a real PostgreSQL instance (embedded) per test session."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator

import httpx
import pgserver
import pytest
import pytest_asyncio

from app.config import Settings
from app.db.connection import Database
from app.db.migrate import apply_migrations

# Business tables exist once per account schema (``acct_<id>``).
ACCOUNT_TABLES = [
    "payment_allocations",
    "payments",
    "pending_payments",
    "daily_reports",
    "audit_log",
    "tenants",
    "apartments",
    "buildings",
    "app_settings",
]

# Platform tables live in ``public`` and are shared by every account.
PLATFORM_TABLES = ["processed_updates", "bot_fsm_states", "accounts", "platform_settings"]

OWNER_ID = 111222333

TEST_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456789:AAHtest-token-for-unit-tests-only_123",
    "TELEGRAM_OWNER_ID": str(OWNER_ID),
    "TELEGRAM_WEBHOOK_SECRET": "test-webhook-secret-0123456789",
    "CRON_SECRET": "test-cron-secret-0123456789",
    "APP_TIMEZONE": "Africa/Nouakchott",
    "DEFAULT_CURRENCY": "MRU",
}


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    data_dir = tempfile.mkdtemp(prefix="rental-test-pg-")
    server = pgserver.get_server(data_dir)
    dsn = server.get_uri()
    asyncio.run(apply_migrations(dsn))
    yield dsn
    try:
        server.cleanup()
    except Exception:  # noqa: BLE001 - best effort in CI
        pass


async def _reset_all_accounts(database: Database) -> None:
    """Empty every per-account schema plus the shared platform tables."""
    await database.execute("SELECT app_provision_account(1)")
    schemas = await database.fetch_all(
        "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'acct\\_%'"
    )
    for row in schemas:
        schema = row["nspname"]
        targets = ", ".join(f'{schema}."{table}"' for table in ACCOUNT_TABLES)
        await database.execute(f"TRUNCATE {targets} RESTART IDENTITY CASCADE")
    await database.execute(
        "TRUNCATE " + ", ".join(PLATFORM_TABLES) + " RESTART IDENTITY CASCADE"
    )
    # Recreate the owner row so tests never reuse its id (= account 1).
    from app.db import platform

    await platform.ensure_owner_account(database, OWNER_ID)


@pytest_asyncio.fixture
async def db(postgres_dsn: str) -> AsyncIterator[Database]:
    database = Database(postgres_dsn, min_size=1, max_size=4)
    await database.open()
    await _reset_all_accounts(database)
    await database.execute(
        """
        INSERT INTO app_settings (key, value) VALUES
            ('currency', 'MRU'), ('report_enabled', 'true'),
            ('report_time', '08:00'), ('upcoming_days', '3')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """
    )
    yield database
    await database.close()


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env({**TEST_ENV, "DATABASE_URL": "postgresql://user:pass@localhost:5432/db"})


@pytest_asyncio.fixture
async def web(postgres_dsn, db):
    """A live ASGI app with a fake Telegram session, sharing the test database."""
    from app.config import Settings
    from app.web.app import create_app
    from tests.fake_bot import attach_fake_session

    settings = Settings.from_env({**TEST_ENV, "DATABASE_URL": postgres_dsn})
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        session = attach_fake_session(app)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield app, client, session
