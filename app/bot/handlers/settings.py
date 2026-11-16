"""Settings: default currency, daily report time/state, bot info."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot import texts
from app.bot.states import SettingsForm
from app.bot.utils import answer_callback, edit_or_send, is_cancel
from app.config import Settings
from app.core.dates import normalize_digits
from app.db import repository as repo
from app.db.reports import recent_reports

logger = logging.getLogger(__name__)
router = Router(name="settings")

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


async def _show_menu(callback: CallbackQuery, db, app_settings: dict) -> None:
    enabled = app_settings.get("report_enabled", "true") == "true"
    await edit_or_send(
        callback,
        texts.SETTINGS_MENU,
        kb.settings_menu(
            app_settings.get("currency", "MRU"),
            app_settings.get("report_time", "08:00"),
            enabled,
        ),
    )


@router.callback_query(F.data == "s:currency")
async def choose_currency(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await answer_callback(callback)
    await edit_or_send(callback, "💱 اختر العملة الافتراضية:", kb.currency_choice())


@router.callback_query(F.data.startswith("s:setcur:"))
async def set_currency(callback: CallbackQuery, db, cfg: Settings) -> None:
    code = callback.data.split(":")[2].upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        await answer_callback(callback, "⚠️ رمز غير صحيح.", alert=True)
        return
    await repo.set_app_setting(db, "currency", code)
    await repo.log_audit(
        db, actor_id=cfg.owner_id, action="currency_updated", details={"currency": code}
    )
    await answer_callback(callback, f"تم تعيين العملة: {code}")
    await _show_menu(callback, db, await repo.get_app_settings(db))


@router.callback_query(F.data == "s:customcur")
async def custom_currency_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await answer_callback(callback)
    await state.set_state(SettingsForm.currency)
    await edit_or_send(callback, texts.ASK_CURRENCY, kb.cancel_keyboard("m:settings"))


@router.message(SettingsForm.currency)
async def custom_currency(message: Message, state: FSMContext, db, cfg: Settings) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    code = normalize_digits(message.text or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        await message.answer("⚠️ أرسل رمز عملة من 3 أحرف مثل MRU أو USD.")
        return
    await repo.set_app_setting(db, "currency", code)
    await repo.log_audit(
        db, actor_id=cfg.owner_id, action="currency_updated", details={"currency": code}
    )
    await state.clear()
    settings = await repo.get_app_settings(db)
    enabled = settings.get("report_enabled", "true") == "true"
    await message.answer(f"✅ تم تعيين العملة الافتراضية: {code}")
    await message.answer(
        texts.SETTINGS_MENU,
        reply_markup=kb.settings_menu(code, settings.get("report_time", "08:00"), enabled),
    )


@router.callback_query(F.data == "s:time")
async def ask_report_time(callback: CallbackQuery, state: FSMContext, app_settings: dict) -> None:
    await answer_callback(callback)
    await state.set_state(SettingsForm.report_time)
    await edit_or_send(
        callback,
        f"{texts.ASK_REPORT_TIME}\n\nالوقت الحالي: {app_settings.get('report_time', '08:00')}",
        kb.cancel_keyboard("m:settings"),
    )


@router.message(SettingsForm.report_time)
async def set_report_time(message: Message, state: FSMContext, db, cfg: Settings) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    raw = normalize_digits(message.text or "").strip()
    match = _TIME_RE.match(raw)
    if not match:
        await message.answer("⚠️ صيغة غير صحيحة. مثال: 08:00")
        return
    value = f"{int(match.group(1)):02d}:{match.group(2)}"
    await repo.set_app_setting(db, "report_time", value)
    await repo.log_audit(
        db, actor_id=cfg.owner_id, action="report_time_updated", details={"time": value}
    )
    await state.clear()
    settings = await repo.get_app_settings(db)
    enabled = settings.get("report_enabled", "true") == "true"
    await message.answer(f"✅ تم تعيين وقت التقرير اليومي: {value} ({cfg.app_timezone})")
    await message.answer(
        texts.SETTINGS_MENU,
        reply_markup=kb.settings_menu(
            settings.get("currency", "MRU"), settings.get("report_time", "08:00"), enabled
        ),
    )


@router.callback_query(F.data == "s:report")
async def toggle_report(callback: CallbackQuery, db, cfg: Settings) -> None:
    settings = await repo.get_app_settings(db)
    enabled = settings.get("report_enabled", "true") == "true"
    new_value = "false" if enabled else "true"
    await repo.set_app_setting(db, "report_enabled", new_value)
    await repo.log_audit(
        db, actor_id=cfg.owner_id, action="report_enabled_updated", details={"value": new_value}
    )
    await answer_callback(callback, "✅ تم تحديث إعداد التقرير")
    await _show_menu(callback, db, await repo.get_app_settings(db))


@router.callback_query(F.data == "s:info")
async def bot_info(callback: CallbackQuery, db, cfg: Settings, app_settings: dict) -> None:
    await answer_callback(callback)
    reports = await recent_reports(db, limit=5)
    report_lines = (
        "\n".join(
            f"• {row['report_date'].strftime('%d/%m/%Y')}: {row['status']}"
            for row in reports
        )
        or "لا يوجد سجل بعد"
    )
    text = (
        "ℹ️ *معلومات البوت*\n\n"
        f"• المنطقة الزمنية: {cfg.app_timezone}\n"
        f"• العملة الافتراضية: {app_settings.get('currency', 'MRU')}\n"
        f"• وقت التقرير اليومي: {app_settings.get('report_time', '08:00')}\n"
        f"• التقرير اليومي: {'مفعّل ✅' if app_settings.get('report_enabled', 'true') == 'true' else 'متوقف ⛔️'}\n"
        f"• المالك: `{cfg.owner_id}`\n\n"
        f"📤 *آخر التقارير اليومية*\n{report_lines}"
    )
    await edit_or_send(callback, text, kb.back_only("m:settings"))
