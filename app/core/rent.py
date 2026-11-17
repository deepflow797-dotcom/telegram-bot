"""Rental cycle domain logic: statuses, balances and payment allocation.

Rental cycle model
------------------
* ``current_cycle_start`` / ``next_due_date`` describe the currently *funded*
  rental period ``[current_cycle_start, next_due_date)``. ``next_due_date`` is
  when the next payment becomes due.
* ``credit_amount`` is money received that is not yet attached to a funded
  period (partial payments / prepayments).
* When a payment completes the amount required for the upcoming period, the new
  period starts on the **actual payment date** (documented default policy) and
  the next due date becomes ``add_months(payment_date, 1)``.
* Extra money after the period is closed funds following periods one month at
  a time; whatever is left stays as ``credit_amount`` (never discarded).

Arrears
-------
A tenant who does not pay keeps the same ``next_due_date``. Each complete
calendar month elapsed after ``next_due_date`` adds one month of rent to the
amount required to become current, so the debt is never silently forgotten.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from app.core.dates import add_months, months_between

MONEY_QUANT = Decimal("0.01")
UPCOMING_DAYS_DEFAULT = 3
MAX_PERIODS_PER_PAYMENT = 36


class TenantStatus(str, Enum):
    COVERED = "covered"
    UPCOMING = "upcoming"
    DUE_TODAY = "due_today"
    OVERDUE = "overdue"
    INACTIVE = "inactive"


class PaymentType(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    OVERPAYMENT = "overpayment"


def money(value: Decimal | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def arrears_months(next_due: dt.date, today: dt.date) -> int:
    """Complete calendar months elapsed after ``next_due`` (0 when not late)."""
    if today <= next_due:
        return 0
    return months_between(next_due, today)


def status_for(
    *,
    next_due: dt.date,
    today: dt.date,
    is_active: bool = True,
    upcoming_days: int = UPCOMING_DAYS_DEFAULT,
) -> TenantStatus:
    if not is_active:
        return TenantStatus.INACTIVE
    if today > next_due:
        return TenantStatus.OVERDUE
    days = (next_due - today).days
    if days == 0:
        return TenantStatus.DUE_TODAY
    if days <= upcoming_days:
        return TenantStatus.UPCOMING
    return TenantStatus.COVERED


@dataclass(frozen=True)
class TenantState:
    """Everything the UI/report needs about one tenant, derived from data."""

    monthly_rent: Decimal
    currency: str
    cycle_start: dt.date
    next_due: dt.date
    credit: Decimal
    is_active: bool
    today: dt.date
    status: TenantStatus
    arrears_months: int
    # Amount that must be received to close the pending period and roll forward.
    required_to_close: Decimal
    # Amount actually owed *now* (0 while the next period is not yet due).
    outstanding: Decimal
    # Amount still missing for the upcoming period (after prepayments).
    remaining_for_period: Decimal
    days_until_due: int
    is_overdue: bool = field(default=False)

    @property
    def covered_until(self) -> dt.date:
        return self.next_due


def compute_state(
    *,
    monthly_rent: Decimal,
    currency: str,
    cycle_start: dt.date,
    next_due: dt.date,
    credit: Decimal | None,
    is_active: bool,
    today: dt.date,
    upcoming_days: int = UPCOMING_DAYS_DEFAULT,
) -> TenantState:
    rent = money(monthly_rent)
    credit_amount = money(credit or 0)
    late_months = arrears_months(next_due, today)
    required = money(rent * (1 + late_months) - credit_amount)
    if required < 0:
        required = money(0)
    status = status_for(
        next_due=next_due, today=today, is_active=is_active, upcoming_days=upcoming_days
    )
    owed_now = required if status in (TenantStatus.OVERDUE, TenantStatus.DUE_TODAY) else money(0)
    remaining_period = money(max(Decimal(0), rent - credit_amount))
    return TenantState(
        monthly_rent=rent,
        currency=currency,
        cycle_start=cycle_start,
        next_due=next_due,
        credit=credit_amount,
        is_active=is_active,
        today=today,
        status=status,
        arrears_months=late_months,
        required_to_close=required,
        outstanding=owed_now,
        remaining_for_period=remaining_period,
        days_until_due=(next_due - today).days,
        is_overdue=status is TenantStatus.OVERDUE,
    )


@dataclass(frozen=True)
class PeriodAllocation:
    period_start: dt.date
    period_end: dt.date
    amount: Decimal


@dataclass(frozen=True)
class PaymentPlan:
    amount: Decimal
    currency: str
    payment_date: dt.date
    required_before: Decimal
    payment_type: PaymentType
    allocations: list[PeriodAllocation]
    new_cycle_start: dt.date
    new_next_due: dt.date
    new_credit: Decimal
    closes_period: bool
    periods_funded: int
    remaining_after: Decimal

    @property
    def surfaces_arrears(self) -> int:
        return self.periods_funded


def plan_payment(
    *,
    monthly_rent: Decimal,
    currency: str,
    cycle_start: dt.date,
    next_due: dt.date,
    credit: Decimal | None,
    amount: Decimal,
    payment_date: dt.date,
    max_periods: int = MAX_PERIODS_PER_PAYMENT,
) -> PaymentPlan:
    """Allocate a received payment to rental periods.

    Pure function: no I/O, no clock access (``payment_date`` is injected), which
    makes partial payments, overpayments and cycle renewal fully testable.
    """
    rent = money(monthly_rent)
    received = money(amount)
    if received <= 0:
        raise ValueError("payment amount must be positive")
    credit_amount = money(credit or 0)
    available = money(credit_amount + received)

    late_months = arrears_months(next_due, payment_date)
    required = money(rent * (1 + late_months))

    if available < required:
        return PaymentPlan(
            amount=received,
            currency=currency,
            payment_date=payment_date,
            required_before=required,
            payment_type=PaymentType.PARTIAL,
            allocations=[],
            new_cycle_start=cycle_start,
            new_next_due=next_due,
            new_credit=available,
            closes_period=False,
            periods_funded=0,
            remaining_after=money(required - available),
        )

    allocations: list[PeriodAllocation] = []
    remaining = available
    consume = required
    remaining = money(remaining - consume)
    new_start = payment_date
    new_due = add_months(payment_date, 1)
    allocations.append(PeriodAllocation(payment_date, new_due, consume))
    periods = 1

    while remaining >= rent and periods < max_periods:
        remaining = money(remaining - rent)
        start = new_due
        new_due = add_months(start, 1)
        allocations.append(PeriodAllocation(start, new_due, rent))
        new_start = start
        periods += 1

    payment_type = (
        PaymentType.OVERPAYMENT if (periods > 1 or remaining > 0) else PaymentType.FULL
    )
    return PaymentPlan(
        amount=received,
        currency=currency,
        payment_date=payment_date,
        required_before=required,
        payment_type=payment_type,
        allocations=allocations,
        new_cycle_start=new_start,
        new_next_due=new_due,
        new_credit=remaining,
        closes_period=True,
        periods_funded=periods,
        remaining_after=money(max(Decimal(0), rent - remaining)),
    )
