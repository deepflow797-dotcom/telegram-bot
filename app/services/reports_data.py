"""Collect the data needed by reports (single place, reused by bot and cron)."""

from __future__ import annotations

import datetime as dt

from app.core.dates import add_months, day_start, month_bounds
from app.core.rent import TenantState, compute_state
from app.core.reports import (
    BuildingBreakdown,
    CurrencyTotals,
    DailyReport,
    MonthlyReport,
    ReportTenant,
    build_daily_report,
)
from app.db import payments as payments_repo
from app.db import tenants as tenants_repo
from app.db.connection import Database


def state_from_row(row: dict, *, today: dt.date, upcoming_days: int = 3) -> TenantState:
    return compute_state(
        monthly_rent=row["monthly_rent"],
        currency=row["currency"].strip() if isinstance(row["currency"], str) else row["currency"],
        cycle_start=row["current_cycle_start"],
        next_due=row["next_due_date"],
        credit=row["credit_amount"],
        is_active=row["is_active"],
        today=today,
        upcoming_days=upcoming_days,
    )


async def tenant_states(
    db: Database,
    *,
    today: dt.date,
    upcoming_days: int = 3,
    active_only: bool = True,
    limit: int = 1000,
) -> list[tuple[dict, TenantState]]:
    rows = await tenants_repo.list_tenants(db, active_only=active_only, limit=limit)
    return [(row, state_from_row(row, today=today, upcoming_days=upcoming_days)) for row in rows]


async def totals_between(
    db: Database, *, start: dt.datetime, end: dt.datetime
) -> tuple[CurrencyTotals, int]:
    grouped = await payments_repo.totals_by_currency(db, start=start, end=end)
    totals = CurrencyTotals()
    count = 0
    for currency, (amount, payments_count) in grouped.items():
        totals.add(currency.strip(), amount)
        count += payments_count
    return totals, count


async def build_daily_report_for(
    db: Database,
    *,
    tz,
    report_date: dt.date,
    now: dt.datetime,
    upcoming_days: int = 3,
) -> DailyReport:
    states = await tenant_states(db, today=report_date, upcoming_days=upcoming_days)

    day_from = day_start(report_date, tz)
    day_to = day_start(report_date + dt.timedelta(days=1), tz)
    collected_today, count_today = await totals_between(db, start=day_from, end=day_to)

    month_start, month_end = month_bounds(report_date)
    collected_month, count_month = await totals_between(
        db, start=day_start(month_start, tz), end=day_start(month_end, tz)
    )

    return build_daily_report(
        tenants=states,
        report_date=report_date,
        generated_at=now,
        collected_today=collected_today,
        collected_month=collected_month,
        payments_today_count=count_today,
        payments_month_count=count_month,
    )


def entries_for_status(report: DailyReport, status) -> list[ReportTenant]:
    return report.sections.get(status, [])


async def build_monthly_report_for(
    db: Database,
    *,
    tz,
    month_start: dt.date,
    today: dt.date,
    upcoming_days: int = 3,
) -> MonthlyReport:
    month_end = add_months(month_start, 1)
    states = await tenant_states(db, today=today, upcoming_days=upcoming_days)
    collected, count = await totals_between(
        db, start=day_start(month_start, tz), end=day_start(month_end, tz)
    )

    expected = CurrencyTotals()
    outstanding = CurrencyTotals()
    buildings: dict[str, BuildingBreakdown] = {}
    for row, state in states:
        if not state.is_active:
            continue
        expected.add(state.currency, state.monthly_rent)
        if state.outstanding > 0:
            outstanding.add(state.currency, state.outstanding)
        name = row.get("building_name") or "—"
        entry = buildings.get(name)
        if entry is None:
            entry = BuildingBreakdown(
                building_name=name,
                tenants=0,
                expected=CurrencyTotals(),
                collected=CurrencyTotals(),
                outstanding=CurrencyTotals(),
            )
            buildings[name] = entry
        entry.tenants += 1
        entry.expected.add(state.currency, state.monthly_rent)

    # attribute collected money to buildings via payment rows of the month
    rows = await db.fetch_all(
        """
        SELECT b.name AS building_name, p.currency, coalesce(sum(p.amount), 0) AS total
        FROM payments p
        JOIN tenants t ON t.id = p.tenant_id
        JOIN apartments a ON a.id = t.apartment_id
        JOIN buildings b ON b.id = a.building_id
        WHERE p.payment_date >= %s AND p.payment_date < %s
        GROUP BY b.name, p.currency
        """,
        (day_start(month_start, tz), day_start(month_end, tz)),
    )
    for row in rows:
        entry = buildings.get(row["building_name"])
        if entry is not None:
            entry.collected.add(row["currency"].strip(), row["total"])

    return MonthlyReport(
        month_start=month_start,
        month_end=month_end,
        active_tenants=sum(1 for _, state in states if state.is_active),
        expected=expected,
        collected=collected,
        outstanding=outstanding,
        payments_count=count,
        by_building=list(buildings.values()),
    )


async def revenue_totals(db: Database, *, tz, today: dt.date) -> dict[str, object]:
    month_start, month_end = month_bounds(today)
    year_start = today.replace(month=1, day=1)
    day_from = day_start(today, tz)
    day_to = day_start(today + dt.timedelta(days=1), tz)

    today_totals, _ = await totals_between(db, start=day_from, end=day_to)
    month_totals, _ = await totals_between(
        db, start=day_start(month_start, tz), end=day_start(month_end, tz)
    )
    year_totals, _ = await totals_between(
        db, start=day_start(year_start, tz), end=day_start(add_months(year_start, 12), tz)
    )
    all_totals, count = await totals_between(
        db, start=day_start(dt.date(1900, 1, 1), tz), end=day_start(add_months(today, 1), tz)
    )
    return {
        "today": today_totals,
        "month": month_totals,
        "year": year_totals,
        "all_time": all_totals,
        "payments_count": count,
    }
