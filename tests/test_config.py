"""Configuration validation and secret hygiene."""

from __future__ import annotations

import pytest

from app.config import ConfigError, Settings, redact_dsn
from tests.conftest import TEST_ENV

BASE = {**TEST_ENV, "DATABASE_URL": "postgresql://user:secret@localhost:5432/db"}


def test_valid_settings() -> None:
    cfg = Settings.from_env(BASE)
    assert cfg.telegram_owner_id == 111222333
    assert cfg.app_timezone == "Africa/Nouakchott"
    assert cfg.default_currency == "MRU"
    assert cfg.tz.key == "Africa/Nouakchott"


@pytest.mark.parametrize(
    "missing",
    [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_OWNER_ID",
        "TELEGRAM_WEBHOOK_SECRET",
        "DATABASE_URL",
        "CRON_SECRET",
    ],
)
def test_missing_required_variables(missing: str) -> None:
    env = {k: v for k, v in BASE.items() if k != missing}
    with pytest.raises(ConfigError) as exc:
        Settings.from_env(env)
    assert any(missing in problem for problem in exc.value.problems)


def test_all_problems_reported_at_once() -> None:
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({})
    assert len(exc.value.problems) >= 5


def test_invalid_values() -> None:
    env = {
        **BASE,
        "TELEGRAM_OWNER_ID": "abc",
        "TELEGRAM_WEBHOOK_SECRET": "short",
        "CRON_SECRET": "tiny",
        "APP_TIMEZONE": "Mars/Olympus",
        "DEFAULT_CURRENCY": "Riyal",
        "DATABASE_URL": "mysql://nope",
    }
    with pytest.raises(ConfigError) as exc:
        Settings.from_env(env)
    problems = " ".join(exc.value.problems)
    assert "TELEGRAM_OWNER_ID" in problems
    assert "TELEGRAM_WEBHOOK_SECRET" in problems
    assert "CRON_SECRET" in problems
    assert "APP_TIMEZONE" in problems
    assert "DEFAULT_CURRENCY" in problems
    assert "DATABASE_URL" in problems


def test_defaults_are_applied() -> None:
    env = {k: v for k, v in BASE.items() if k not in {"APP_TIMEZONE", "DEFAULT_CURRENCY"}}
    cfg = Settings.from_env(env)
    assert cfg.app_timezone == "Africa/Nouakchott"
    assert cfg.default_currency == "MRU"


def test_supabase_db_url_alias() -> None:
    env = {k: v for k, v in BASE.items() if k != "DATABASE_URL"}
    env["SUPABASE_DB_URL"] = "postgresql://user:pass@localhost:5432/db"
    cfg = Settings.from_env(env)
    assert cfg.database_url.startswith("postgresql://")


def test_redact_dsn() -> None:
    assert redact_dsn("postgresql://user:supersecret@host:5432/db") == (
        "postgresql://user:***@host:5432/db"
    )
