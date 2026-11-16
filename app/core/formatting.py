"""Arabic-first formatting helpers."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.core.dates import add_months
from app.core.rent import TenantStatus

CURRENCY_NAMES_AR = {
    "MRU": "أوقية",
    "USD": "دولار",
    "EUR": "يورو",
    "SAR": "ريال سعودي",
    "AED": "درهم",
    "MAD": "درهم مغربي",
    "EGP": "جنيه",
    "KWD": "دينار كويتي",
    "QAR": "ريال قطري",
}

STATUS_LABELS_AR = {
    TenantStatus.COVERED: "مدفوع ✅",
    TenantStatus.UPCOMING: "مستحق قريباً ⏳",
    TenantStatus.DUE_TODAY: "مستحق اليوم 🔔",
    TenantStatus.OVERDUE: "متأخر ⚠️",
    TenantStatus.INACTIVE: "مؤرشف 📁",
}

PAYMENT_TYPE_LABELS_AR = {
    "full": "دفع كامل",
    "partial": "دفع جزئي",
    "overpayment": "دفع زائد / مقدّم",
}


def currency_name(code: str) -> str:
    return CURRENCY_NAMES_AR.get((code or "").upper(), code)


def format_money(amount: Decimal | int | float | None, currency: str = "") -> str:
    if amount is None:
        return "—"
    value = Decimal(str(amount))
    whole = value == value.to_integral_value()
    text = f"{value:,.0f}" if whole else f"{value:,.2f}"
    if currency:
        return f"{text} {currency_name(currency)}"
    return text


def format_date(value: dt.date | dt.datetime | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        value = value.date()
    return value.strftime("%d/%m/%Y")


def format_datetime(value: dt.datetime | None, tz=None) -> str:
    if value is None:
        return "—"
    if tz is not None and value.tzinfo is not None:
        value = value.astimezone(tz)
    return value.strftime("%d/%m/%Y %H:%M")


def format_period(start: dt.date, end: dt.date) -> str:
    return f"{format_date(start)} ← {format_date(end)}"


def status_label(status: TenantStatus | str) -> str:
    if isinstance(status, str):
        try:
            status = TenantStatus(status)
        except ValueError:
            return str(status)
    return STATUS_LABELS_AR.get(status, str(status))


def payment_type_label(value: str) -> str:
    return PAYMENT_TYPE_LABELS_AR.get(value, value)


def period_after(start: dt.date) -> int:
    """Helper for tests/UI: number of calendar months span of one cycle."""
    return 1


def next_cycle_label(next_due: dt.date, today: dt.date) -> str:
    days = (next_due - today).days
    if days == 0:
        return "اليوم"
    if days > 0:
        return f"بعد {days} يوم" if days != 1 else "بعد يوم واحد"
    late = -days
    return f"متأخر {late} يوم"


def month_label(value: dt.date) -> str:
    return value.strftime("%m/%Y")


def month_range(value: dt.date) -> tuple[dt.date, dt.date]:
    first = value.replace(day=1)
    return first, add_months(first, 1)


def truncate(text: str | None, limit: int = 120) -> str:
    if not text:
        return "—"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
