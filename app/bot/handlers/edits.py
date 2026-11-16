"""Generic single-field edit flow for buildings, apartments and tenants.

Editing never touches payment history and never resets a rental cycle.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from psycopg.errors import UniqueViolation

from app.bot import keyboards as kb
from app.bot import texts
from app.bot.states import EditForm
from app.bot.utils import answer_callback, edit_or_send, is_cancel, parse_int, skipped, to_html
from app.bot.views import apartment_text, building_text, tenant_text
from app.config import Settings
from app.core.dates import normalize_digits, parse_amount, parse_date
from app.core.formatting import format_money
from app.db import repository as repo
from app.db import tenants as tenants_repo
from app.services.reports_data import state_from_row

logger = logging.getLogger(__name__)
router = Router(name="edits")

FIELD_LABELS = {
    "full_name": "الاسم الكامل",
    "phone": "رقم الهاتف",
    "monthly_rent": "الإيجار الشهري",
    "currency": "العملة (3 أحرف)",
    "rental_start_date": "تاريخ بداية الإيجار (YYYY-MM-DD)",
    "notes": "الملاحظات",
    "apartment_id": "رقم الشقة",
    "name": "الاسم",
    "address": "العنوان",
    "number": "رقم الشقة",
    "floor": "الطابق",
}


@router.callback_query(F.data.startswith("t:field:"))
async def choose_tenant_field(callback: CallbackQuery, state: FSMContext, db) -> None:
    parts = callback.data.split(":")  # t:field:<field>:<id>
    field = parts[2]
    tenant_id = parse_int(parts[3])
    tenant = await tenants_repo.get_tenant(db, tenant_id)
    await answer_callback(callback)
    if tenant is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المستأجر.", kb.main_inline())
        return
    await state.clear()
    await state.set_state(EditForm.value)
    await state.update_data(entity="tenant", entity_id=tenant_id, field=field)
    hint = ""
    if field == "monthly_rent":
        hint = f"\n\nالقيمة الحالية: {format_money(tenant['monthly_rent'], tenant['currency'])}"
    elif field == "currency":
        hint = f"\n\nالقيمة الحالية: {tenant['currency']}"
    elif field == "apartment_id":
        hint = (
            f"\n\nالشقة الحالية: {tenant['building_name']} / {tenant['apartment_number']}\n"
            "أرسل رقم الشقة الجديدة."
        )
    await edit_or_send(
        callback,
        f"✏️ أرسل {FIELD_LABELS.get(field, 'القيمة الجديدة')}:{hint}",
        kb.cancel_keyboard(f"t:view:{tenant_id}"),
    )


@router.callback_query(F.data.startswith("t:edit:"))
async def tenant_edit_menu(callback: CallbackQuery, state: FSMContext) -> None:
    tenant_id = parse_int(callback.data.split(":")[2])
    await state.clear()
    await answer_callback(callback)
    await edit_or_send(callback, "✏️ *تعديل البيانات*\nاختر الحقل:", kb.tenant_edit_menu(tenant_id))


@router.message(EditForm.value)
async def apply_edit(message: Message, state: FSMContext, db, cfg: Settings) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return

    data = await state.get_data()
    entity = data.get("entity")
    entity_id = int(data.get("entity_id", 0))
    field = data.get("field", "")
    raw = (message.text or "").strip()

    try:
        if entity == "building":
            await _edit_building(message, state, db, cfg, entity_id, field, raw)
        elif entity == "apartment":
            await _edit_apartment(message, state, db, cfg, entity_id, field, raw)
        elif entity == "tenant":
            await _edit_tenant(message, state, db, cfg, entity_id, field, raw)
        else:
            await state.clear()
            await message.answer("⚠️ عملية غير معروفة.", reply_markup=kb.main_menu())
    except UniqueViolation:
        await message.answer("⚠️ هذه القيمة مستخدمة مسبقاً. أرسل قيمة أخرى.")
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")


async def _edit_building(message, state, db, cfg, building_id, field, raw) -> None:
    if field not in {"name", "address", "description"}:
        await state.clear()
        await message.answer("⚠️ حقل غير مدعوم.", reply_markup=kb.main_menu())
        return
    if field == "name" and len(raw) < 2:
        await message.answer(texts.INVALID_INPUT)
        return
    building = await repo.update_building(db, building_id, **{field: raw})
    await state.clear()
    if building is None:
        await message.answer("⚠️ لم يتم العثور على المبنى.", reply_markup=kb.main_menu())
        return
    await repo.log_audit(
        db, actor_id=cfg.owner_id, action=f"building_{field}_updated",
        entity_type="building", entity_id=building_id, details={"value": raw[:200]},
    )
    building = await repo.get_building(db, building_id)
    await message.answer("✅ تم التحديث.")
    await message.answer(
        to_html(building_text(building)), reply_markup=kb.building_card(building_id), parse_mode="HTML"
    )


async def _edit_apartment(message, state, db, cfg, apartment_id, field, raw) -> None:
    if field not in {"number", "floor", "description"}:
        await state.clear()
        await message.answer("⚠️ حقل غير مدعوم.", reply_markup=kb.main_menu())
        return
    if field == "number":
        kwargs = {"apartment_number": raw}
    elif field == "floor":
        kwargs = {"floor": raw}
    else:
        kwargs = {"description": raw}
    apartment = await repo.update_apartment(db, apartment_id, **kwargs)
    await state.clear()
    if apartment is None:
        await message.answer("⚠️ لم يتم العثور على الشقة.", reply_markup=kb.main_menu())
        return
    await repo.log_audit(
        db, actor_id=cfg.owner_id, action=f"apartment_{field}_updated",
        entity_type="apartment", entity_id=apartment_id, details={"value": raw[:200]},
    )
    await message.answer("✅ تم التحديث.")
    await message.answer(
        to_html(apartment_text(apartment)), reply_markup=kb.apartment_card(apartment), parse_mode="HTML"
    )


async def _edit_tenant(message, state, db, cfg, tenant_id, field, raw) -> None:
    tenant = await tenants_repo.get_tenant(db, tenant_id)
    if tenant is None:
        await state.clear()
        await message.answer("⚠️ لم يتم العثور على المستأجر.", reply_markup=kb.main_menu())
        return

    updates: dict = {}
    if field == "full_name":
        if len(raw) < 3:
            raise ValueError("الاسم قصير جداً.")
        updates["full_name"] = raw
    elif field == "phone":
        updates["phone"] = skipped(raw)
    elif field == "monthly_rent":
        amount = parse_amount(raw)
        if amount is None:
            raise ValueError("أرسل مبلغاً صحيحاً أكبر من صفر.")
        updates["monthly_rent"] = amount
    elif field == "currency":
        code = normalize_digits(raw).upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("أرسل رمز عملة من 3 أحرف مثل MRU.")
        updates["currency"] = code
    elif field == "rental_start_date":
        when = parse_date(raw)
        if when is None:
            raise ValueError("صيغة التاريخ غير صحيحة. مثال: 2026-01-15")
        updates["rental_start_date"] = when
    elif field == "notes":
        updates["notes"] = skipped(raw)
    elif field == "apartment_id":
        apartment_id = parse_int(normalize_digits(raw))
        if apartment_id <= 0:
            raise ValueError("أرسل رقم الشقة الصحيح.")
        try:
            updated = await tenants_repo.change_tenant_apartment(db, tenant_id, apartment_id)
        except ValueError as exc:
            if str(exc) == "apartment_occupied":
                raise ValueError("الشقة مشغولة بمستأجر نشط آخر.") from exc
            raise ValueError("الشقة غير موجودة أو مؤرشفة.") from exc
        if updated is None:
            raise ValueError("المستأجر غير نشط أو غير موجود.")
        await state.clear()
        await repo.log_audit(
            db, actor_id=cfg.owner_id, action="tenant_apartment_changed",
            entity_type="tenant", entity_id=tenant_id, details={"apartment_id": apartment_id},
        )
        await message.answer("✅ تم تحديث شقة المستأجر.")
        await _send_tenant_card(message, db, updated, cfg)
        return
    else:
        raise ValueError("حقل غير مدعوم.")

    updated = await tenants_repo.update_tenant(db, tenant_id, **updates)
    await state.clear()
    await repo.log_audit(
        db, actor_id=cfg.owner_id, action=f"tenant_{field}_updated",
        entity_type="tenant", entity_id=tenant_id,
        details={field: str(updates[field])[:200]},
    )
    await message.answer("✅ تم التحديث دون المساس بسجل المدفوعات.")
    await _send_tenant_card(message, db, updated, cfg)


async def _send_tenant_card(message: Message, db, tenant: dict, cfg: Settings) -> None:
    from app.bot.utils import today as today_of

    state = state_from_row(tenant, today=today_of(cfg))
    stats = await tenants_repo.payment_stats(db, tenant["id"])
    await message.answer(
        to_html(tenant_text(tenant, state, stats)),
        reply_markup=kb.tenant_profile(tenant),
        parse_mode="HTML",
    )
