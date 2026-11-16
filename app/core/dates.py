"""Calendar-month date arithmetic and Arabic-friendly input parsing.

Month-end policy (documented in README):
    The next due date is the same day-of-month as the anchor date, clamped to
    the last day of the target month when that day does not exist.

    Jan 31 -> Feb 28 (non leap year)
    Jan 31 -> Feb 29 (leap year)
    Mar 31 -> Apr 30
    May 30 -> Jun 30

A calendar month is never represented as a fixed 30-day span.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_digits(text: str) -> str:
    """Convert Arabic-Indic / Persian digits to ASCII digits."""
    return (text or "").translate(_ARABIC_DIGITS)


def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def add_months(value: dt.date, months: int) -> dt.date:
    """Add ``months`` calendar months, clamping the day to month end."""
    total = (value.year * 12 + (value.month - 1)) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(value.day, last_day_of_month(year, month))
    return dt.date(year, month, day)


def months_between(start: dt.date, end: dt.date) -> int:
    """Number of *complete* calendar months between two dates (0 when end <= start)."""
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def next_due_from(start: dt.date) -> dt.date:
    return add_months(start, 1)


def today_in(tz: ZoneInfo | None = None) -> dt.date:
    if tz is None:
        return dt.date.today()
    return dt.datetime.now(tz).date()


def now_in(tz: ZoneInfo) -> dt.datetime:
    return dt.datetime.now(tz)


def day_start(day: dt.date, tz: ZoneInfo) -> dt.datetime:
    """Timezone-aware instant at the start of ``day``."""
    return dt.datetime(day.year, day.month, day.day, tzinfo=tz)


_DATE_PATTERNS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d")


def parse_date(text: str | None) -> dt.date | None:
    """Parse a date typed by the owner. Accepts Arabic-Indic digits."""
    if not text:
        return None
    raw = normalize_digits(text).strip().replace(" ", "")
    for fmt in _DATE_PATTERNS:
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


_SPACE_RE = re.compile(r"[\s\u066c\u060c]")
_THOUSANDS_RE = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")


def parse_amount(text: str | None) -> Decimal | None:
    """Parse a money amount typed by the owner.

    Accepts ``1500``, ``1,500``, ``1,500.75``, ``1500.75``, ``1500,75`` and
    Arabic-Indic digits. Returns a Decimal quantized to 2 places, or ``None``
    when unparsable or not positive.
    """
    if text is None:
        return None
    raw = normalize_digits(text)
    raw = _SPACE_RE.sub("", raw)
    if not raw:
        return None
    if "," in raw:
        if _THOUSANDS_RE.match(raw):
            raw = raw.replace(",", "")
        elif raw.count(",") == 1 and "." not in raw:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    if not _AMOUNT_RE.match(raw):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:  # pragma: no cover - defensive
        return None
    if value <= 0:
        return None
    return value.quantize(Decimal("0.01"))


def month_bounds(day: dt.date) -> tuple[dt.date, dt.date]:
    """First day of ``day``'s month and the first day of the following month."""
    first = day.replace(day=1)
    return first, add_months(first, 1)
