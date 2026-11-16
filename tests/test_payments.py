"""Payment registration: atomicity, idempotency, partial payments, history."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.core.dates import add_months
from app.db import payments as payments_repo
from app.db import repository as repo
from app.db import tenants as tenants_repo


async def setup_tenant(db, *, rent="10000", start=dt.date(2026, 1, 15)):
    building = await repo.create_building(db, name="مبنى النور")
    apartment = await repo.create_apartment(
        db, building_id=building["id"], apartment_number="1"
    )
    tenant = await tenants_repo.create_tenant(
        db,
        full_name="محمد أحمد",
        apartment_id=apartment["id"],
        monthly_rent=Decimal(rent),
        currency="MRU",
        rental_start_date=start,
    )
    return tenant


async def pay(db, tenant, amount, *, when=dt.datetime(2026, 1, 15, 9, 30), owner=1):
    pending = await payments_repo.create_pending_payment(
        db, tenant_id=tenant["id"], amount=Decimal(amount), created_by=owner
    )
    return await payments_repo.register_payment(
        db, pending_id=pending["id"], registered_by=owner, now=when
    )


async def test_full_payment_registration(db) -> None:
    tenant = await setup_tenant(db)
    result = await pay(db, tenant, "10000")
    assert result["status"] == "ok"
    payment = result["payment"]
    assert payment["payment_type"] == "full"
    assert payment["amount"] == Decimal("10000.00")
    assert payment["idempotency_key"].startswith("pending:")

    refreshed = await tenants_repo.get_tenant(db, tenant["id"])
    assert refreshed["next_due_date"] == dt.date(2026, 2, 15)
    assert refreshed["current_cycle_start"] == dt.date(2026, 1, 15)
    assert refreshed["credit_amount"] == Decimal("0.00")

    allocations = await db.fetch_all(
        "SELECT * FROM payment_allocations WHERE tenant_id = %s", (tenant["id"],)
    )
    assert len(allocations) == 1
    assert allocations[0]["period_start"] == dt.date(2026, 1, 15)
    assert allocations[0]["period_end"] == dt.date(2026, 2, 15)


async def test_duplicate_confirmation_registers_once(db) -> None:
    tenant = await setup_tenant(db)
    pending = await payments_repo.create_pending_payment(
        db, tenant_id=tenant["id"], amount=Decimal("10000"), created_by=1
    )
    first = await payments_repo.register_payment(
        db, pending_id=pending["id"], registered_by=1, now=dt.datetime(2026, 1, 15, 10, 0)
    )
    second = await payments_repo.register_payment(
        db, pending_id=pending["id"], registered_by=1, now=dt.datetime(2026, 1, 15, 10, 1)
    )
    assert first["status"] == "ok"
    assert second["status"] == "duplicate"
    count = await db.fetch_value(
        "SELECT count(*) FROM payments WHERE tenant_id = %s", (tenant["id"],)
    )
    assert count == 1
    refreshed = await tenants_repo.get_tenant(db, tenant["id"])
    assert refreshed["next_due_date"] == dt.date(2026, 2, 15)


async def test_partial_payments_then_completion(db) -> None:
    tenant = await setup_tenant(db)
    first = await pay(db, tenant, "4000", when=dt.datetime(2026, 1, 15, 9, 0))
    assert first["payment"]["payment_type"] == "partial"
    refreshed = await tenants_repo.get_tenant(db, tenant["id"])
    assert refreshed["next_due_date"] == dt.date(2026, 1, 15)
    assert refreshed["credit_amount"] == Decimal("4000.00")

    second = await pay(db, tenant, "6000", when=dt.datetime(2026, 1, 20, 9, 0))
    assert second["payment"]["payment_type"] == "full"
    refreshed = await tenants_repo.get_tenant(db, tenant["id"])
    assert refreshed["next_due_date"] == dt.date(2026, 2, 20)
    assert refreshed["current_cycle_start"] == dt.date(2026, 1, 20)
    assert refreshed["credit_amount"] == Decimal("0.00")

    payments = await payments_repo.list_payments(db, tenant_id=tenant["id"])
    assert len(payments) == 2
    assert sum(p["amount"] for p in payments) == Decimal("10000.00")


async def test_overpayment_keeps_credit(db) -> None:
    tenant = await setup_tenant(db)
    result = await pay(db, tenant, "12000")
    assert result["payment"]["payment_type"] == "overpayment"
    assert result["payment"]["amount"] == Decimal("12000.00")
    refreshed = await tenants_repo.get_tenant(db, tenant["id"])
    assert refreshed["credit_amount"] == Decimal("2000.00")
    assert refreshed["next_due_date"] == dt.date(2026, 2, 15)


async def test_payment_for_archived_tenant_is_rejected(db) -> None:
    tenant = await setup_tenant(db)
    await tenants_repo.archive_tenant(db, tenant["id"])
    pending = await payments_repo.create_pending_payment(
        db, tenant_id=tenant["id"], amount=Decimal("10000"), created_by=1
    )
    result = await payments_repo.register_payment(
        db, pending_id=pending["id"], registered_by=1, now=dt.datetime(2026, 1, 15)
    )
    assert result["status"] == "tenant_inactive"
    assert await db.fetch_value("SELECT count(*) FROM payments") == 0


async def test_unknown_pending_payment(db) -> None:
    result = await payments_repo.register_payment(
        db, pending_id=uuid.uuid4(), registered_by=1, now=dt.datetime(2026, 1, 15)
    )
    assert result["status"] == "pending_not_found"


async def test_failed_registration_leaves_no_partial_state(db) -> None:
    tenant = await setup_tenant(db)
    pending = await payments_repo.create_pending_payment(
        db, tenant_id=tenant["id"], amount=Decimal("5000"), created_by=1
    )
    # make the tenant disappear to force a failure after the pending row exists
    with pytest.raises(Exception):
        async with db.transaction() as conn:
            await conn.execute("DELETE FROM tenants WHERE id = %s", (tenant["id"],))
            raise RuntimeError("boom")
    assert await db.fetch_value("SELECT count(*) FROM tenants") == 1
    result = await payments_repo.register_payment(
        db, pending_id=pending["id"], registered_by=1, now=dt.datetime(2026, 1, 15)
    )
    assert result["status"] == "ok"


async def test_payment_history_survives_archive(db) -> None:
    tenant = await setup_tenant(db)
    await pay(db, tenant, "10000")
    await tenants_repo.archive_tenant(db, tenant["id"])
    payments = await payments_repo.list_payments(db, tenant_id=tenant["id"])
    assert len(payments) == 1
    assert payments[0]["full_name"] == "محمد أحمد"
    stats = await tenants_repo.payment_stats(db, tenant["id"])
    assert stats["total_paid"] == Decimal("10000.00")


async def test_payment_totals_by_currency(db) -> None:
    tenant = await setup_tenant(db)
    await pay(db, tenant, "10000", when=dt.datetime(2026, 1, 15, 8, 0))
    await pay(db, tenant, "5000", when=dt.datetime(2026, 2, 15, 8, 0))
    january = await payments_repo.totals_by_currency(
        db,
        start=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        end=dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc),
    )
    assert january["MRU"][0] == Decimal("10000.00")
    assert january["MRU"][1] == 1


async def test_cycle_renewal_after_arrears(db) -> None:
    tenant = await setup_tenant(db)
    # due 2026-01-15; pay on 2026-03-20 covering three periods
    result = await pay(db, tenant, "30000", when=dt.datetime(2026, 3, 20, 12, 0))
    assert result["status"] == "ok"
    assert result["plan"].required_before == Decimal("30000.00")
    refreshed = await tenants_repo.get_tenant(db, tenant["id"])
    assert refreshed["next_due_date"] == dt.date(2026, 4, 20)


async def test_month_end_renewal(db) -> None:
    tenant = await setup_tenant(db, start=dt.date(2026, 1, 31))
    result = await pay(db, tenant, "10000", when=dt.datetime(2026, 1, 31, 12, 0))
    assert result["status"] == "ok"
    assert result["payment"]["payment_type"] == "full"
    refreshed = await tenants_repo.get_tenant(db, tenant["id"])
    assert refreshed["next_due_date"] == add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)


async def test_payment_amount_validation_in_db(db) -> None:
    tenant = await setup_tenant(db)
    from psycopg import errors as pg_errors

    with pytest.raises(pg_errors.CheckViolation):
        await payments_repo.create_pending_payment(
            db, tenant_id=tenant["id"], amount=Decimal("0"), created_by=1
        )
