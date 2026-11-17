"""Tenant management: list, create, profile, history, archive."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot import policy as bot_policy
from app.bot import texts
from app.bot.states import TenantForm
from app.bot.utils import (
    answer_callback,
    edit_or_send,
    is_cancel,
    parse_int,
    skipped,
    to_html,
)
from app.bot.utils import (
    today as today_of,
)
from app.bot.views import payment_line, tenant_text
from app.config import Settings
from app.core.dates import normalize_digits, parse_amount, parse_date
from app.core.formatting import format_date, format_money, status_label
from app.core.rent import TenantStatus
from app.db import payments as payments_repo
from app.db import repository as repo
from app.db import tenants as tenants_repo
from app.services.reports_data import state_from_row

logger = logging.getLogger(__name__)
router = Router(name="tenants")

PAGE_SIZE = kb.TENANTS_PAGE_SIZE


async def _render_tenant_card(callback: CallbackQuery, db, cfg: Settings, tenant_id: int) -> None:
    tenant = await tenants_repo.get_tenant(db, tenant_id)
    if tenant is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المستأجر.", kb.tenants_menu())
        return
    state = state_from_row(tenant, today=today_of(cfg))
    stats = await tenants_repo.payment_stats(db, tenant_id)
    await edit_or_send(callback, tenant_text(tenant, state, stats), kb.tenant_profile(tenant))


@router.callback_query(F.data.startswith("t:list:"))
async def list_tenants(callback: CallbackQuery, db, cfg: Settings) -> None:
    page = max(0, parse_int(callback.data.split(":")[2]))
    tenants = await tenants_repo.list_tenants(
        db, active_only=True, limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE
    )
    await answer_callback(callback)
    if not tenants and page == 0:
        await edit_or_send(callback, texts.NO_TENANTS, kb.tenants_menu())
        return
    has_next = len(tenants) > PAGE_SIZE
    tenants = tenants[:PAGE_SIZE]
    today = today_of(cfg)
    lines = [f"👥 *المستأجرون* — صفحة {page + 1}", ""]
    for tenant in tenants:
        state = state_from_row(tenant, today=today)
        lines.append(
            f"• {tenant['full_name']} — {tenant['building_name']} / {tenant['apartment_number']}\n"
            f"  {format_money(state.monthly_rent, state.currency)} | "
            f"{format_date(state.next_due)} | {status_label(state.status)}"
        )
    await edit_or_send(
        callback,
        "\n".join(lines),
        kb.tenant_list(tenants, page=page, has_next=has_next),
    )


@router.callback_query(F.data.startswith("t:view:"))
async def view_tenant(callback: CallbackQuery, db, cfg: Settings) -> None:
    tenant_id = parse_int(callback.data.split(":")[2])
    await answer_callback(callback)
    await _render_tenant_card(callback, db, cfg, tenant_id)


@router.callback_query(F.data == "t:overdue")
async def list_overdue(callback: CallbackQuery, db, cfg: Settings, app_settings: dict) -> None:
    from app.services.reports_data import tenant_states

    today = today_of(cfg)
    upcoming = int(app_settings.get("upcoming_days", "3") or 3)
    states = await tenant_states(db, today=today, upcoming_days=upcoming)
    overdue = [(row, st) for row, st in states if st.status is TenantStatus.OVERDUE]
    await answer_callback(callback)
    if not overdue:
        await edit_or_send(callback, "✅ لا توجد متأخرات حالياً.", kb.tenants_menu())
        return
    lines = [f"⚠️ *المتأخرون* ({len(overdue)})", ""]
    for row, st in overdue:
        lines.append(
            f"• {row['full_name']} — {row['building_name']} / {row['apartment_number']}\n"
            f"  المستحق: {format_money(st.outstanding, st.currency)} | "
            f"الاستحقاق: {format_date(st.next_due)}"
        )
    await edit_or_send(
        callback,
        "\n".join(lines),
        kb.tenant_list([row for row, _ in overdue[:PAGE_SIZE]], page=0, has_next=False, back="m:tenants"),
    )


@router.callback_query(F.data == "t:upcoming")
async def list_upcoming(callback: CallbackQuery, db, cfg: Settings, app_settings: dict) -> None:
    from app.services.reports_data import tenant_states

    today = today_of(cfg)
    upcoming = int(app_settings.get("upcoming_days", "3") or 3)
    states = await tenant_states(db, today=today, upcoming_days=upcoming)
    entries = [
        (row, st)
        for row, st in states
        if st.status in (TenantStatus.UPCOMING, TenantStatus.DUE_TODAY)
    ]
    await answer_callback(callback)
    if not entries:
        await edit_or_send(callback, "✅ لا توجد مدفوعات مستحقة قريباً.", kb.tenants_menu())
        return
    lines = [f"⏳ *مستحق خلال {upcoming} أيام* ({len(entries)})", ""]
    for row, st in entries:
        lines.append(
            f"• {row['full_name']} — {row['building_name']} / {row['apartment_number']}\n"
            f"  المستحق: {format_money(st.remaining_for_period, st.currency)} | "
            f"الاستحقاق: {format_date(st.next_due)} | {status_label(st.status)}"
        )
    await edit_or_send(
        callback,
        "\n".join(lines),
        kb.tenant_list([row for row, _ in entries[:PAGE_SIZE]], page=0, has_next=False),
    )


@router.callback_query(F.data.startswith("t:hist:"))
async def tenant_history(callback: CallbackQuery, db) -> None:
    tenant_id = parse_int(callback.data.split(":")[2])
    tenant = await tenants_repo.get_tenant(db, tenant_id)
    payments = await payments_repo.list_payments(db, tenant_id=tenant_id, limit=15)
    await answer_callback(callback)
    if tenant is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المستأجر.", kb.tenants_menu())
        return
    if not payments:
        body = "ℹ️ لا توجد مدفوعات مسجلة لهذا المستأجر."
    else:
        body = "\n".join(payment_line(payment) for payment in payments)
    text = f"📄 *سجل مدفوعات {tenant['full_name']}*\n\n{body}"
    await edit_or_send(callback, text, kb.back_only(f"t:view:{tenant_id}"))


@router.callback_query(F.data.startswith("t:arch:"))
async def archive_confirm(callback: CallbackQuery, db) -> None:
    if not bot_policy.allow_archive():
        await answer_callback(callback, "الأرشفة متوقفة من الإعدادات", alert=True)
        await edit_or_send(
            callback,
            "⛔️ الأرشفة متوقفة في حسابك.\n\n"
            "لتشغيلها: ⚙️ الإعدادات ← 🗑 الحذف والأرشفة ← 🗄 الأرشفة.",
            kb.settings_hint("s:delpol"),
        )
        return
    tenant_id = parse_int(callback.data.split(":")[2])
    tenant = await tenants_repo.get_tenant(db, tenant_id)
    await answer_callback(callback)
    if tenant is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المستأجر.", kb.tenants_menu())
        return
    await edit_or_send(
        callback,
        f"🗄 هل تريد أرشفة المستأجر *{tenant['full_name']}*؟\n\n"
        "سيتم تحرير الشقة، ويبقى سجل المدفوعات محفوظاً بالكامل.",
        kb.confirm_archive("t", tenant_id),
    )


@router.callback_query(F.data.startswith("t:archok:"))
async def archive_tenant(callback: CallbackQuery, db, cfg: Settings) -> None:
    if not bot_policy.allow_archive():
        await answer_callback(callback, "الأرشفة متوقفة من الإعدادات", alert=True)
        return
    tenant_id = parse_int(callback.data.split(":")[2])
    tenant = await tenants_repo.archive_tenant(db, tenant_id)
    await answer_callback(callback, "تم الأرشفة")
    if tenant is None:
        await edit_or_send(callback, "⚠️ المستأجر غير موجود أو مؤرشف مسبقاً.", kb.tenants_menu())
        return
    await repo.log_audit(
        db,
        actor_id=cfg.owner_id,
        action="tenant_archived",
        entity_type="tenant",
        entity_id=tenant_id,
        details={"name": tenant["full_name"]},
    )
    await edit_or_send(
        callback,
        f"✅ تم أرشفة المستأجر *{tenant['full_name']}* وتحرير الشقة.\n"
        "سجل المدفوعات محفوظ.",
        kb.tenants_menu(),
    )


# ---------------------------------------------------------------------------
# add tenant flow
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "t:add")
async def add_tenant_start(callback: CallbackQuery, state: FSMContext, db) -> None:
    await answer_callback(callback)
    await _start_tenant_form(callback, state, db, apartment_id=None)


@router.callback_query(F.data.startswith("t:add:"))
async def add_tenant_for_apartment(callback: CallbackQuery, state: FSMContext, db) -> None:
    apartment_id = parse_int(callback.data.split(":")[2])
    await answer_callback(callback)
    await _start_tenant_form(callback, state, db, apartment_id=apartment_id)


async def _start_tenant_form(
    callback: CallbackQuery, state: FSMContext, db, apartment_id: int | None
) -> None:
    await state.clear()
    if apartment_id is not None:
        apartment = await repo.get_apartment(db, apartment_id)
        if apartment is None or not apartment["is_active"]:
            await answer_callback(callback, "⚠️ الشقة غير متاحة.", alert=True)
            return
        if apartment["tenant_id"]:
            await answer_callback(callback, "⚠️ الشقة مشغولة بمستأجر.", alert=True)
            return
        await state.update_data(apartment_id=apartment_id, building_id=apartment["building_id"])
        await state.set_state(TenantForm.name)
        await edit_or_send(
            callback,
            f"👤 *مستأجر جديد* — {apartment['building_name']} / شقة {apartment['apartment_number']}\n\n"
            f"{texts.ASK_TENANT_NAME}",
            kb.cancel_keyboard("m:tenants"),
        )
        return

    vacant = await repo.list_apartments(db, active_only=True, only_vacant=True, limit=200)
    if not vacant:
        await edit_or_send(callback, texts.NO_VACANT_APARTMENTS, kb.properties_menu())
        return
    buildings = await repo.list_buildings(db, active_only=True)
    await state.set_state(TenantForm.building)
    await edit_or_send(
        callback,
        "🏢 اختر المبنى الذي تريد إضافة مستأجر فيه:",
        kb.buildings_picker(buildings, "t:pickb", "m:tenants"),
    )


@router.callback_query(F.data.startswith("t:pickb:"))
async def choose_building_for_tenant(callback: CallbackQuery, state: FSMContext, db) -> None:
    building_id = parse_int(callback.data.split(":")[2])
    apartments = await repo.list_apartments(
        db, building_id=building_id, active_only=True, only_vacant=True, limit=100
    )
    await answer_callback(callback)
    if not apartments:
        await edit_or_send(
            callback,
            "ℹ️ لا توجد شقق فارغة في هذا المبنى.\nأضف شقة جديدة أولاً.",
            kb.generic_rows(
                [
                    [("➕ إضافة شقة", f"b:addapt:{building_id}")],
                    [("🔙 رجوع", "t:add")],
                ]
            ),
        )
        return
    await state.set_state(TenantForm.apartment)
    await state.update_data(building_id=building_id)
    await edit_or_send(
        callback, "🚪 اختر الشقة:", kb.vacant_apartments_picker(apartments, back="t:add")
    )


@router.callback_query(F.data.startswith("t:apt:"))
async def choose_apartment_for_tenant(callback: CallbackQuery, state: FSMContext, db) -> None:
    apartment_id = parse_int(callback.data.split(":")[2])
    apartment = await repo.get_apartment(db, apartment_id)
    await answer_callback(callback)
    if apartment is None or apartment["tenant_id"]:
        await answer_callback(callback, "⚠️ الشقة غير متاحة.", alert=True)
        return
    await state.update_data(apartment_id=apartment_id, building_id=apartment["building_id"])
    await state.set_state(TenantForm.name)
    await edit_or_send(
        callback,
        f"🚪 {apartment['building_name']} / شقة {apartment['apartment_number']}\n\n"
        f"{texts.ASK_TENANT_NAME}",
        kb.cancel_keyboard("m:tenants"),
    )


@router.message(TenantForm.name)
async def tenant_name(message: Message, state: FSMContext) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("⚠️ الاسم قصير جداً. أرسل الاسم الكامل.")
        return
    await state.update_data(full_name=name)
    await state.set_state(TenantForm.phone)
    await message.answer(texts.ASK_TENANT_PHONE, reply_markup=kb.cancel_keyboard("m:tenants"))


@router.message(TenantForm.phone)
async def tenant_phone(message: Message, state: FSMContext) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    await state.update_data(phone=skipped(message.text))
    await state.set_state(TenantForm.rent)
    await message.answer(texts.ASK_TENANT_RENT, reply_markup=kb.cancel_keyboard("m:tenants"))


@router.message(TenantForm.rent)
async def tenant_rent(message: Message, state: FSMContext) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("⚠️ أرسل مبلغاً صحيحاً أكبر من صفر (أرقام فقط).")
        return
    await state.update_data(monthly_rent=str(amount))
    await state.set_state(TenantForm.start_date)
    await message.answer(texts.ASK_TENANT_START, reply_markup=kb.cancel_keyboard("m:tenants"))


@router.message(TenantForm.start_date)
async def tenant_start_date(message: Message, state: FSMContext, cfg: Settings) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    raw = normalize_digits(message.text or "").strip()
    if raw in {"اليوم", "today"}:
        start = today_of(cfg)
    else:
        start = parse_date(raw)
    if start is None:
        await message.answer("⚠️ صيغة التاريخ غير صحيحة. مثال: 2026-01-15 أو *اليوم*.")
        return
    await state.update_data(rental_start_date=start.isoformat())
    await state.set_state(TenantForm.notes)
    await message.answer(texts.ASK_TENANT_NOTES, reply_markup=kb.cancel_keyboard("m:tenants"))


@router.message(TenantForm.notes)
async def tenant_notes(
    message: Message, state: FSMContext, db, cfg: Settings, app_settings: dict
) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    currency = (app_settings.get("currency") or "MRU").upper()
    await state.update_data(notes=skipped(message.text), currency=currency)
    data = await state.get_data()
    apartment = await repo.get_apartment(db, int(data["apartment_id"]))
    await state.set_state(TenantForm.confirm)
    currency = data.get("currency") or "MRU"
    summary = (
        "🧾 *مراجعة بيانات المستأجر*\n\n"
        f"👤 الاسم: {data.get('full_name')}\n"
        f"📱 الهاتف: {data.get('phone') or '—'}\n"
        f"🏢 المبنى: {apartment['building_name'] if apartment else '—'}\n"
        f"🚪 الشقة: {apartment['apartment_number'] if apartment else '—'}\n"
        f"💰 الإيجار الشهري: {format_money(data['monthly_rent'], currency)}\n"
        f"📅 تاريخ البداية: {format_date(parse_date(data.get('rental_start_date', '')) or today_of(cfg))}\n"
        f"📝 ملاحظات: {data.get('notes') or '—'}\n\n"
        "هل تريد حفظ المستأجر؟"
    )
    await message.answer(
        to_html(summary),
        reply_markup=kb.generic_rows(
            [
                [("✅ حفظ المستأجر", "t:save")],
                [("✖️ إلغاء", "x:cancel")],
            ]
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "t:save")
async def save_tenant(callback: CallbackQuery, state: FSMContext, db, cfg: Settings) -> None:
    data = await state.get_data()
    if await state.get_state() != TenantForm.confirm.state or not data.get("apartment_id"):
        await answer_callback(callback, texts.EXPIRED_ACTION, alert=True)
        return
    apartment = await repo.get_apartment(db, int(data["apartment_id"]))
    if apartment is None or apartment["tenant_id"]:
        await state.clear()
        await answer_callback(callback, "⚠️ الشقة لم تعد متاحة.", alert=True)
        return
    rent = parse_amount(data.get("monthly_rent"))
    start = parse_date(data.get("rental_start_date"))
    if rent is None or start is None:
        await state.clear()
        await answer_callback(callback, "⚠️ بيانات غير مكتملة. أعد المحاولة.", alert=True)
        return
    currency = data.get("currency") or "MRU"
    try:
        tenant = await tenants_repo.create_tenant(
            db,
            full_name=data.get("full_name", ""),
            phone=data.get("phone"),
            apartment_id=int(data["apartment_id"]),
            monthly_rent=rent,
            currency=currency,
            rental_start_date=start,
            notes=data.get("notes"),
        )
    except Exception:
        logger.exception("failed to create tenant")
        await answer_callback(callback, "⚠️ تعذر حفظ المستأجر. قد تكون الشقة مشغولة.", alert=True)
        return
    await state.clear()
    await repo.log_audit(
        db,
        actor_id=cfg.owner_id,
        action="tenant_created",
        entity_type="tenant",
        entity_id=tenant["id"],
        details={"name": tenant["full_name"], "rent": str(rent), "currency": currency},
    )
    await answer_callback(callback, "تم الحفظ")
    await edit_or_send(callback, "✅ تم إضافة المستأجر بنجاح.", kb.tenants_menu())
    tenant = await tenants_repo.get_tenant(db, tenant["id"])
    st = state_from_row(tenant, today=today_of(cfg))
    stats = await tenants_repo.payment_stats(db, tenant["id"])
    message = callback.message
    if isinstance(message, Message):
        await message.answer(
            to_html(tenant_text(tenant, st, stats)),
            reply_markup=kb.tenant_profile(tenant),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "t:abort")
async def abort_tenant(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await answer_callback(callback, "تم الإلغاء")
    await edit_or_send(callback, texts.CANCELLED, kb.tenants_menu())
