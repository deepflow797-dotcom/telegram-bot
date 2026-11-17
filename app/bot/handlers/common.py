"""Start, main menu and navigation handlers."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot import texts
from app.bot.utils import answer_callback, edit_or_send, send_long
from app.config import Settings

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(
    message: Message, state: FSMContext, db, cfg: Settings, is_owner: bool = False
) -> None:
    await state.clear()
    greeting = texts.WELCOME if is_owner else texts.WELCOME_ACCOUNT
    await message.answer(greeting, reply_markup=kb.main_menu(), parse_mode="HTML")
    await message.answer(texts.MAIN_MENU_HINT, reply_markup=kb.main_inline())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.MAIN_MENU_HINT, reply_markup=kb.main_menu())
    await message.answer("الأقسام:", reply_markup=kb.main_inline())


@router.message(Command("help"))
async def cmd_help(message: Message, cfg: Settings) -> None:
    await send_long(
        message,
        "ℹ️ *المساعدة*\n\n"
        "استخدم الأزرار للتنقل بين الأقسام:\n"
        "• 🏠 إدارة العقارات: إضافة وتعديل المباني والشقق.\n"
        "• 👥 المستأجرون: إضافة المستأجرين وملفاتهم وأرشفتهم.\n"
        "• 💰 تسجيل الدفع: تسجيل دفعة كاملة أو جزئية.\n"
        "• 📊 التقارير: تقرير اليوم والشهر والمتأخرات والإيرادات.\n"
        "• 🔎 البحث: البحث بالاسم أو الهاتف أو المبنى أو الشقة.\n"
        "• ⚙️ الإعدادات: العملة ووقت التقرير.\n\n"
        "الأوامر المتاحة: /start /menu /cancel /id",
        kb.main_inline(),
    )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    user = message.from_user
    await message.answer(
        f"🆔 معرّف حسابك في تيليجرام: `{user.id}`" if user else "🆔 غير متوفر",
        parse_mode="Markdown",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
    await message.answer(texts.MAIN_MENU_HINT, reply_markup=kb.main_inline())


# -- reply keyboard section buttons ----------------------------------------
@router.message(F.text == kb.BTN_PROPERTIES)
async def open_properties(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.PROPERTIES_MENU, reply_markup=kb.properties_menu(), parse_mode="Markdown")


@router.message(F.text == kb.BTN_TENANTS)
async def open_tenants(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.TENANTS_MENU, reply_markup=kb.tenants_menu(), parse_mode="Markdown")


@router.message(F.text == kb.BTN_PAY)
async def open_payments(message: Message, state: FSMContext, db, cfg: Settings, app_settings: dict) -> None:
    from app.bot.handlers.payments import show_payment_tenant_picker

    await show_payment_tenant_picker(message, state=state, db=db, cfg=cfg, page=0)


@router.message(F.text == kb.BTN_REPORTS)
async def open_reports(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.REPORTS_MENU, reply_markup=kb.reports_menu(), parse_mode="Markdown")


@router.message(F.text == kb.BTN_SEARCH)
async def open_search(message: Message, state: FSMContext) -> None:
    from app.bot.handlers.search import start_search

    await start_search(message, state=state)


@router.message(F.text == kb.BTN_SETTINGS)
async def open_settings(message: Message, state: FSMContext, app_settings: dict) -> None:
    await state.clear()
    report_time = app_settings.get("report_time", "08:00")
    enabled = app_settings.get("report_enabled", "true") == "true"
    await message.answer(
        texts.SETTINGS_MENU,
        reply_markup=kb.settings_menu(app_settings.get("currency", "MRU"), report_time, enabled),
        parse_mode="Markdown",
    )


# -- inline navigation ------------------------------------------------------
@router.callback_query(F.data == "m:main")
async def back_to_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await answer_callback(callback)
    await edit_or_send(callback, texts.MAIN_MENU_HINT, kb.main_inline())


@router.callback_query(F.data == "m:props")
async def nav_properties(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await answer_callback(callback)
    await edit_or_send(callback, texts.PROPERTIES_MENU, kb.properties_menu())


@router.callback_query(F.data == "m:tenants")
async def nav_tenants(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await answer_callback(callback)
    await edit_or_send(callback, texts.TENANTS_MENU, kb.tenants_menu())


@router.callback_query(F.data == "m:reports")
async def nav_reports(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await answer_callback(callback)
    await edit_or_send(callback, texts.REPORTS_MENU, kb.reports_menu())


@router.callback_query(F.data == "m:settings")
async def nav_settings(callback: CallbackQuery, state: FSMContext, app_settings: dict) -> None:
    await state.clear()
    await answer_callback(callback)
    enabled = app_settings.get("report_enabled", "true") == "true"
    await edit_or_send(
        callback,
        texts.SETTINGS_MENU,
        kb.settings_menu(app_settings.get("currency", "MRU"), app_settings.get("report_time", "08:00"), enabled),
    )


@router.callback_query(F.data == "x:cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await answer_callback(callback, "تم الإلغاء")
    await edit_or_send(callback, texts.CANCELLED, kb.main_inline())


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await answer_callback(callback)
