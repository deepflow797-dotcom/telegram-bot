"""Build an Excel workbook with the account's own rental data.

Every account (owner or customer) exports its own dataset: the module only uses
the repositories, which run under the account's ``search_path``, so the file can
never contain another account's rows.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from app.core.xlsx import Sheet, build_workbook
from app.db import payments as payments_repo
from app.db import repository as repo
from app.db import tenants as tenants_repo
from app.db.connection import Database
from app.services.reports_data import state_from_row


def _money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value or 0))
    except Exception:  # noqa: BLE001 - defensive, data is NUMERIC in the DB
        return Decimal(0)


def _status_label(state) -> str:
    return {
        "covered": "مغطى",
        "upcoming": "قريب الاستحقاق",
        "due_today": "مستحق اليوم",
        "overdue": "متأخر",
        "inactive": "مؤرشف",
    }.get(getattr(state, "status", ""), str(getattr(state, "status", "")))


async def build_export(
    db: Database,
    *,
    tz: dt.tzinfo,
    today: dt.date,
    currency: str,
    upcoming_days: int = 3,
    payments_limit: int = 2000,
) -> tuple[bytes, str]:
    """Return ``(xlsx_bytes, filename)`` for the active account."""
    buildings = await repo.list_buildings(db, active_only=False)
    apartments = await repo.list_apartments(db, active_only=False, limit=2000)
    tenant_rows = await tenants_repo.list_tenants(db, active_only=False, limit=2000)
    payments = await payments_repo.list_payments(db, limit=payments_limit)

    states = [state_from_row(row, today=today, upcoming_days=upcoming_days) for row in tenant_rows]

    active_tenants = sum(1 for row in tenant_rows if row["is_active"])
    archived_tenants = len(tenant_rows) - active_tenants
    total_paid = sum((_money(payment.get("amount")) for payment in payments), Decimal(0))
    outstanding = sum(
        (state.outstanding for state, row in zip(states, tenant_rows) if row["is_active"]),
        Decimal(0),
    )
    occupied = sum(1 for apartment in apartments if apartment.get("occupancy_status") == "occupied")

    summary_rows: list[list[Any]] = [
        ["تاريخ التقرير", today.isoformat()],
        ["العملة الافتراضية", currency],
        ["عدد المباني", len(buildings)],
        ["عدد الشقق", len(apartments)],
        ["الشقق المشغولة", occupied],
        ["الشقق الفارغة", len(apartments) - occupied],
        ["المستأجرون النشطون", active_tenants],
        ["المستأجرون المؤرشفون", archived_tenants],
        ["عدد الدفعات المسجلة", len(payments)],
        ["إجمالي المبالغ المستلمة", total_paid],
        ["إجمالي المتأخرات المستحقة", outstanding],
    ]

    building_rows = [
        [
            building["name"],
            building.get("address") or "",
            building.get("description") or "",
            building.get("apartments_count", 0),
            building.get("occupied_count", 0),
            "نشط" if building.get("is_active") else "مؤرشف",
        ]
        for building in buildings
    ]

    apartment_rows = [
        [
            apartment.get("building_name") or "",
            apartment["apartment_number"],
            apartment.get("floor") or "",
            "مشغولة" if apartment.get("occupancy_status") == "occupied" else "فارغة",
            apartment.get("tenant_name") or "",
            "نشطة" if apartment.get("is_active") else "مؤرشفة",
            apartment.get("description") or "",
        ]
        for apartment in apartments
    ]

    tenant_table: list[list[Any]] = []
    arrears_rows: list[list[Any]] = []
    for row, state in zip(tenant_rows, states):
        tenant_table.append(
            [
                row["full_name"],
                row.get("phone") or "",
                row.get("building_name") or "",
                row.get("apartment_number") or "",
                _money(row.get("monthly_rent")),
                (row.get("currency") or "").strip(),
                row.get("rental_start_date"),
                row.get("next_due_date"),
                _status_label(state),
                _money(state.outstanding),
                "نشط" if row["is_active"] else "مؤرشف",
                row.get("notes") or "",
            ]
        )
        if row["is_active"] and state.outstanding > 0:
            arrears_rows.append(
                [
                    row["full_name"],
                    row.get("phone") or "",
                    row.get("building_name") or "",
                    row.get("apartment_number") or "",
                    row.get("next_due_date"),
                    state.arrears_months,
                    _money(state.outstanding),
                    (row.get("currency") or "").strip(),
                ]
            )

    payment_rows = [
        [
            payment.get("payment_date"),
            payment.get("full_name") or "",
            payment.get("building_name") or "",
            payment.get("apartment_number") or "",
            _money(payment.get("amount")),
            payment.get("currency") or "",
            payment.get("notes") or "",
        ]
        for payment in payments
    ]

    workbook = build_workbook(
        [
            Sheet("ملخص", ["البند", "القيمة"], summary_rows, widths=[34, 30]),
            Sheet(
                "المباني",
                ["المبنى", "العنوان", "الوصف", "عدد الشقق", "المشغولة", "الحالة"],
                building_rows,
                widths=[28, 28, 32, 12, 12, 12],
            ),
            Sheet(
                "الشقق",
                ["المبنى", "الشقة", "الطابق", "الإشغال", "المستأجر", "الحالة", "الوصف"],
                apartment_rows,
                widths=[24, 14, 12, 12, 24, 12, 28],
            ),
            Sheet(
                "المستأجرون",
                [
                    "الاسم",
                    "الهاتف",
                    "المبنى",
                    "الشقة",
                    "الإيجار",
                    "العملة",
                    "بداية الإيجار",
                    "الاستحقاق القادم",
                    "الحالة",
                    "المتأخرات",
                    "السجل",
                    "ملاحظات",
                ],
                tenant_table,
                widths=[26, 16, 22, 12, 14, 10, 14, 14, 14, 14, 10, 28],
            ),
            Sheet(
                "الدفعات",
                ["التاريخ", "المستأجر", "المبنى", "الشقة", "المبلغ", "العملة", "ملاحظة"],
                payment_rows,
                widths=[18, 24, 22, 12, 14, 10, 30],
            ),
            Sheet(
                "المتأخرات",
                ["المستأجر", "الهاتف", "المبنى", "الشقة", "الاستحقاق", "أشهر متأخرة", "المبلغ", "العملة"],
                arrears_rows,
                widths=[24, 16, 20, 12, 14, 14, 14, 10],
            ),
        ]
    )
    filename = f"rentals-{today.isoformat()}.xlsx"
    return workbook, filename
