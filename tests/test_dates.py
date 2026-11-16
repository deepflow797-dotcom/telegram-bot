"""Calendar-month arithmetic and input parsing."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.core.dates import (
    add_months,
    month_bounds,
    months_between,
    normalize_digits,
    parse_amount,
    parse_date,
)


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (dt.date(2026, 1, 15), dt.date(2026, 2, 15)),  # January 15 -> February 15
        (dt.date(2023, 1, 31), dt.date(2023, 2, 28)),  # January 31 -> February 28
        (dt.date(2024, 1, 31), dt.date(2024, 2, 29)),  # leap year
        (dt.date(2026, 3, 31), dt.date(2026, 4, 30)),  # March 31 -> April 30
        (dt.date(2026, 5, 30), dt.date(2026, 6, 30)),  # May 30 -> June 30
        (dt.date(2026, 12, 31), dt.date(2027, 1, 31)),  # year rollover
        (dt.date(2026, 1, 31), dt.date(2026, 3, 31)),  # two months from month end
    ],
)
def test_add_months(start: dt.date, expected: dt.date) -> None:
    assert add_months(start, 1) == expected or add_months(start, 2) == expected


def test_add_months_is_calendar_based_not_30_days() -> None:
    assert add_months(dt.date(2026, 1, 15), 1) == dt.date(2026, 2, 15)
    assert add_months(dt.date(2026, 2, 15), 1) == dt.date(2026, 3, 15)
    # 28-day February must not shift the day of month
    assert add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)


def test_add_months_backwards() -> None:
    assert add_months(dt.date(2026, 3, 31), -1) == dt.date(2026, 2, 28)
    assert add_months(dt.date(2026, 1, 1), -1) == dt.date(2025, 12, 1)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (dt.date(2026, 1, 15), dt.date(2026, 1, 15), 0),
        (dt.date(2026, 1, 15), dt.date(2026, 1, 20), 0),
        (dt.date(2026, 1, 15), dt.date(2026, 2, 15), 1),
        (dt.date(2026, 1, 15), dt.date(2026, 2, 14), 0),
        (dt.date(2026, 1, 1), dt.date(2026, 4, 1), 3),
        (dt.date(2026, 4, 1), dt.date(2026, 1, 1), 0),
    ],
)
def test_months_between(start: dt.date, end: dt.date, expected: int) -> None:
    assert months_between(start, end) == expected


def test_month_bounds() -> None:
    assert month_bounds(dt.date(2026, 2, 17)) == (dt.date(2026, 2, 1), dt.date(2026, 3, 1))


def test_parse_date_formats() -> None:
    assert parse_date("2026-01-15") == dt.date(2026, 1, 15)
    assert parse_date("15/01/2026") == dt.date(2026, 1, 15)
    assert parse_date("15-01-2026") == dt.date(2026, 1, 15)
    assert parse_date("١٥/٠١/٢٠٢٦") == dt.date(2026, 1, 15)
    assert parse_date("not a date") is None
    assert parse_date("2026-13-40") is None
    assert parse_date(None) is None


def test_parse_amount() -> None:
    assert parse_amount("1500") == Decimal("1500.00")
    assert parse_amount("1,500") == Decimal("1500.00")
    assert parse_amount("1500.50") == Decimal("1500.50")
    assert parse_amount("1500,50") == Decimal("1500.50")
    assert parse_amount("٣٥٠٠") == Decimal("3500.00")
    assert parse_amount(" 2 000 ") == Decimal("2000.00")
    assert parse_amount("0") is None
    assert parse_amount("-5") is None
    assert parse_amount("abc") is None
    assert parse_amount("") is None


def test_normalize_digits() -> None:
    assert normalize_digits("١٢٣٤٥٦٧٨٩٠") == "1234567890"
    assert normalize_digits("۴۵۶") == "456"
