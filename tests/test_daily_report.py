"""Daily report: content, totals, timezone, idempotency, retries."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.core.reports import render_daily_report
from app.db import payments as payments_repo
from app.db import reports as reports_repo
from app.db import repository as repo
from app.db import tenants as tenants_repo
from app.db.reports import claim_daily_report
from app.services.daily_report import run_daily_report
from app.services.reports_data import build_daily_report_for, tenant_states

TZ = ZoneInfo("Africa/Nouakchott")
REPORT_DATE = dt.date(2026, 1, 15)
NOW = dt.datetime(2026, 1, 15, 8, 0, tzinfo=TZ)


async def add_tenant(db, name, due, rent="10000", apartment="1"):
    building = await repo.create_building(db, name="مبنى النور")
    apartments = await repo.list_apartments(db, building_id=building["id"], active_only=True)
    if apartments:
        apartment_id = (
            await repo.create_apartment(
                db, building_id=building["id"], apartment_number=apartment
            )
        )["id"]
    else:
        apartment_id = (
            await repo.create_apartment(
                db, building_id=building["id"], apartment_number=apartment
            )
        )["id"]
    return await tenants_repo.create_tenant(
        db,
        full_name=name,
        apartment_id=apartment_id,
        monthly_rent=Decimal(rent),
        currency="MRU",
        rental_start_date=due,
    )


async def pay_at(db, tenant, amount, when):
    pending = await payments_repo.create_pending_payment(
        db, tenant_id=tenant["id"], amount=Decimal(amount), created_by=1
    )
    return await payments_repo.register_payment(db, pending_id=pending["id"], registered_by=1, now=when)


async def seed(db):
    building = await repo.create_building(db, name="مبنى النور")
    apartment_ids = []
    for number in ("1", "2", "3", "4"):
        apartment_ids.append(
            (
                await repo.create_apartment(
                    db, building_id=building["id"], apartment_number=number
                )
            )["id"]
        )

    async def tenant(name, apartment_id, rent, due):
        return await tenants_repo.create_tenant(
            db,
            full_name=name,
            apartment_id=apartment_id,
            monthly_rent=Decimal(rent),
            currency="MRU",
            rental_start_date=due,
        )

    overdue = await tenant("متأخر", apartment_ids[0], "10000", dt.date(2026, 1, 10))
    due_today = await tenant("مستحق اليوم", apartment_ids[1], "12000", dt.date(2026, 1, 15))
    upcoming = await tenant("قريب", apartment_ids[2], "8000", dt.date(2026, 1, 17))
    covered = await tenant("مدفوع", apartment_ids[3], "15000", dt.date(2026, 2, 1))
    return overdue, due_today, upcoming, covered


async def test_daily_report_counts_and_totals(db) -> None:
    overdue, due_today, upcoming, covered = await seed(db)
    # money collected today and earlier this month / last month
    await pay_at(db, covered, "15000", dt.datetime(2026, 1, 15, 9, 0, tzinfo=TZ))
    await pay_at(db, upcoming, "2000", dt.datetime(2026, 1, 5, 9, 0, tzinfo=TZ))
    await pay_at(db, overdue, "1000", dt.datetime(2025, 12, 20, 9, 0, tzinfo=TZ))

    report = await build_daily_report_for(
        db, tz=TZ, report_date=REPORT_DATE, now=NOW, upcoming_days=3
    )

    assert report.report_date == REPORT_DATE
    assert report.active_tenants == 4
    assert report.counts["overdue"] >= 1
    assert report.counts["due_today"] >= 0
    # today's collection is separate from the monthly figure
    assert report.collected_today.get("MRU") == Decimal("15000.00")
    assert report.payments_today_count == 1
    assert report.collected_month.get("MRU") == Decimal("17000.00")
    assert report.payments_month_count == 2
    assert report.expected_rent.get("MRU") == Decimal("45000.00")
    assert report.outstanding.get("MRU") > 0


async def test_daily_report_sections(db) -> None:
    await seed(db)
    states = await tenant_states(db, today=REPORT_DATE, upcoming_days=3)
    statuses = {row["full_name"]: state.status.value for row, state in states}
    assert statuses["متأخر"] == "overdue"
    assert statuses["مستحق اليوم"] == "due_today"
    assert statuses["قريب"] == "upcoming"
    assert statuses["مدفوع"] == "covered"

    report = await build_daily_report_for(
        db, tz=TZ, report_date=REPORT_DATE, now=NOW, upcoming_days=3
    )
    blocks = render_daily_report(report)
    text = "\n".join(block.text for block in blocks)
    assert "الملخص العام" in text
    assert "المتأخرون" in text
    assert "مستحق اليوم" in text or "المستحقون اليوم" in text
    assert "المحصل اليوم" in text and "المحصل هذا الشهر" in text
    for block in blocks:
        assert len(block.text) <= 4096


async def test_report_is_sent_once_per_date(db) -> None:
    await seed(db)
    sent: list[str] = []

    async def sender(text, keyboard=None):
        sent.append(text)

    first = await run_daily_report(
        db, tz=TZ, report_date=REPORT_DATE, now=NOW, send=sender, upcoming_days=3
    )
    assert first.status == "sent"
    assert first.messages_sent == len(sent) > 0

    second = await run_daily_report(
        db, tz=TZ, report_date=REPORT_DATE, now=NOW, send=sender, upcoming_days=3
    )
    assert second.status == "skipped"
    assert second.reason == "already_sent"
    assert len(sent) == first.messages_sent

    row = await reports_repo.get_report_for_date(db, REPORT_DATE)
    assert row["status"] == "sent"
    assert row["sent_at"] is not None
    assert row["summary"]["active_tenants"] == 4


async def test_report_failure_is_recorded_and_retried(db) -> None:
    await seed(db)

    async def broken_sender(text, keyboard=None):
        raise RuntimeError("telegram is down")

    failed = await run_daily_report(
        db, tz=TZ, report_date=REPORT_DATE, now=NOW, send=broken_sender, upcoming_days=3
    )
    assert failed.status == "failed"
    assert "telegram is down" in (failed.error or "")
    row = await reports_repo.get_report_for_date(db, REPORT_DATE)
    assert row["status"] == "failed"
    assert "telegram is down" in row["error_message"]

    sent: list[str] = []

    async def sender(text, keyboard=None):
        sent.append(text)

    retried = await run_daily_report(
        db, tz=TZ, report_date=REPORT_DATE, now=NOW, send=sender, upcoming_days=3
    )
    assert retried.status == "sent"
    assert retried.reason == "retry"
    assert await db.fetch_value("SELECT attempts FROM daily_reports") == 2


async def test_retry_limit(db) -> None:
    await seed(db)

    async def broken_sender(text, keyboard=None):
        raise RuntimeError("nope")

    for _ in range(3):
        await run_daily_report(
            db, tz=TZ, report_date=REPORT_DATE, now=NOW, send=broken_sender, upcoming_days=3
        )
    result = await run_daily_report(
        db, tz=TZ, report_date=REPORT_DATE, now=NOW, send=broken_sender, upcoming_days=3
    )
    assert result.status == "skipped"
    assert result.reason == "retry_limit"


async def test_report_does_not_modify_tenant_dates(db) -> None:
    overdue, *_ = await seed(db)
    before = await tenants_repo.get_tenant(db, overdue["id"])

    async def sender(text, keyboard=None):
        return None

    await run_daily_report(
        db, tz=TZ, report_date=REPORT_DATE, now=NOW, send=sender, upcoming_days=3
    )
    after = await tenants_repo.get_tenant(db, overdue["id"])
    assert before["next_due_date"] == after["next_due_date"]
    assert before["current_cycle_start"] == after["current_cycle_start"]


async def test_claim_reports_in_progress(db) -> None:
    first = await claim_daily_report(db, REPORT_DATE)
    assert first["claimed"] is True
    second = await claim_daily_report(db, REPORT_DATE)
    assert second["claimed"] is False
    assert second["reason"] == "in_progress"


async def test_report_date_uses_configured_timezone(db) -> None:
    report = await build_daily_report_for(
        db, tz=TZ, report_date=REPORT_DATE, now=NOW, upcoming_days=3
    )
    assert report.generated_at.tzinfo is not None
    assert report.report_date == NOW.date()


def test_utc_offset_of_nouakchott() -> None:
    assert NOW.utcoffset() == dt.timedelta(0)  # Mauritania is UTC+0 all year
