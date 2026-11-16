"""Text renderers for cards and receipts shown in the chat."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.core.dates import add_months
from app.core.formatting import (
    format_date,
    format_datetime,
    format_money,
    format_period,
    payment_type_label,
    status_label,
    truncate,
)
from app.core.rent import PaymentPlan, TenantState, plan_payment


def building_text(building: dict) -> str:
    status = "✅ نشط" if building.get("is_active") else "🗄 مؤرشف"
    lines = [
        f"🏢 *{building['name']}*",
        f"📍 العنوان: {building.get('address') or '—'}",
        f"📝 الوصف: {truncate(building.get('description'), 200)}",
        "",
        f"🚪 الشقق: {building.get('apartments_count', 0)} "
        f"(مشغولة: {building.get('occupied_count', 0)})",
        f"🗓 تاريخ الإضافة: {format_date(building.get('created_at'))}",
        f"الحالة: {status}",
    ]
    return "\n".join(lines)


def apartment_text(apartment: dict) -> str:
    status = "🟢 مشغولة" if apartment.get("tenant_name") else "⚪️ فارغة"
    if not apartment.get("is_active"):
        status += " (مؤرشفة)"
    lines = [
        f"🚪 *شقة {apartment['apartment_number']}* — {apartment.get('building_name', '')}",
        f"🏗 الطابق: {apartment.get('floor') or '—'}",
        f"📝 الوصف: {truncate(apartment.get('description'), 160)}",
        f"👤 المستأجر: {apartment.get('tenant_name') or 'لا يوجد'}",
        f"الحالة: {status}",
    ]
    return "\n".join(lines)


def tenant_text(tenant: dict, state: TenantState, stats: dict) -> str:
    lines = [
        f"👤 *{tenant['full_name']}*",
        f"📱 الهاتف: {tenant.get('phone') or '—'}",
        f"🏢 المبنى: {tenant.get('building_name', '—')}",
        f"🚪 الشقة: {tenant.get('apartment_number', '—')}"
        + (f" — الطابق {tenant['floor']}" if tenant.get("floor") else ""),
        "",
        f"💰 الإيجار الشهري: {format_money(state.monthly_rent, state.currency)}",
        f"📅 بداية الإيجار: {format_date(tenant.get('rental_start_date'))}",
        f"🗓 الفترة المغطاة: {format_period(state.cycle_start, state.next_due)}",
        f"📌 الاستحقاق القادم: {format_date(state.next_due)}",
        f"🔔 الحالة: {status_label(state.status)}",
        f"💵 المتبقي على الفترة القادمة: {format_money(state.remaining_for_period, state.currency)}",
    ]
    if state.outstanding > 0:
        lines.append(f"⚠️ *المستحق الآن: {format_money(state.outstanding, state.currency)}*")
        if state.arrears_months:
            lines.append(f"⏰ متأخرات: {state.arrears_months} شهر إضافي")
    if state.credit > 0:
        lines.append(f"🏦 رصيد دفعات مقدمة: {format_money(state.credit, state.currency)}")
    lines.extend(
        [
            "",
            f"🧾 إجمالي المدفوعات: {format_money(stats.get('total_paid'), state.currency)}"
            f" ({stats.get('payments_count', 0)} دفعة)",
            f"🕐 آخر دفعة: {format_datetime(stats.get('last_payment_at'))}",
            f"📝 ملاحظات: {truncate(tenant.get('notes'), 200)}",
        ]
    )
    return "\n".join(lines)


def payment_receipt(
    tenant: dict,
    *,
    amount: Decimal,
    currency: str,
    when: dt.datetime,
    preview: PaymentPlan,
) -> str:
    """Confirmation screen shown before a payment is written to the database."""
    lines = [
        "🧾 *تأكيد تسجيل الدفعة*",
        "",
        f"👤 المستأجر: *{tenant['full_name']}*",
        f"🏢 المبنى: {tenant.get('building_name', '—')}",
        f"🚪 الشقة: {tenant.get('apartment_number', '—')}",
        "",
        f"💵 المبلغ المستلم: *{format_money(amount, currency)}*",
        f"💳 المطلوب للسداد: {format_money(preview.required_before, currency)}",
        f"🕐 التاريخ والوقت: {format_datetime(when)}",
        f"📄 نوع الدفعة: {payment_type_label(preview.payment_type.value)}",
        f"📌 الاستحقاق الحالي: {format_date(preview.new_next_due if preview.closes_period else preview.payment_date)}",
        f"💼 المتبقي بعد الدفعة: {format_money(preview.remaining_after, currency)}",
    ]
    if not preview.closes_period:
        lines.append(
            "\n⚠️ المبلغ أقل من المطلوب — ستُسجل دفعة جزئية ويبقى الاستحقاق كما هو."
        )
    elif preview.new_credit > 0:
        lines.append(
            f"\nℹ️ يوجد مبلغ زائد {format_money(preview.new_credit, currency)} "
            "سيُحفظ كرصيد للفترة القادمة."
        )
    lines.append("\nهل تريد تأكيد تسجيل الدفعة؟")
    return "\n".join(lines)


def payment_success(
    tenant: dict,
    *,
    payment: dict,
    preview: PaymentPlan,
    previous_due: dt.date,
) -> str:
    lines = [
        "✅ *تم تسجيل الدفعة بنجاح*",
        "",
        f"👤 {tenant['full_name']}",
        f"💵 {format_money(payment['amount'], payment['currency'])}",
        f"📄 {payment_type_label(payment['payment_type'])}",
        f"🕐 {format_datetime(payment['payment_date'])}",
        f"🔢 رقم الدفعة: #{payment['id']}",
        "",
    ]
    if preview.closes_period:
        lines.extend(
            [
                f"📌 الاستحقاق السابق: {format_date(previous_due)}",
                f"📌 الاستحقاق الجديد: *{format_date(preview.new_next_due)}*",
                f"🗓 الفترة المغطاة: {format_period(preview.new_cycle_start, preview.new_next_due)}",
            ]
        )
    else:
        lines.extend(
            [
                f"📌 الاستحقاق كما هو: {format_date(previous_due)}",
                f"💼 المتبقي: {format_money(preview.remaining_after, payment['currency'])}",
            ]
        )
    if preview.new_credit > 0:
        lines.append(f"🏦 رصيد زائد محفوظ: {format_money(preview.new_credit, payment['currency'])}")
    return "\n".join(lines)


def payment_line(payment: dict) -> str:
    when = payment["payment_date"]
    stamp = when.strftime("%d/%m/%Y %H:%M") if isinstance(when, dt.datetime) else str(when)
    return (
        f"• {stamp} — {format_money(payment['amount'], payment['currency'])} "
        f"({payment_type_label(payment['payment_type'])})"
    )


def preview_plan(tenant: dict, amount: Decimal, when: dt.datetime) -> PaymentPlan:
    """Pure preview of what a payment would do (used by the confirm screen)."""
    return plan_payment(
        monthly_rent=tenant["monthly_rent"],
        currency=tenant["currency"],
        cycle_start=tenant["current_cycle_start"],
        next_due=tenant["next_due_date"],
        credit=tenant["credit_amount"],
        amount=amount,
        payment_date=when.date(),
    )


def next_cycle(due: dt.date) -> dt.date:
    return add_months(due, 1)
