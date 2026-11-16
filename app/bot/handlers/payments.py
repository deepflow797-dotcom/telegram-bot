"""Manual payment registration: pick tenant → amount → confirm → register."""

from __future__ import annotations

import logging
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot import texts
from app.bot.states import PaymentForm
from app.bot.utils import (
    answer_callback,
    edit_or_send,
    is_cancel,
    now as now_of,
    parse_int,
    send_long,
    skipped,
    to_html,
    today as today_of,
)
from app.bot.views import payment_receipt, payment_success, preview_plan
from app.config import Settings
from app.core.dates import parse_amount
from app.core.formatting import format_money, status_label
from app.db import payments as payments_repo
from app.db import tenants as tenants_repo
from app.services.reports_data import state_from_row

logger = logging.getLogger(__name__)
router = Router(name="payments")

PAGE_SIZE = kb.PAY_TENANTS_PAGE_SIZE


async def show_payment_tenant_picker(
    message: Message,
    *,
    state: FSMContext,
    db,
    cfg: Settings,
    page: int = 0,
) -> None:
    tenants = await tenants_repo.list_tenants(
        db, active_only=True, limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE
    )
    if not tenants and page == 0:
        await state.clear()
        await send_long(
            message,
            "ℹ️ لا يوجد مستأجرون نشطون. أضف مستأجراً أولاً من قسم 👥 المستأجرون.",
            kb.tenants_menu(),
        )
        return
    has_next = len(tenants) > PAGE_SIZE
    tenants = tenants[:PAGE_SIZE]
    today = today_of(cfg)
    states = {
        tenant["id"]: state_from_row(tenant, today=today) for tenant in tenants
    }
    await state.clear()
    await state.set_state(PaymentForm.tenant)
    await send_long(
        message,
        "💰 *تسجيل الدفع*\nاختر المستأجر الذي استلمت منه الدفعة:",
        kb.payment_tenant_picker(tenants, states, page=page, has_next=has_next),
    )


@router.callback_query(F.data == "m:pay")
async def open_payment_picker(callback: CallbackQuery, state: FSMContext, db, cfg: Settings) -> None:
    await answer_callback(callback)
    await state.clear()
    await state.set_state(PaymentForm.tenant)
    tenants = await tenants_repo.list_tenants(db, active_only=True, limit=PAGE_SIZE + 1)
    if not tenants:
        await edit_or_send(callback, "ℹ️ لا يوجد مستأجرون نشطون.", kb.tenants_menu())
        return
    has_next = len(tenants) > PAGE_SIZE
    tenants = tenants[:PAGE_SIZE]
    today = today_of(cfg)
    states = {tenant["id"]: state_from_row(tenant, today=today) for tenant in tenants}
    await edit_or_send(
        callback,
        "💰 *تسجيل الدفع*\nاختر المستأجر:",
        kb.payment_tenant_picker(tenants, states, page=0, has_next=has_next),
    )


@router.callback_query(F.data.startswith("pay:list:"))
async def paginate_payment_picker(callback: CallbackQuery, state: FSMContext, db, cfg: Settings) -> None:
    page = max(0, parse_int(callback.data.split(":")[2]))
    tenants = await tenants_repo.list_tenants(
        db, active_only=True, limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE
    )
    await answer_callback(callback)
    if not tenants:
        await edit_or_send(callback, "ℹ️ لا يوجد المزيد.", kb.tenants_menu())
        return
    has_next = len(tenants) > PAGE_SIZE
    tenants = tenants[:PAGE_SIZE]
    today = today_of(cfg)
    states = {tenant["id"]: state_from_row(tenant, today=today) for tenant in tenants}
    await edit_or_send(
        callback,
        "💰 *تسجيل الدفع*\nاختر المستأجر:",
        kb.payment_tenant_picker(tenants, states, page=page, has_next=has_next),
    )


@router.callback_query(F.data.startswith("pay:sel:"))
async def select_tenant_for_payment(
    callback: CallbackQuery, state: FSMContext, db, cfg: Settings
) -> None:
    tenant_id = parse_int(callback.data.split(":")[2])
    tenant = await tenants_repo.get_tenant(db, tenant_id)
    if tenant is None:
        await answer_callback(callback, "⚠️ لم يتم العثور على المستأجر.", alert=True)
        return
    if not tenant["is_active"]:
        await answer_callback(callback, texts.PAYMENT_BLOCKED_ARCHIVED, alert=True)
        return
    state_now = state_from_row(tenant, today=today_of(cfg))
    await state.clear()
    await state.set_state(PaymentForm.amount)
    await state.update_data(tenant_id=tenant_id)
    await answer_callback(callback)
    await edit_or_send(
        callback,
        "💰 *تسجيل الدفع*\n\n"
        f"👤 {tenant['full_name']}\n"
        f"🏢 {tenant['building_name']} / شقة {tenant['apartment_number']}\n"
        f"💳 الإيجار الشهري: {format_money(state_now.monthly_rent, state_now.currency)}\n"
        f"📌 الاستحقاق: {state_now.next_due.strftime('%d/%m/%Y')} — {status_label(state_now.status)}\n"
        f"💼 المطلوب للسداد: *{format_money(state_now.required_to_close, state_now.currency)}*\n\n"
        f"{texts.ASK_PAYMENT_AMOUNT}",
        kb.cancel_keyboard(f"t:view:{tenant_id}"),
    )


@router.message(PaymentForm.amount)
async def payment_amount(message: Message, state: FSMContext, db, cfg: Settings) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("⚠️ أرسل مبلغاً صحيحاً أكبر من صفر.")
        return
    data = await state.get_data()
    tenant = await tenants_repo.get_tenant(db, int(data.get("tenant_id", 0)))
    if tenant is None:
        await state.clear()
        await message.answer("⚠️ لم يتم العثور على المستأجر.", reply_markup=kb.main_menu())
        return
    await state.update_data(amount=str(amount))
    await state.set_state(PaymentForm.notes)
    await message.answer(
        f"💵 المبلغ: {format_money(amount, tenant['currency'])}\n\n{texts.ASK_PAYMENT_NOTES}",
        reply_markup=kb.cancel_keyboard(f"t:view:{tenant['id']}"),
    )


@router.message(PaymentForm.notes)
async def payment_notes(message: Message, state: FSMContext, db, cfg: Settings) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    data = await state.get_data()
    tenant = await tenants_repo.get_tenant(db, int(data.get("tenant_id", 0)))
    amount = parse_amount(data.get("amount"))
    if tenant is None or amount is None:
        await state.clear()
        await message.answer("⚠️ بيانات الدفعة غير مكتملة. أعد المحاولة.", reply_markup=kb.main_menu())
        return
    if not tenant["is_active"]:
        await state.clear()
        await message.answer(texts.PAYMENT_BLOCKED_ARCHIVED, reply_markup=kb.main_menu())
        return

    notes = skipped(message.text)
    pending = await payments_repo.create_pending_payment(
        db,
        tenant_id=tenant["id"],
        amount=amount,
        notes=notes,
        created_by=cfg.owner_id,
    )
    when = now_of(cfg)
    preview = preview_plan(tenant, amount, when)
    await state.set_state(PaymentForm.confirm)
    await state.update_data(pending_id=str(pending["id"]))
    await message.answer(
        to_html(payment_receipt(tenant, amount=amount, currency=tenant["currency"], when=when, preview=preview)),
        reply_markup=kb.confirm_payment(str(pending["id"])),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("p:ok:"))
async def confirm_payment(callback: CallbackQuery, state: FSMContext, db, cfg: Settings) -> None:
    raw_id = callback.data.split(":", 2)[2]
    try:
        pending_id = uuid.UUID(raw_id)
    except ValueError:
        await answer_callback(callback, texts.EXPIRED_ACTION, alert=True)
        return

    result = await payments_repo.register_payment(
        db,
        pending_id=pending_id,
        registered_by=cfg.owner_id,
        now=now_of(cfg),
    )
    status = result["status"]

    if status == "duplicate":
        await answer_callback(callback, texts.PAYMENT_DUPLICATE, alert=True)
        await edit_or_send(
            callback,
            texts.PAYMENT_DUPLICATE + "\nلا حاجة لأي إجراء إضافي.",
            kb.main_inline(),
        )
        await state.clear()
        return
    if status == "tenant_inactive":
        await answer_callback(callback, texts.PAYMENT_BLOCKED_ARCHIVED, alert=True)
        await state.clear()
        return
    if status == "tenant_missing":
        await answer_callback(callback, texts.PAYMENT_TENANT_MISSING, alert=True)
        await state.clear()
        return
    if status != "ok":
        await answer_callback(callback, texts.EXPIRED_ACTION, alert=True)
        await state.clear()
        return

    await state.clear()
    payment = result["payment"]
    plan = result["plan"]
    tenant = await tenants_repo.get_tenant(db, payment["tenant_id"])
    await answer_callback(callback, "تم تسجيل الدفعة ✅")
    await edit_or_send(
        callback,
        payment_success(tenant, payment=payment, preview=plan, previous_due=result["previous_next_due"]),
        kb.tenant_profile(tenant),
    )
    logger.info(
        "payment registered",
        extra={
            "extra_fields": {
                "payment_id": payment["id"],
                "tenant_id": tenant["id"],
                "amount": str(payment["amount"]),
                "type": payment["payment_type"],
            }
        },
    )


@router.callback_query(F.data == "p:no")
async def cancel_payment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await answer_callback(callback, "تم الإلغاء")
    await edit_or_send(callback, "✖️ تم إلغاء تسجيل الدفعة.", kb.main_inline())
