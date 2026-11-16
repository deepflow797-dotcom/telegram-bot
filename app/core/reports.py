"""Report generation (pure functions, no I/O).

The service layer collects data, these functions turn it into Arabic texts
that respect Telegram's 4096-character message limit.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.formatting import (
    format_date,
    format_money,
    format_period,
    payment_type_label,
    status_label,
    truncate,
)
from app.core.rent import TenantState, TenantStatus

MAX_MESSAGE_LEN = 3800  # stay safely below Telegram's 4096 limit
SECTION_HEADERS = {
    TenantStatus.OVERDUE: "⚠️ المتأخرون",
    TenantStatus.DUE_TODAY: "🔔 المستحقون اليوم",
    TenantStatus.UPCOMING: "⏳ مستحق خلال الأيام القادمة",
    TenantStatus.COVERED: "✅ المدفوعون",
    TenantStatus.INACTIVE: "📁 المؤرشفون",
}


@dataclass
class ReportTenant:
    tenant_id: int
    name: str
    building: str
    apartment: str
    amount_due: Decimal
    currency: str
    due_date: dt.date
    status: TenantStatus
    monthly_rent: Decimal


@dataclass
class CurrencyTotals:
    values: dict[str, Decimal] = field(default_factory=dict)

    def add(self, currency: str, amount: Decimal) -> None:
        self.values[currency] = self.values.get(currency, Decimal("0")) + Decimal(amount)

    def get(self, currency: str) -> Decimal:
        return self.values.get(currency, Decimal("0"))

    @property
    def is_empty(self) -> bool:
        return not self.values or all(v == 0 for v in self.values.values())

    def render(self, empty: str = "0") -> str:
        if self.is_empty:
            return empty
        return " | ".join(
            format_money(amount, currency) for currency, amount in sorted(self.values.items())
        )


@dataclass
class DailyReport:
    report_date: dt.date
    generated_at: dt.datetime
    active_tenants: int
    counts: dict[str, int]
    expected_rent: CurrencyTotals
    collected_today: CurrencyTotals
    collected_month: CurrencyTotals
    outstanding: CurrencyTotals
    payments_today_count: int
    payments_month_count: int
    sections: dict[TenantStatus, list[ReportTenant]]

    def summary_dict(self) -> dict:
        return {
            "report_date": self.report_date.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "active_tenants": self.active_tenants,
            "counts": dict(self.counts),
            "expected_rent": {k: str(v) for k, v in self.expected_rent.values.items()},
            "collected_today": {k: str(v) for k, v in self.collected_today.values.items()},
            "collected_month": {k: str(v) for k, v in self.collected_month.values.items()},
            "outstanding": {k: str(v) for k, v in self.outstanding.values.items()},
        }


@dataclass
class MessageBlock:
    text: str
    tenants: list[ReportTenant] = field(default_factory=list)


def to_report_tenant(row_tenant: dict, state: TenantState) -> ReportTenant:
    return ReportTenant(
        tenant_id=int(row_tenant["id"]),
        name=row_tenant["full_name"],
        building=row_tenant.get("building_name") or "—",
        apartment=row_tenant.get("apartment_number") or "—",
        amount_due=state.outstanding if state.outstanding > 0 else state.remaining_for_period,
        currency=state.currency,
        due_date=state.next_due,
        status=state.status,
        monthly_rent=state.monthly_rent,
    )


def build_daily_report(
    *,
    tenants: list[tuple[dict, TenantState]],
    report_date: dt.date,
    generated_at: dt.datetime,
    collected_today: CurrencyTotals,
    collected_month: CurrencyTotals,
    payments_today_count: int,
    payments_month_count: int,
) -> DailyReport:
    sections: dict[TenantStatus, list[ReportTenant]] = {status: [] for status in TenantStatus}
    expected = CurrencyTotals()
    outstanding = CurrencyTotals()
    counts = {
        "covered": 0,
        "upcoming": 0,
        "due_today": 0,
        "overdue": 0,
        "inactive": 0,
    }
    active = 0
    for row, state in tenants:
        if not state.is_active:
            counts["inactive"] += 1
            continue
        active += 1
        counts[state.status.value] = counts.get(state.status.value, 0) + 1
        expected.add(state.currency, state.monthly_rent)
        if state.outstanding > 0:
            outstanding.add(state.currency, state.outstanding)
        sections[state.status].append(to_report_tenant(row, state))

    return DailyReport(
        report_date=report_date,
        generated_at=generated_at,
        active_tenants=active,
        counts=counts,
        expected_rent=expected,
        collected_today=collected_today,
        collected_month=collected_month,
        outstanding=outstanding,
        payments_today_count=payments_today_count,
        payments_month_count=payments_month_count,
        sections=sections,
    )


def _split_lines(header: str, lines: list[str], limit: int = MAX_MESSAGE_LEN) -> list[str]:
    chunks: list[str] = []
    current = header
    for line in lines:
        candidate = f"{current}\n{line}"
        if len(candidate) > limit and current.strip() != header.strip():
            chunks.append(current)
            current = header.rstrip() + " (تكملة)\n" + line
        elif len(candidate) > limit:
            # single line longer than the limit: hard split
            chunks.append(candidate[:limit])
            current = header.rstrip() + " (تكملة)\n" + candidate[limit:]
        else:
            current = candidate
    chunks.append(current)
    return chunks


def render_daily_report(report: DailyReport) -> list[MessageBlock]:
    """Render the daily report as one or more Telegram message blocks."""
    currency_hint = report.expected_rent.render()
    summary_lines = [
        f"📅 التاريخ: {format_date(report.report_date)}",
        "",
        "📌 *الملخص العام*",
        f"• المستأجرون النشطون: {report.active_tenants}",
        f"• المدفوعون (مغطى): {report.counts.get('covered', 0)}",
        f"• مستحق اليوم: {report.counts.get('due_today', 0)}",
        f"• متأخرون: {report.counts.get('overdue', 0)}",
        f"• يستحق خلال 3 أيام: {report.counts.get('upcoming', 0)}",
        "",
        f"💰 الإيجار المتوقع شهرياً: {currency_hint}",
        f"🧾 المحصل اليوم: {report.collected_today.render()} ({report.payments_today_count} دفعة)",
        f"🧾 المحصل هذا الشهر: {report.collected_month.render()} ({report.payments_month_count} دفعة)",
        f"📉 إجمالي المتأخرات: {report.outstanding.render()}",
    ]
    blocks = [MessageBlock(text="\n".join(summary_lines))]

    for status in (
        TenantStatus.OVERDUE,
        TenantStatus.DUE_TODAY,
        TenantStatus.UPCOMING,
        TenantStatus.COVERED,
    ):
        entries = report.sections.get(status, [])
        if not entries:
            continue
        header = f"{SECTION_HEADERS[status]} ({len(entries)})"
        lines = []
        for index, tenant in enumerate(entries, start=1):
            amount = (
                tenant.amount_due
                if status in (TenantStatus.OVERDUE, TenantStatus.DUE_TODAY)
                else tenant.monthly_rent
            )
            lines.append(
                f"{index}. {tenant.name} — {tenant.building} / شقة {tenant.apartment}\n"
                f"   المبلغ: {format_money(amount, tenant.currency)} | "
                f"الاستحقاق: {format_date(tenant.due_date)} | {status_label(tenant.status)}"
            )
        chunks = _split_lines(header, lines)
        # attach tenants to the block that mentions them where possible
        blocks.extend(MessageBlock(text=chunk, tenants=entries if len(chunks) == 1 else []) for chunk in chunks)

    if report.active_tenants == 0:
        blocks.append(
            MessageBlock(text="ℹ️ لا يوجد مستأجرون نشطون حالياً. أضف مبنى وشقة ومستأجراً للبدء.")
        )
    return blocks


def render_tenant_line(tenant: ReportTenant) -> str:
    return (
        f"• {tenant.name} — {tenant.building} / شقة {tenant.apartment}\n"
        f"  الإيجار: {format_money(tenant.monthly_rent, tenant.currency)} | "
        f"الاستحقاق: {format_date(tenant.due_date)} | {status_label(tenant.status)}"
    )


def render_overdue_report(entries: list[ReportTenant], today: dt.date) -> list[str]:
    if not entries:
        return ["✅ لا توجد متأخرات حالياً."]
    total = CurrencyTotals()
    lines = [f"⚠️ *المتأخرون* ({len(entries)}) — {format_date(today)}", ""]
    for tenant in entries:
        days_late = (today - tenant.due_date).days
        lines.append(f"{render_tenant_line(tenant)}\n  ⏰ تأخر {days_late} يوم")
        total.add(tenant.currency, tenant.amount_due)
    lines.extend(["", f"📉 إجمالي المتأخرات: {total.render()}"])
    return _split_lines("", lines)


def render_upcoming_report(entries: list[ReportTenant], today: dt.date, days: int = 3) -> list[str]:
    if not entries:
        return [f"✅ لا توجد مدفوعات مستحقة خلال {days} أيام القادمة."]
    lines = [f"⏳ *مستحق خلال {days} أيام* ({len(entries)}) — {format_date(today)}", ""]
    for tenant in entries:
        remaining = (tenant.due_date - today).days
        lines.append(f"{render_tenant_line(tenant)}\n  📅 متبقٍ {remaining} يوم")
    return _split_lines("", lines)


def render_payments_list(payments: list[dict], title: str) -> list[str]:
    if not payments:
        return ["ℹ️ لا توجد مدفوعات مسجلة بعد."]
    lines = [f"🧾 *{title}* ({len(payments)})", ""]
    for payment in payments:
        when = payment["payment_date"]
        lines.append(
            f"• {payment['full_name']} — {payment.get('building_name', '—')} / "
            f"شقة {payment.get('apartment_number', '—')}\n"
            f"  {format_money(payment['amount'], payment['currency'])} | "
            f"{payment_type_label(payment['payment_type'])} | "
            f"{when.strftime('%d/%m/%Y %H:%M') if isinstance(when, dt.datetime) else when}"
        )
        if payment.get("notes"):
            lines.append(f"  📝 {truncate(payment['notes'], 80)}")
    return _split_lines("", lines)


@dataclass
class BuildingBreakdown:
    building_name: str
    tenants: int
    expected: CurrencyTotals
    collected: CurrencyTotals
    outstanding: CurrencyTotals


@dataclass
class MonthlyReport:
    month_start: dt.date
    month_end: dt.date
    active_tenants: int
    expected: CurrencyTotals
    collected: CurrencyTotals
    outstanding: CurrencyTotals
    payments_count: int
    by_building: list[BuildingBreakdown]


def render_monthly_report(report: MonthlyReport) -> list[str]:
    lines = [
        f"📊 *تقرير الشهر* {report.month_start.strftime('%m/%Y')}",
        f"الفترة: {format_period(report.month_start, report.month_end)}",
        "",
        f"• المستأجرون النشطون: {report.active_tenants}",
        f"• الإيجار المتوقع شهرياً: {report.expected.render()}",
        f"• المحصل خلال الشهر: {report.collected.render()} ({report.payments_count} دفعة)",
        f"• المتأخرات الحالية: {report.outstanding.render()}",
    ]
    if report.by_building:
        lines.extend(["", "🏢 *حسب المبنى*"])
        for item in sorted(report.by_building, key=lambda b: b.building_name):
            lines.append(
                f"• {item.building_name} ({item.tenants} مستأجر): "
                f"متوقع {item.expected.render()} | محصل {item.collected.render()}"
            )
    return _split_lines("", lines)


def render_revenue_report(
    *,
    today_total: CurrencyTotals,
    month_total: CurrencyTotals,
    year_total: CurrencyTotals,
    all_time_total: CurrencyTotals,
    payments_count: int,
) -> list[str]:
    lines = [
        "💰 *إجمالي الإيرادات*",
        "",
        f"• اليوم: {today_total.render()}",
        f"• هذا الشهر: {month_total.render()}",
        f"• هذه السنة: {year_total.render()}",
        f"• الإجمالي الكلي: {all_time_total.render()}",
        "",
        f"🧾 عدد الدفعات المسجلة: {payments_count}",
    ]
    return _split_lines("", lines)
