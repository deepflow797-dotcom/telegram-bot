"""Fallbacks and global error handling."""

from __future__ import annotations

import logging

from aiogram import Dispatcher, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.bot import keyboards as kb
from app.bot import texts
from app.bot.utils import answer_callback, edit_or_send

logger = logging.getLogger(__name__)
router = Router(name="errors")


@router.message(F.text)
async def unknown_message(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current:
        await message.answer(
            "⚠️ لم أفهم هذه الرسالة في الخطوة الحالية.\n"
            "أرسل القيمة المطلوبة أو اضغط إلغاء للخروج.",
            reply_markup=kb.cancel_keyboard("x:cancel"),
        )
        return
    await message.answer(texts.MAIN_MENU_HINT, reply_markup=kb.main_menu())
    await message.answer("استخدم الأزرار للتنقل:", reply_markup=kb.main_inline())


@router.message()
async def unknown_content(message: Message) -> None:
    await message.answer(
        "⚠️ هذا النوع من الرسائل غير مدعوم. استخدم الأزرار أو أرسل نصاً.",
        reply_markup=kb.main_menu(),
    )


@router.callback_query()
async def unknown_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await answer_callback(callback, texts.EXPIRED_ACTION, alert=False)
    await edit_or_send(callback, texts.EXPIRED_ACTION, kb.main_inline())


async def error_handler(event: ErrorEvent) -> bool:
    """Log the failure, keep the bot alive, tell the owner something went wrong."""
    logger.error(
        "update handling failed",
        exc_info=event.exception,
        extra={
            "extra_fields": {
                "update_type": type(event.update).__name__ if event.update else None,
            }
        },
    )
    update = event.update
    try:
        if update and update.callback_query and update.callback_query.message:
            await update.callback_query.message.answer(
                "⚠️ حدث خطأ غير متوقع. تم تسجيل المشكلة في السجلات.\n"
                "يمكنك إعادة المحاولة من القائمة.",
                reply_markup=kb.main_menu(),
            )
        elif update and update.message:
            await update.message.answer(
                "⚠️ حدث خطأ غير متوقع. تم تسجيل المشكلة في السجلات.",
                reply_markup=kb.main_menu(),
            )
    except Exception:  # noqa: BLE001 - never fail inside the error handler
        logger.exception("failed to notify owner about the error")
    return True


def register_errors(dp: Dispatcher) -> None:
    dp.errors.register(error_handler)
