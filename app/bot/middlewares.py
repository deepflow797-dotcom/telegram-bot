"""Access control for the multi-account bot plus dependency injection.

Rules
-----
* The platform owner (``TELEGRAM_OWNER_ID``) is account 1 and always has access.
* Any other Telegram user becomes a *request* (status ``pending``) the first
  time they write to the bot. Until the owner approves it they only ever see
  the contact screen — no handler runs and no data query happens.
* Approved accounts work on their own private dataset (own PostgreSQL schema),
  so a customer can never reach the owner's buildings, tenants or payments.
* When a subscription expires the account is *frozen*: the data stays intact,
  the user sees a renewal screen, and nothing else runs.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.bot import texts
from app.bot.menu_context import reset_owner_menu, set_owner_menu
from app.bot.utils import today as today_of
from app.config import Settings
from app.db import platform
from app.db.connection import Database
from app.db.context import reset_current_account, set_current_account
from app.db.repository import get_app_settings
from app.logging_config import log_event

logger = logging.getLogger(__name__)

_OWNER_CACHE_TTL = 120.0
_owner_cache: dict[int, tuple[float, dict]] = {}


def _keyboard(rows: list[list[tuple[str, str]]]):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


async def _owner_account(db: Database, cfg: Settings, user: User) -> dict:
    now = time.monotonic()
    cached = _owner_cache.get(user.id)
    if cached and now - cached[0] < _OWNER_CACHE_TTL:
        return cached[1]
    account = await platform.ensure_owner_account(db, user.id, user.full_name)
    _owner_cache[user.id] = (now, account)
    return account


class AccessMiddleware(BaseMiddleware):
    """Resolve the account of the sender and gate every update."""

    def __init__(self, db: Database, cfg: Settings) -> None:
        self.db = db
        self.cfg = cfg

    # -- helpers -----------------------------------------------------------
    async def _contact_text(self, account: dict) -> str:
        settings = await platform.get_platform_settings(self.db)
        return texts.PENDING_REQUEST.format(
            phone=settings.get("owner_phone", ""),
            note=settings.get("contact_note", ""),
            account_id=account["id"],
        )

    async def _send_screen(self, event: TelegramObject, text: str, reply_markup=None) -> None:
        try:
            if isinstance(event, CallbackQuery):
                await event.answer()
                if event.message is not None:
                    await event.message.answer(text, reply_markup=reply_markup)
            elif isinstance(event, Message):
                await event.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.exception("could not deliver the access screen")

    async def _notify_owner_new_request(self, data: dict, account: dict, user: User) -> None:
        bot = data.get("bot")
        if bot is None:
            return
        who = account.get("display_name") or user.full_name
        handle = f"@{account['username']}" if account.get("username") else "بدون يوزر"
        text = (
            "📥 <b>طلب تفعيل جديد</b>\n\n"
            f"الاسم: {who}\n"
            f"اليوزر: {handle}\n"
            f"رقم الحساب: <code>{account['id']}</code>\n"
            f"معرّف تيليجرام: <code>{user.id}</code>"
        )
        try:
            await bot.send_message(
                self.cfg.owner_id,
                text,
                reply_markup=_keyboard(
                    [
                        [("✅ تفعيل 30 يوم", f"adm:ok:{account['id']}:30")],
                        [("✅ تفعيل 90 يوم", f"adm:ok:{account['id']}:90")],
                        [("❌ رفض", f"adm:no:{account['id']}")],
                    ]
                ),
            )
        except Exception:
            logger.exception("could not notify the owner about a new request")

    # -- entry point -------------------------------------------------------
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return None

        is_owner = user.id == self.cfg.owner_id
        try:
            if is_owner:
                account = await _owner_account(self.db, self.cfg, user)
            else:
                account = await platform.get_account_by_telegram(self.db, user.id)
                if account is None:
                    account, is_new = await platform.register_request(
                        self.db,
                        telegram_id=user.id,
                        display_name=user.full_name,
                        username=user.username,
                    )
                    if is_new:
                        await self._notify_owner_new_request(data, account, user)
        except Exception:
            logger.exception("access middleware failed to resolve the account")
            await self._send_screen(event, texts.ACCESS_TEMPORARY_ERROR)
            return None

        if account is None:  # pragma: no cover - defensive
            await self._send_screen(event, texts.ACCESS_TEMPORARY_ERROR)
            return None

        today = today_of(self.cfg)
        status = platform.effective_status(account, today)

        if not is_owner and status != platform.STATUS_ACTIVE:
            if isinstance(event, CallbackQuery) and (event.data or "") == "acc:check":
                await self._answer_check(event, account, today)
                return None
            log_event(
                logger,
                "blocked update for inactive account",
                account_id=account["id"],
                status=status,
                event=type(event).__name__,
            )
            if status == platform.STATUS_REJECTED:
                await self._send_screen(event, texts.REQUEST_REJECTED)
            elif status == platform.STATUS_FROZEN:
                await self._send_screen(
                    event, await self._frozen_text(account, today), _keyboard([[("🔄 تحقق", "acc:check")]])
                )
            else:
                await self._send_screen(
                    event, await self._contact_text(account), _keyboard([[("🔄 تحقق", "acc:check")]])
                )
            return None

        token = set_current_account(account["id"])
        menu_token = set_owner_menu(is_owner)
        data["account"] = account
        data["is_owner"] = is_owner
        data["account_status"] = status
        try:
            return await handler(event, data)
        finally:
            reset_owner_menu(menu_token)
            reset_current_account(token)

    async def _answer_check(self, event: CallbackQuery, account: dict, today) -> None:
        status = platform.effective_status(account, today)
        left = platform.days_left(account, today)
        label = platform.STATUS_LABELS.get(status, status)
        suffix = f" — متبقي {left} يوم" if left is not None else ""
        try:
            await event.answer(f"الحالة: {label}{suffix}", show_alert=True)
        except Exception:  # noqa: BLE001
            pass

    async def _frozen_text(self, account: dict, today) -> str:
        settings = await platform.get_platform_settings(self.db)
        left = platform.days_left(account, today)
        return texts.SUBSCRIPTION_FROZEN.format(
            phone=settings.get("owner_phone", ""),
            note=settings.get("contact_note", ""),
            account_id=account["id"],
            days=left if left is not None else 0,
        )


class ContextMiddleware(BaseMiddleware):
    """Inject the database, configuration and per-account settings."""

    def __init__(self, db: Database, cfg: Settings) -> None:
        self.db = db
        self.cfg = cfg

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["db"] = self.db
        data["cfg"] = self.cfg
        try:
            data["app_settings"] = await get_app_settings(self.db)
        except Exception:
            logger.exception("failed to load app settings")
            data["app_settings"] = {}
        return await handler(event, data)


# Backwards-compatible alias (old name used by tests and docs).
OwnerOnlyMiddleware = AccessMiddleware
