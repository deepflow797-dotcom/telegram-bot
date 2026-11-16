"""Owner-only access control plus dependency injection."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.bot import texts
from app.config import Settings
from app.db.connection import Database
from app.db.repository import get_app_settings

logger = logging.getLogger(__name__)


class OwnerOnlyMiddleware(BaseMiddleware):
    """Reject every update that does not come from the configured owner.

    The comparison happens before any handler runs and before any database
    query, so unknown users cannot read or write anything.
    """

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id != self.owner_id:
            logger.warning(
                "unauthorized access attempt",
                extra={
                    "extra_fields": {
                        "user_id": getattr(user, "id", None),
                        "username": getattr(user, "username", None),
                        "event": type(event).__name__,
                    }
                },
            )
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(texts.NOT_AUTHORIZED, show_alert=True)
                except Exception:  # noqa: BLE001
                    pass
            elif isinstance(event, Message):
                try:
                    await event.answer(texts.NOT_AUTHORIZED)
                except Exception:  # noqa: BLE001
                    pass
            return None
        return await handler(event, data)


class ContextMiddleware(BaseMiddleware):
    """Inject the database, configuration and app settings into handlers."""

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
        except Exception:  # noqa: BLE001 - never block the UI on settings
            logger.exception("failed to load app settings")
            data["app_settings"] = {}
        return await handler(event, data)
