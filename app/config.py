"""Application configuration.

Every value comes from environment variables. Secrets are never written to
disk, logged or committed. ``Settings.from_env`` collects *all* problems so the
operator sees the complete list in one go instead of fixing them one by one.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Africa/Nouakchott"  # UTC+0, no daylight saving
DEFAULT_CURRENCY = "MRU"  # Mauritanian ouguiya

_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class ConfigError(RuntimeError):
    """Raised when the environment is not usable."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_owner_id: int
    telegram_webhook_secret: str
    database_url: str
    cron_secret: str
    app_timezone: str = DEFAULT_TIMEZONE
    default_currency: str = DEFAULT_CURRENCY
    webhook_url: str | None = None
    log_level: str = "INFO"
    # When true, the first request migrates the database (if needed) and
    # registers TELEGRAM_WEBHOOK_URL-less WEBHOOK_URL with Telegram.
    auto_setup: bool = False
    # Optional: kept for documentation / future Supabase REST usage.
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    extras: dict[str, str] = field(default_factory=dict, repr=False)

    # -- derived -----------------------------------------------------------
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def owner_id(self) -> int:
        return self.telegram_owner_id

    # -- loading -----------------------------------------------------------
    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        problems: list[str] = []

        def get(name: str) -> str:
            return (env.get(name) or "").strip()

        token = get("TELEGRAM_BOT_TOKEN")
        if not token:
            problems.append("TELEGRAM_BOT_TOKEN is missing")
        elif not _TOKEN_RE.match(token):
            problems.append("TELEGRAM_BOT_TOKEN does not look like a bot token")

        owner_raw = get("TELEGRAM_OWNER_ID")
        owner_id = 0
        if not owner_raw:
            problems.append("TELEGRAM_OWNER_ID is missing")
        elif not owner_raw.isdigit() or int(owner_raw) <= 0:
            problems.append("TELEGRAM_OWNER_ID must be a positive integer Telegram user id")
        else:
            owner_id = int(owner_raw)

        webhook_secret = get("TELEGRAM_WEBHOOK_SECRET")
        if not webhook_secret:
            problems.append("TELEGRAM_WEBHOOK_SECRET is missing")
        elif not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", webhook_secret):
            problems.append(
                "TELEGRAM_WEBHOOK_SECRET must be 16-256 chars of A-Z a-z 0-9 _ -"
            )

        database_url = get("DATABASE_URL") or get("SUPABASE_DB_URL")
        if not database_url:
            problems.append(
                "DATABASE_URL is missing (PostgreSQL connection string, "
                "e.g. from Supabase → Settings → Database → Connection string)"
            )
        elif not database_url.startswith(("postgres://", "postgresql://")):
            problems.append("DATABASE_URL must start with postgresql://")

        cron_secret = get("CRON_SECRET")
        if not cron_secret:
            problems.append("CRON_SECRET is missing")
        elif len(cron_secret) < 16:
            problems.append("CRON_SECRET must be at least 16 characters")

        tz_name = get("APP_TIMEZONE") or DEFAULT_TIMEZONE
        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            problems.append(f"APP_TIMEZONE is not a valid IANA timezone: {tz_name}")

        currency = (get("DEFAULT_CURRENCY") or DEFAULT_CURRENCY).upper()
        if not _CURRENCY_RE.match(currency):
            problems.append("DEFAULT_CURRENCY must be a 3-letter ISO code such as MRU")

        if problems:
            raise ConfigError(problems)

        return cls(
            telegram_bot_token=token,
            telegram_owner_id=owner_id,
            telegram_webhook_secret=webhook_secret,
            database_url=database_url,
            cron_secret=cron_secret,
            app_timezone=tz_name,
            default_currency=currency,
            webhook_url=(get("WEBHOOK_URL") or None),
            log_level=(get("LOG_LEVEL") or "INFO").upper(),
            auto_setup=(get("AUTO_SETUP") or "false").lower() in {"1", "true", "yes", "on"},
            supabase_url=(get("SUPABASE_URL") or None),
            supabase_service_role_key=(get("SUPABASE_SERVICE_ROLE_KEY") or None),
        )


def redact_dsn(dsn: str) -> str:
    """Return a DSN safe for logs (password removed)."""
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", dsn)
