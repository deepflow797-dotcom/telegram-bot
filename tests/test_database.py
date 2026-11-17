"""Database layer: properties, tenants, single-active-tenant rule, archiving."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from psycopg import errors as pg_errors

from app.db import repository as repo
from app.db import tenants as tenants_repo
from app.services.reports_data import state_from_row


async def make_property(db):
    building = await repo.create_building(db, name="مبنى النور", address="نواكشوط")
    apartment = await repo.create_apartment(
        db, building_id=building["id"], apartment_number="1", floor="0"
    )
    return building, apartment


async def make_tenant(db, apartment_id: int, **overrides):
    params = dict(
        full_name="محمد أحمد",
        phone="22212345678",
        apartment_id=apartment_id,
        monthly_rent=Decimal(10000),
        currency="MRU",
        rental_start_date=dt.date(2026, 1, 15),
    )
    params.update(overrides)
    return await tenants_repo.create_tenant(db, **params)


async def test_create_building_and_apartment(db) -> None:
    building, apartment = await make_property(db)
    assert building["is_active"] is True
    fetched = await repo.get_building(db, building["id"])
    assert fetched["apartments_count"] == 1
    assert fetched["occupied_count"] == 0
    assert apartment["occupancy_status"] == "vacant"
    assert apartment["building_name"] == "مبنى النور"


async def test_duplicate_apartment_number_in_same_building(db) -> None:
    building, _ = await make_property(db)
    with pytest.raises(pg_errors.UniqueViolation):
        await repo.create_apartment(db, building_id=building["id"], apartment_number="1")


async def test_same_number_in_different_buildings_allowed(db) -> None:
    first, _ = await make_property(db)
    second = await repo.create_building(db, name="مبنى آخر")
    apartment = await repo.create_apartment(
        db, building_id=second["id"], apartment_number="1"
    )
    assert apartment["building_id"] == second["id"]


async def test_tenant_creation_sets_first_due_on_start_date(db) -> None:
    _, apartment = await make_property(db)
    tenant = await make_tenant(db, apartment["id"])
    assert tenant["next_due_date"] == dt.date(2026, 1, 15)
    assert tenant["current_cycle_start"] == dt.date(2025, 12, 15)
    assert tenant["credit_amount"] == Decimal("0.00")
    updated_apartment = await repo.get_apartment(db, apartment["id"])
    assert updated_apartment["occupancy_status"] == "occupied"
    assert updated_apartment["tenant_name"] == "محمد أحمد"


async def test_apartment_cannot_have_two_active_tenants(db) -> None:
    _, apartment = await make_property(db)
    await make_tenant(db, apartment["id"])
    with pytest.raises(pg_errors.UniqueViolation):
        await make_tenant(db, apartment["id"], full_name="مستأجر آخر")


async def test_archiving_tenant_frees_apartment_and_moves_out(db) -> None:
    _, apartment = await make_property(db)
    tenant = await make_tenant(db, apartment["id"])
    archived = await tenants_repo.archive_tenant(db, tenant["id"])
    assert archived["is_active"] is False
    assert archived["archived_at"] is not None
    apartment_after = await repo.get_apartment(db, apartment["id"])
    assert apartment_after["occupancy_status"] == "vacant"
    assert apartment_after["tenant_id"] is None
    # the apartment accepts a new tenant again
    await make_tenant(db, apartment["id"], full_name="مستأجر جديد")


async def test_archiving_is_idempotent(db) -> None:
    _, apartment = await make_property(db)
    tenant = await make_tenant(db, apartment["id"])
    assert await tenants_repo.archive_tenant(db, tenant["id"]) is not None
    assert await tenants_repo.archive_tenant(db, tenant["id"]) is None


async def test_change_tenant_apartment(db) -> None:
    building, apartment = await make_property(db)
    other = await repo.create_apartment(db, building_id=building["id"], apartment_number="2")
    tenant = await make_tenant(db, apartment["id"])
    moved = await tenants_repo.change_tenant_apartment(db, tenant["id"], other["id"])
    assert moved["apartment_id"] == other["id"]
    assert (await repo.get_apartment(db, apartment["id"]))["occupancy_status"] == "vacant"
    assert (await repo.get_apartment(db, other["id"]))["occupancy_status"] == "occupied"


async def test_change_to_occupied_apartment_rejected(db) -> None:
    building, apartment = await make_property(db)
    other = await repo.create_apartment(db, building_id=building["id"], apartment_number="2")
    await make_tenant(db, apartment["id"])
    second_tenant = await make_tenant(db, other["id"], full_name="آخر")
    with pytest.raises(ValueError):
        await tenants_repo.change_tenant_apartment(db, second_tenant["id"], apartment["id"])


async def test_update_tenant_preserves_cycle_dates(db) -> None:
    _, apartment = await make_property(db)
    tenant = await make_tenant(db, apartment["id"])
    updated = await tenants_repo.update_tenant(
        db, tenant["id"], monthly_rent=Decimal(12000), phone="22200000000", notes="ملاحظة"
    )
    # editing must never reset the rental timer
    assert updated["current_cycle_start"] == tenant["current_cycle_start"]
    assert updated["next_due_date"] == tenant["next_due_date"]
    assert updated["monthly_rent"] == Decimal("12000.00")
    assert updated["notes"] == "ملاحظة"


async def test_update_tenant_rejects_unknown_field(db) -> None:
    _, apartment = await make_property(db)
    tenant = await make_tenant(db, apartment["id"])
    with pytest.raises(ValueError):
        await tenants_repo.update_tenant(db, tenant["id"], next_due_date=dt.date(2030, 1, 1))


async def test_search_tenants(db) -> None:
    _, apartment = await make_property(db)
    await make_tenant(db, apartment["id"], full_name="فاطمة بنت أحمد")
    assert await tenants_repo.search_tenants(db, "فاطمة")
    assert await tenants_repo.search_tenants(db, "22212345678")
    assert await tenants_repo.search_tenants(db, "النور")
    assert await tenants_repo.search_tenants(db, "zzz") == []


async def test_archive_building_archives_apartments(db) -> None:
    building, _ = await make_property(db)
    await repo.archive_building(db, building["id"])
    apartments = await repo.list_apartments(db, building_id=building["id"], active_only=True)
    assert apartments == []
    assert await repo.building_active_tenants_count(db, building["id"]) == 0


async def test_settings_defaults_and_updates(db) -> None:
    settings = await repo.get_app_settings(db)
    assert settings["currency"] == "MRU"
    assert settings["report_time"] == "08:00"
    await repo.set_app_setting(db, "currency", "USD")
    assert (await repo.get_app_settings(db))["currency"] == "USD"


async def test_tenant_state_from_row(db) -> None:
    _, apartment = await make_property(db)
    tenant = await make_tenant(db, apartment["id"])
    state = state_from_row(tenant, today=dt.date(2026, 1, 13))
    assert state.status.value == "upcoming"
    assert state.required_to_close == Decimal("10000.00")


async def test_audit_log_written(db) -> None:
    await repo.log_audit(
        db, actor_id=1, action="test_action", entity_type="tenant", entity_id=5, details={"a": 1}
    )
    row = await db.fetch_one("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1")
    assert row["action"] == "test_action"
    assert row["details"] == {"a": 1}
