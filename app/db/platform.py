"""Accounts, subscriptions, free trials and platform settings.

The platform owner (``TELEGRAM_OWNER_ID``) is always account 1. Any other
Telegram account that starts the bot gets a **free trial** (7 days by default,
configurable by the owner) with its own empty PostgreSQL schema. When the trial
or the subscription ends the account is frozen — data kept, renewal screen.

If the owner sets the trial to 0 days, a new account instead becomes a
*request* with status ``pending`` that the owner approves or rejects.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable
from typing import Any

from app.db.connection import Database
from app.logging_config import log_event

logger = logging.getLogger(__name__)

OWNER_ACCOUNT_ID = 1

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_FROZEN = "frozen"
STATUS_REJECTED = "rejected"

STATUS_LABELS = {
    STATUS_PENDING: "بانتظار التفعيل",
    STATUS_ACTIVE: "نشط",
    STATUS_FROZEN: "موقوف",
    STATUS_REJECTED: "مرفوض",
}

PLAN_OWNER = "owner"
PLAN_TRIAL = "trial"
PLAN_STANDARD = "standard"

TRIAL_DAYS_KEY = "trial_days"
DEFAULT_TRIAL_DAYS = 7
MAX_TRIAL_DAYS = 365

PLATFORM_DEFAULTS = {
    "owner_phone": "22222222",
    TRIAL_DAYS_KEY: str(DEFAULT_TRIAL_DAYS),
    "contact_note": (
        "أرسل اسم المستخدم (اليوزر) الخاص بك لهذا الرقم، وسيتم تفعيل البوت لك، "
        "وستصلك رسالة تأكيد."
    ),
}


# ---------------------------------------------------------------------------
# platform settings (owner contact …)
# ---------------------------------------------------------------------------
async def get_platform_settings(db: Database) -> dict[str, str]:
    settings = dict(PLATFORM_DEFAULTS)
    try:
        rows = await db.fetch_all("SELECT key, value FROM platform_settings")
    except Exception:  # noqa: BLE001 - table may not exist before migration
        return settings
    settings.update({row["key"]: row["value"] for row in rows})
    return settings


async def set_platform_setting(db: Database, key: str, value: str) -> None:
    await db.execute(
        """
        INSERT INTO platform_settings (key, value, updated_at) VALUES (%s, %s, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        (key, str(value)),
    )


def trial_days_from(settings: dict[str, str]) -> int:
    """Trial length in days (0 disables the trial and enables the request flow)."""
    raw = str(settings.get(TRIAL_DAYS_KEY, DEFAULT_TRIAL_DAYS)).strip()
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TRIAL_DAYS
    return max(0, min(days, MAX_TRIAL_DAYS))


async def get_trial_days(db: Database) -> int:
    return trial_days_from(await get_platform_settings(db))


async def set_trial_days(db: Database, days: int) -> int:
    value = max(0, min(int(days), MAX_TRIAL_DAYS))
    await set_platform_setting(db, TRIAL_DAYS_KEY, str(value))
    return value


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------
async def ensure_owner_account(
    db: Database, telegram_id: int | None, display_name: str | None = None
) -> dict | None:
    """Bind account 1 to the configured owner id and make sure its schema exists."""
    await db.execute(
        """
        INSERT INTO accounts (id, telegram_id, display_name, status, plan, approved_at)
        VALUES (%s, %s, %s, 'active', 'owner', now())
        ON CONFLICT (id) DO UPDATE
            SET telegram_id = COALESCE(EXCLUDED.telegram_id, accounts.telegram_id),
                status = 'active',
                updated_at = now()
        """,
        (OWNER_ACCOUNT_ID, telegram_id, display_name or "المالك"),
    )
    # The owner row uses an explicit id, so the sequence has to catch up —
    # otherwise the very first customer request would try to reuse id 1.
    await db.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('accounts', 'id'),
            GREATEST((SELECT COALESCE(max(id), 0) FROM accounts), 1)
        )
        """
    )
    await db.execute("SELECT app_provision_account(%s)", (OWNER_ACCOUNT_ID,))
    return await get_account(db, OWNER_ACCOUNT_ID)


async def get_account(db: Database, account_id: int) -> dict | None:
    return await db.fetch_one("SELECT * FROM accounts WHERE id = %s", (account_id,))


async def get_account_by_telegram(db: Database, telegram_id: int) -> dict | None:
    return await db.fetch_one(
        "SELECT * FROM accounts WHERE telegram_id = %s", (telegram_id,)
    )


async def register_request(
    db: Database, *, telegram_id: int, display_name: str | None, username: str | None
) -> tuple[dict, bool]:
    """Create (or refresh) a pending account. Returns ``(account, is_new)``."""
    existing = await get_account_by_telegram(db, telegram_id)
    if existing is not None:
        await db.execute(
            """
            UPDATE accounts
            SET display_name = COALESCE(%s, display_name),
                username = COALESCE(%s, username),
                last_seen_at = now(),
                updated_at = now()
            WHERE id = %s
            """,
            (display_name, username, existing["id"]),
        )
        return await get_account(db, existing["id"]), False

    row = await db.fetch_one(
        """
        INSERT INTO accounts (telegram_id, display_name, username, status, last_seen_at)
        VALUES (%s, %s, %s, 'pending', now())
        RETURNING *
        """,
        (telegram_id, display_name, username),
    )
    log_event(logger, "new subscription request", account_id=row["id"], telegram_id=telegram_id)
    return row, True


async def list_accounts(
    db: Database, statuses: Iterable[str] | None = None, limit: int = 50
) -> list[dict]:
    if statuses:
        return await db.fetch_all(
            "SELECT * FROM accounts WHERE status = ANY(%s) ORDER BY created_at DESC LIMIT %s",
            (list(statuses), limit),
        )
    return await db.fetch_all(
        "SELECT * FROM accounts ORDER BY created_at DESC LIMIT %s", (limit,)
    )


async def start_trial(
    db: Database,
    *,
    telegram_id: int,
    display_name: str | None,
    username: str | None,
    days: int | None = None,
    today: dt.date | None = None,
) -> tuple[dict | None, bool]:
    """Activate a brand-new account on a free trial; returns ``(account, is_new)``."""
    existing = await get_account_by_telegram(db, telegram_id)
    if existing is not None:
        return existing, False

    length = await get_trial_days(db) if days is None else max(0, int(days))
    today = today or dt.date.today()
    paid_until = today + dt.timedelta(days=length)

    row = await db.fetch_one(
        """
        INSERT INTO accounts (telegram_id, display_name, username, status, plan,
                              paid_until, approved_at, last_seen_at)
        VALUES (%s, %s, %s, 'active', %s, %s, now(), now())
        ON CONFLICT (telegram_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                username = EXCLUDED.username,
                last_seen_at = now()
        RETURNING *
        """,
        (telegram_id, display_name, username, PLAN_TRIAL, paid_until),
    )
    if row is None:
        return None, False

    if row["status"] != STATUS_ACTIVE or row["paid_until"] != paid_until:
        # a row existed concurrently / already known: never overwrite it
        return row, False

    await db.execute("SELECT app_provision_account(%s)", (row["id"],))
    log_event(logger, "free trial started", account_id=row["id"], days=length)
    return row, True


async def approve_account(
    db: Database, account_id: int, *, days: int = 30, today: dt.date | None = None
) -> dict | None:
    """Activate an account, provision its private dataset and set the expiry."""
    account = await get_account(db, account_id)
    if account is None:
        return None
    today = today or dt.date.today()
    base = account.get("paid_until") or today
    if isinstance(base, dt.datetime):
        base = base.date()
    start = max(today, base)
    paid_until = start + dt.timedelta(days=days)

    await db.execute("SELECT app_provision_account(%s)", (account_id,))
    await db.execute(
        """
        UPDATE accounts
        SET status = 'active', paid_until = %s, plan = %s,
            approved_at = now(), updated_at = now()
        WHERE id = %s
        """,
        (paid_until, PLAN_STANDARD, account_id),
    )
    log_event(logger, "account approved", account_id=account_id, paid_until=str(paid_until))
    return await get_account(db, account_id)


async def set_account_status(
    db: Database, account_id: int, status: str, *, paid_until: dt.date | None = None
) -> dict | None:
    if status not in STATUS_LABELS:
        raise ValueError(f"unknown account status: {status}")
    await db.execute(
        """
        UPDATE accounts
        SET status = %s,
            paid_until = COALESCE(%s, paid_until),
            updated_at = now()
        WHERE id = %s
        """,
        (status, paid_until, account_id),
    )
    return await get_account(db, account_id)


async def touch_account(db: Database, account_id: int) -> None:
    await db.execute(
        "UPDATE accounts SET last_seen_at = now() WHERE id = %s", (account_id,)
    )


def effective_status(account: dict, today: dt.date) -> str:
    """Status after applying the subscription expiry (freeze, never delete)."""
    status = account.get("status") or STATUS_PENDING
    if status == STATUS_ACTIVE and account.get("paid_until"):
        paid_until = account["paid_until"]
        if isinstance(paid_until, dt.datetime):
            paid_until = paid_until.date()
        if paid_until < today:
            return STATUS_FROZEN
    return status


async def active_accounts(db: Database, today: dt.date) -> list[dict]:
    """Accounts that should receive the daily report."""
    rows = await list_accounts(db, statuses=[STATUS_ACTIVE], limit=500)
    return [row for row in rows if effective_status(row, today) == STATUS_ACTIVE]


def days_left(account: dict, today: dt.date) -> int | None:
    paid_until = account.get("paid_until")
    if not paid_until:
        return None
    if isinstance(paid_until, dt.datetime):
        paid_until = paid_until.date()
    return (paid_until - today).days


def public_view(account: dict) -> dict[str, Any]:
    """Account fields that are safe to show to the platform owner."""
    return {
        "id": account["id"],
        "telegram_id": account.get("telegram_id"),
        "display_name": account.get("display_name"),
        "username": account.get("username"),
        "status": account.get("status"),
        "paid_until": account.get("paid_until"),
    }
