"""Rental cycle engine: statuses, partial payments, overpayments, arrears."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.core.rent import (
    PaymentType,
    TenantStatus,
    arrears_months,
    compute_state,
    plan_payment,
    status_for,
)

RENT = Decimal("10000.00")
TODAY = dt.date(2026, 1, 15)


def make_state(**overrides):
    params = dict(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 12, 15),
        next_due=dt.date(2026, 1, 15),
        credit=Decimal(0),
        is_active=True,
        today=TODAY,
    )
    params.update(overrides)
    return compute_state(**params)


# ---------------------------------------------------------------------------
# statuses
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (dt.date(2026, 1, 1), TenantStatus.COVERED),
        (dt.date(2026, 1, 11), TenantStatus.COVERED),
        (dt.date(2026, 1, 12), TenantStatus.UPCOMING),
        (dt.date(2026, 1, 14), TenantStatus.UPCOMING),
        (dt.date(2026, 1, 15), TenantStatus.DUE_TODAY),
        (dt.date(2026, 1, 16), TenantStatus.OVERDUE),
        (dt.date(2026, 2, 20), TenantStatus.OVERDUE),
    ],
)
def test_status_for(today: dt.date, expected: TenantStatus) -> None:
    assert status_for(next_due=dt.date(2026, 1, 15), today=today) is expected


def test_archived_tenant_is_inactive() -> None:
    assert (
        status_for(next_due=dt.date(2026, 1, 15), today=TODAY, is_active=False)
        is TenantStatus.INACTIVE
    )


def test_covered_state_has_no_outstanding() -> None:
    state = make_state(next_due=dt.date(2026, 2, 10))
    assert state.status is TenantStatus.COVERED
    assert state.outstanding == Decimal("0.00")
    assert state.remaining_for_period == RENT


def test_due_today_state_owes_full_rent() -> None:
    state = make_state()
    assert state.status is TenantStatus.DUE_TODAY
    assert state.outstanding == RENT


def test_overdue_state_accumulates_arrears() -> None:
    state = make_state(today=dt.date(2026, 3, 20), next_due=dt.date(2026, 1, 15))
    assert state.status is TenantStatus.OVERDUE
    assert state.arrears_months == 2
    assert state.outstanding == RENT * 3  # January + February + March periods


def test_credit_reduces_amount_due() -> None:
    state = make_state(credit=Decimal(4000))
    assert state.outstanding == Decimal("6000.00")
    assert state.remaining_for_period == Decimal("6000.00")


def test_arrears_months_helper() -> None:
    assert arrears_months(dt.date(2026, 1, 15), dt.date(2026, 1, 20)) == 0
    assert arrears_months(dt.date(2026, 1, 15), dt.date(2026, 2, 15)) == 1
    assert arrears_months(dt.date(2026, 1, 15), dt.date(2026, 2, 14)) == 0
    assert arrears_months(dt.date(2026, 1, 15), dt.date(2026, 1, 1)) == 0


# ---------------------------------------------------------------------------
# payments
# ---------------------------------------------------------------------------
def test_full_payment_rolls_cycle_from_payment_date() -> None:
    plan = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 12, 15),
        next_due=dt.date(2026, 1, 15),
        credit=Decimal(0),
        amount=RENT,
        payment_date=TODAY,
    )
    assert plan.payment_type is PaymentType.FULL
    assert plan.closes_period is True
    assert plan.new_cycle_start == TODAY
    assert plan.new_next_due == dt.date(2026, 2, 15)
    assert plan.new_credit == Decimal("0.00")
    assert plan.remaining_after == RENT
    assert len(plan.allocations) == 1
    assert plan.allocations[0].amount == RENT


def test_full_payment_on_month_end_clamps() -> None:
    plan = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 12, 31),
        next_due=dt.date(2026, 1, 31),
        credit=Decimal(0),
        amount=RENT,
        payment_date=dt.date(2026, 1, 31),
    )
    assert plan.new_next_due == dt.date(2026, 2, 28)


def test_partial_payment_keeps_due_date_and_balance() -> None:
    plan = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 12, 15),
        next_due=dt.date(2026, 1, 15),
        credit=Decimal(0),
        amount=Decimal(4000),
        payment_date=TODAY,
    )
    assert plan.payment_type is PaymentType.PARTIAL
    assert plan.closes_period is False
    assert plan.new_next_due == dt.date(2026, 1, 15)
    assert plan.new_cycle_start == dt.date(2025, 12, 15)
    assert plan.new_credit == Decimal("4000.00")
    assert plan.remaining_after == Decimal("6000.00")
    assert plan.allocations == []


def test_multiple_partial_payments_then_completion() -> None:
    first = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 12, 15),
        next_due=dt.date(2026, 1, 15),
        credit=Decimal(0),
        amount=Decimal(3000),
        payment_date=dt.date(2026, 1, 10),
    )
    assert first.payment_type is PaymentType.PARTIAL

    second = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=first.new_cycle_start,
        next_due=first.new_next_due,
        credit=first.new_credit,
        amount=Decimal(7000),
        payment_date=dt.date(2026, 1, 20),
    )
    assert second.payment_type is PaymentType.FULL
    assert second.closes_period is True
    # the completing payment date becomes the new cycle start
    assert second.new_cycle_start == dt.date(2026, 1, 20)
    assert second.new_next_due == dt.date(2026, 2, 20)
    assert second.new_credit == Decimal("0.00")


def test_overpayment_keeps_extra_as_credit() -> None:
    plan = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 12, 15),
        next_due=dt.date(2026, 1, 15),
        credit=Decimal(0),
        amount=Decimal(12000),
        payment_date=TODAY,
    )
    assert plan.payment_type is PaymentType.OVERPAYMENT
    # 10,000 closes the period, 2,000 stays as credit (never discarded)
    assert plan.new_credit == Decimal("2000.00")
    assert plan.new_next_due == dt.date(2026, 2, 15)
    assert plan.remaining_after == Decimal("8000.00")
    assert sum(a.amount for a in plan.allocations) == RENT


def test_large_overpayment_funds_following_months() -> None:
    plan = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 12, 15),
        next_due=dt.date(2026, 1, 15),
        credit=Decimal(0),
        amount=Decimal(30000),
        payment_date=TODAY,
    )
    assert plan.periods_funded == 3
    assert plan.new_next_due == dt.date(2026, 4, 15)
    assert plan.new_credit == Decimal("0.00")
    assert len(plan.allocations) == 3


def test_payment_clears_arrears() -> None:
    # due on 2025-11-15, paying on 2026-01-15: two periods behind + current
    plan = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 10, 15),
        next_due=dt.date(2025, 11, 15),
        credit=Decimal(0),
        amount=Decimal(30000),
        payment_date=TODAY,
    )
    assert plan.required_before == RENT * 3
    assert plan.closes_period is True
    assert plan.new_next_due == dt.date(2026, 2, 15)


def test_arrears_partial_payment_does_not_close() -> None:
    plan = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 10, 15),
        next_due=dt.date(2025, 11, 15),
        credit=Decimal(0),
        amount=Decimal(15000),
        payment_date=TODAY,
    )
    assert plan.payment_type is PaymentType.PARTIAL
    assert plan.new_next_due == dt.date(2025, 11, 15)
    assert plan.remaining_after == Decimal("15000.00")


def test_payment_amount_must_be_positive() -> None:
    with pytest.raises(ValueError):
        plan_payment(
            monthly_rent=RENT,
            currency="MRU",
            cycle_start=dt.date(2025, 12, 15),
            next_due=dt.date(2026, 1, 15),
            credit=Decimal(0),
            amount=Decimal(0),
            payment_date=TODAY,
        )


def test_state_after_full_payment_is_covered() -> None:
    plan = plan_payment(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=dt.date(2025, 12, 15),
        next_due=dt.date(2026, 1, 15),
        credit=Decimal(0),
        amount=RENT,
        payment_date=TODAY,
    )
    state = compute_state(
        monthly_rent=RENT,
        currency="MRU",
        cycle_start=plan.new_cycle_start,
        next_due=plan.new_next_due,
        credit=plan.new_credit,
        is_active=True,
        today=TODAY,
    )
    assert state.status is TenantStatus.COVERED
    assert state.outstanding == Decimal("0.00")
