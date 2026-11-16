"""Test fixtures: a real PostgreSQL instance (embedded) per test session."""

from __future__ import annotations

import asyncio
import tempfile
from typing import AsyncIterator

import pgserver
import pytest
import pytest_asyncio

from app.config import Settings
from app.db.connection import Database
from app.db.migrate import apply_migrations

TABLES = [
    "payment_allocations",
    "payments",
    "pending_payments",
    "daily_reports",
    "audit_log",
    "processed_updates",
    "bot_fsm_states",
    "tenants",
    "apartments",
    "buildings",
    "app_settings",
]

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


@pytest_asyncio.fixture
async def db(postgres_dsn: str) -> AsyncIterator[Database]:
    database = Database(postgres_dsn, min_size=1, max_size=4)
    await database.open()
    await database.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
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
