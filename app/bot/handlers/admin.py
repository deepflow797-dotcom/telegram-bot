"""Platform-owner tools: subscription requests, accounts and contact details.

Only the owner (account 1 / ``TELEGRAM_OWNER_ID``) sees these buttons; the
access middleware blocks everyone else before a handler runs.
"""

from __future__ import annotations

import datetime as dt
import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot import texts
from app.bot.states import ContactForm
from app.bot.utils import edit_or_send, today
from app.db import platform

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _denied() -> str:
    return texts.NOT_AUTHORIZED


def _account_line(account: dict, today_date: dt.date) -> str:
    status = platform.effective_status(account, today_date)
    label = platform.STATUS_LABELS.get(status, status)
    who = html.escape(account.get("display_name") or f"حساب {account['id']}")
    handle = f" @{account['username']}" if account.get("username") else ""
    paid = account.get("paid_until")
    left = platform.days_left(account, today_date)
    expiry = ""
    if paid:
        expiry = f" — ينتهي: {paid}" + (f" ({left} يوم)" if left is not None else "")
    return f"#{account['id']} {who}{handle} — {label}{expiry}"


async def _requests_payload(db, cfg) -> tuple[str, object]:
    rows = await platform.list_accounts(db, statuses=["pending"], limit=30)
    if not rows:
        return texts.REQUESTS_EMPTY, kb.requests_list([])
    today_date = today(cfg)
    lines = [texts.REQUESTS_TITLE, ""]
    for row in rows:
        lines.append(_account_line(row, today_date))
    lines.append("")
    lines.append("اضغط ✅ بجانب الاسم للتفعيل 30 يوماً، أو ❌ للرفض.")
    return "\n".join(lines), kb.requests_list(rows)


async def _accounts_payload(db, cfg) -> tuple[str, object]:
    rows = await platform.list_accounts(db, limit=40)
    if not rows:
        return texts.ACCOUNTS_EMPTY, kb.accounts_list([])
    today_date = today(cfg)
    lines = [texts.ACCOUNTS_TITLE, ""]
    for row in rows:
        lines.append(_account_line(row, today_date))
    return "\n".join(lines), kb.accounts_list(rows)


async def _contact_payload(db) -> str:
    settings = await platform.get_platform_settings(db)
    return (
        f"{texts.CONTACT_TITLE}\n\n"
        f"📱 رقم الجوال: <code>{html.escape(settings.get('owner_phone', ''))}</code>\n\n"
        f"📝 رسالة الطلب:\n{html.escape(settings.get('contact_note', ''))}"
    )


# ---------------------------------------------------------------------------
# entry points (reply keyboard + inline)
# ---------------------------------------------------------------------------
@router.message(F.text == kb.BTN_REQUESTS)
async def open_requests(message: Message, db, cfg, state: FSMContext, is_owner: bool = False) -> None:
    if not is_owner:
        await message.answer(_denied())
        return
    await state.clear()
    text, markup = await _requests_payload(db, cfg)
    await message.answer(text, reply_markup=markup)


@router.message(F.text == kb.BTN_CONTACT)
async def open_contact(message: Message, db, cfg, state: FSMContext, is_owner: bool = False) -> None:
    if not is_owner:
        await message.answer(_denied())
        return
    await state.clear()
    await message.answer(await _contact_payload(db), reply_markup=kb.contact_menu())


@router.callback_query(F.data == "m:requests")
async def cb_requests(callback: CallbackQuery, db, cfg, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    text, markup = await _requests_payload(db, cfg)
    await edit_or_send(callback, text, markup)


@router.callback_query(F.data == "m:accounts")
async def cb_accounts(callback: CallbackQuery, db, cfg, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    text, markup = await _accounts_payload(db, cfg)
    await edit_or_send(callback, text, markup)


@router.callback_query(F.data == "m:contact")
async def cb_contact(callback: CallbackQuery, db, cfg, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    await edit_or_send(callback, await _contact_payload(db), kb.contact_menu())


# ---------------------------------------------------------------------------
# approve / reject
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("adm:ok:"))
async def cb_approve(callback: CallbackQuery, db, cfg, bot, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    _, _, raw_id, raw_days = callback.data.split(":")
    account = await platform.approve_account(db, int(raw_id), days=int(raw_days))
    if account is None:
        await callback.answer("الحساب غير موجود", show_alert=True)
        return
    await _notify_user(bot, account, texts.ACCOUNT_APPROVED)
    await callback.answer("تم التفعيل ✅")
    text, markup = await _requests_payload(db, cfg)
    await edit_or_send(callback, text, markup)


@router.callback_query(F.data.startswith("adm:no:"))
async def cb_reject(callback: CallbackQuery, db, cfg, bot, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    account_id = int(callback.data.split(":")[2])
    account = await platform.set_account_status(db, account_id, platform.STATUS_REJECTED)
    if account is not None:
        await _notify_user(bot, account, texts.REQUEST_REJECTED)
    await callback.answer("تم الرفض ❌")
    text, markup = await _requests_payload(db, cfg)
    await edit_or_send(callback, text, markup)


async def _notify_user(bot, account: dict, text: str) -> None:
    telegram_id = account.get("telegram_id")
    if not telegram_id:
        return
    try:
        await bot.send_message(telegram_id, text)
    except Exception:  # noqa: BLE001 - user may have blocked the bot
        logger.warning("could not notify account %s", account.get("id"))


# ---------------------------------------------------------------------------
# single account: extend / freeze / resume
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("adm:acc:"))
async def cb_account(callback: CallbackQuery, db, cfg, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    account_id = int(callback.data.split(":")[2])
    account = await platform.get_account(db, account_id)
    if account is None:
        await callback.answer("الحساب غير موجود", show_alert=True)
        return
    today_date = today(cfg)
    text = (
        "👤 <b>بيانات الحساب</b>\n\n"
        f"{_account_line(account, today_date)}\n"
        f"معرّف تيليجرام: <code>{account.get('telegram_id')}</code>\n"
        f"تاريخ الطلب: {account.get('created_at')}"
    )
    await edit_or_send(callback, text, kb.account_actions(account))


@router.callback_query(F.data.startswith("adm:ext:"))
async def cb_extend(callback: CallbackQuery, db, cfg, bot, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    _, _, raw_id, raw_days = callback.data.split(":")
    account = await platform.approve_account(db, int(raw_id), days=int(raw_days))
    await callback.answer("تم التمديد ✅")
    if account is not None:
        await _notify_user(bot, account, texts.ACCOUNT_APPROVED)
        today_date = today(cfg)
        await edit_or_send(
            callback,
            "👤 <b>بيانات الحساب</b>\n\n" + _account_line(account, today_date),
            kb.account_actions(account),
        )


@router.callback_query(F.data.startswith("adm:freeze:"))
async def cb_freeze(callback: CallbackQuery, db, cfg, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    account_id = int(callback.data.split(":")[2])
    account = await platform.set_account_status(db, account_id, platform.STATUS_FROZEN)
    await callback.answer("تم الإيقاف ⏸")
    if account is not None:
        today_date = today(cfg)
        await edit_or_send(
            callback,
            "👤 <b>بيانات الحساب</b>\n\n" + _account_line(account, today_date),
            kb.account_actions(account),
        )


@router.callback_query(F.data.startswith("adm:on:"))
async def cb_resume(callback: CallbackQuery, db, cfg, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    account_id = int(callback.data.split(":")[2])
    account = await platform.approve_account(db, account_id, days=30)
    await callback.answer("تم التفعيل ✅")
    if account is not None:
        today_date = today(cfg)
        await edit_or_send(
            callback,
            "👤 <b>بيانات الحساب</b>\n\n" + _account_line(account, today_date),
            kb.account_actions(account),
        )


# ---------------------------------------------------------------------------
# editing the contact details
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "adm:set:phone")
async def ask_phone(callback: CallbackQuery, state: FSMContext, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    await state.set_state(ContactForm.phone)
    await callback.answer()
    await edit_or_send(callback, texts.ASK_OWNER_PHONE, kb.contact_menu())


@router.message(ContactForm.phone)
async def save_phone(message: Message, state: FSMContext, db, is_owner: bool = False) -> None:
    if not is_owner:
        await message.answer(_denied())
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer(texts.INVALID_INPUT)
        return
    await platform.set_platform_setting(db, "owner_phone", value)
    await state.clear()
    await message.answer(texts.CONTACT_SAVED, reply_markup=kb.contact_menu())


@router.callback_query(F.data == "adm:set:note")
async def ask_note(callback: CallbackQuery, state: FSMContext, is_owner: bool = False) -> None:
    if not is_owner:
        await callback.answer(_denied(), show_alert=True)
        return
    await state.set_state(ContactForm.note)
    await callback.answer()
    await edit_or_send(callback, texts.ASK_CONTACT_NOTE, kb.contact_menu())


@router.message(ContactForm.note)
async def save_note(message: Message, state: FSMContext, db, is_owner: bool = False) -> None:
    if not is_owner:
        await message.answer(_denied())
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer(texts.INVALID_INPUT)
        return
    await platform.set_platform_setting(db, "contact_note", value)
    await state.clear()
    await message.answer(texts.CONTACT_SAVED, reply_markup=kb.contact_menu())
