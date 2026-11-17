"""Tenant search by name, phone, building or apartment number."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot import texts
from app.bot.states import SearchForm
from app.bot.utils import answer_callback, edit_or_send, send_long
from app.bot.utils import today as today_of
from app.config import Settings
from app.core.formatting import format_date, format_money, status_label
from app.db import tenants as tenants_repo
from app.services.reports_data import state_from_row

logger = logging.getLogger(__name__)
router = Router(name="search")


async def start_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SearchForm.query)
    await send_long(message, texts.ASK_SEARCH, kb.cancel_keyboard("m:main"))


@router.callback_query(F.data == "m:search")
async def search_from_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await answer_callback(callback)
    await state.clear()
    await state.set_state(SearchForm.query)
    await edit_or_send(callback, texts.ASK_SEARCH, kb.cancel_keyboard("m:main"))


@router.message(SearchForm.query)
async def run_search(message: Message, state: FSMContext, db, cfg: Settings) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("⚠️ أرسل حرفين على الأقل للبحث.")
        return
    tenants = await tenants_repo.search_tenants(db, query, active_only=False, limit=12)
    await state.clear()
    if not tenants:
        await send_long(
            message,
            f"🔎 لا توجد نتائج لـ *{query}*.",
            kb.tenants_menu(),
        )
        return
    today = today_of(cfg)
    lines = [f"🔎 *نتائج البحث عن {query}* ({len(tenants)})", ""]
    for tenant in tenants:
        state_now = state_from_row(tenant, today=today)
        archived = "" if tenant["is_active"] else " (مؤرشف)"
        lines.append(
            f"• {tenant['full_name']}{archived} — {tenant['building_name']} / "
            f"شقة {tenant['apartment_number']}\n"
            f"  {format_money(state_now.monthly_rent, state_now.currency)} | "
            f"{format_date(state_now.next_due)} | {status_label(state_now.status)}"
        )
    await send_long(
        message,
        "\n".join(lines),
        kb.tenant_list(tenants, page=0, has_next=False, back="m:tenants"),
    )
