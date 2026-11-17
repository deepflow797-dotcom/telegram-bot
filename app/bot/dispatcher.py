"""Dispatcher factory: routers, middlewares, FSM storage."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.bot.handlers import (
    admin,
    common,
    deletes,
    edits,
    errors,
    payments,
    properties,
    reports,
    search,
    settings,
    tenants,
)
from app.bot.middlewares import AccessMiddleware, ContextMiddleware
from app.config import Settings
from app.db.connection import Database
from app.db.fsm import PostgresStorage

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="القائمة الرئيسية"),
    BotCommand(command="menu", description="عرض الأقسام"),
    BotCommand(command="cancel", description="إلغاء العملية الحالية"),
    BotCommand(command="help", description="المساعدة"),
    BotCommand(command="id", description="عرض معرّف حسابي"),
]

HANDLER_MODULES = [
    admin,
    common,
    properties,
    edits,
    tenants,
    payments,
    reports,
    search,
    settings,
    deletes,
    errors,
]

# aiogram allows a router to be attached to a single parent only. Handler
# modules register on template routers at import time, so every dispatcher gets
# its own copy of those routers (handlers are stateless and safely shared).
_ROUTER_EVENTS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "inline_query",
    "chosen_inline_result",
    "callback_query",
    "shipping_query",
    "pre_checkout_query",
    "poll",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "message_reaction",
    "message_reaction_count",
)


def clone_router(source: Router) -> Router:
    """Copy handlers of a template router into a fresh, attachable router."""
    target = Router(name=source.name)
    for event_name in _ROUTER_EVENTS:
        src_observer = getattr(source, event_name, None)
        dst_observer = getattr(target, event_name, None)
        if src_observer is None or dst_observer is None:
            continue
        dst_observer.handlers = list(src_observer.handlers)
        dst_observer._handler.filters = list(src_observer._handler.filters or [])
    for sub_router in source.sub_routers:
        target.include_router(clone_router(sub_router))
    return target


def build_bot(cfg: Settings) -> Bot:
    return Bot(
        token=cfg.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(cfg: Settings, db: Database) -> Dispatcher:
    dp = Dispatcher(storage=PostgresStorage(db))

    access_middleware = AccessMiddleware(db, cfg)
    context_middleware = ContextMiddleware(db, cfg)

    # Authorization runs first (outer), context injection afterwards (inner).
    dp.message.outer_middleware(access_middleware)
    dp.callback_query.outer_middleware(access_middleware)
    dp.message.middleware(context_middleware)
    dp.callback_query.middleware(context_middleware)

    root = Router(name="root")
    for module in HANDLER_MODULES:
        root.include_router(clone_router(module.router))
    dp.include_router(root)

    errors.register_errors(dp)
    return dp


async def register_bot_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception:  # noqa: BLE001 - cosmetic only
        logger.warning("could not register bot commands")
