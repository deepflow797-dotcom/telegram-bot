"""Permanent delete for buildings / apartments / tenants, and its guards."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.db import payments as payments_repo
from app.db import repository as repo
from app.db import tenants as tenants_repo
from tests.test_web import WEBHOOK_HEADERS, _texts_to, callback_update

TZ = dt.UTC


async def seed(db, *, rent: str = "10000") -> dict:
    building = await repo.create_building(db, name="مبنى النور")
    apartment = await repo.create_apartment(db, building_id=building["id"], apartment_number="3")
    tenant = await tenants_repo.create_tenant(
        db,
        full_name="محمد أحمد",
        phone="22334455",
        apartment_id=apartment["id"],
        monthly_rent=Decimal(rent),
        currency="MRU",
        rental_start_date=dt.date(2026, 1, 15),
    )
    return {"building": building, "apartment": apartment, "tenant": tenant}


async def pay(db, tenant: dict) -> None:
    pending = await payments_repo.create_pending_payment(
        db, tenant_id=tenant["id"], amount=Decimal(10000), created_by=1
    )
    await payments_repo.register_payment(
        db, pending_id=pending["id"], registered_by=1, now=dt.datetime(2026, 1, 15, 9, 0, tzinfo=TZ)
    )


async def test_building_with_apartments_is_not_deleted(db) -> None:
    data = await seed(db)
    counts = await repo.building_dependency_counts(db, data["building"]["id"])
    assert counts == {"apartments": 1, "tenants": 1}
    assert await repo.delete_building(db, data["building"]["id"]) is None
    assert await repo.get_building(db, data["building"]["id"]) is not None


async def test_empty_building_is_deleted(db) -> None:
    building = await repo.create_building(db, name="مبنى فارغ")
    deleted = await repo.delete_building(db, building["id"])
    assert deleted is not None and deleted["id"] == building["id"]
    assert await repo.get_building(db, building["id"]) is None


async def test_apartment_with_tenant_is_not_deleted(db) -> None:
    data = await seed(db)
    counts = await repo.apartment_dependency_counts(db, data["apartment"]["id"])
    assert counts["tenants"] == 1
    assert await repo.delete_apartment(db, data["apartment"]["id"]) is None


async def test_tenant_with_payments_is_not_deleted(db) -> None:
    data = await seed(db)
    await pay(db, data["tenant"])
    counts = await tenants_repo.tenant_dependency_counts(db, data["tenant"]["id"])
    assert counts["payments"] == 1
    assert await tenants_repo.delete_tenant(db, data["tenant"]["id"]) is None
    assert await tenants_repo.get_tenant(db, data["tenant"]["id"]) is not None


async def test_delete_tenant_without_payments_frees_the_apartment(db) -> None:
    data = await seed(db)
    deleted = await tenants_repo.delete_tenant(db, data["tenant"]["id"])
    assert deleted is not None

    apartment = await repo.get_apartment(db, data["apartment"]["id"])
    assert apartment is not None
    assert apartment["occupancy_status"] == "vacant"
    assert apartment.get("tenant_id") is None
    # now the apartment can be deleted, and then the building
    assert await repo.delete_apartment(db, apartment["id"]) is not None
    assert await repo.delete_building(db, data["building"]["id"]) is not None


async def test_owner_sees_delete_buttons_on_cards(web, db) -> None:
    data = await seed(db)
    _, client, session = web

    for target, expected in (
        (f"b:view:{data['building']['id']}", f"d:ask:b:{data['building']['id']}"),
        (f"a:view:{data['apartment']['id']}", f"d:ask:a:{data['apartment']['id']}"),
        (f"t:view:{data['tenant']['id']}", f"d:ask:t:{data['tenant']['id']}"),
    ):
        response = await client.post(
            "/api/telegram/webhook",
            json=callback_update(target, update_id=hash(target) % 100000),
            headers=WEBHOOK_HEADERS,
        )
        assert response.status_code == 200
        markup = str(session.calls[-1][1])
        assert expected in markup


async def test_delete_flow_deletes_and_blocks(web, db) -> None:
    data = await seed(db)
    _, client, session = web
    building_id = data["building"]["id"]
    apartment_id = data["apartment"]["id"]
    tenant_id = data["tenant"]["id"]

    # ask on a tenant that has no payments -> confirmation screen
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ask:t:{tenant_id}", update_id=301),
        headers=WEBHOOK_HEADERS,
    )
    assert any("حذف المستأجر" in text for text in _texts_to(session, 111222333))

    # confirm -> tenant gone
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ok:t:{tenant_id}", update_id=302),
        headers=WEBHOOK_HEADERS,
    )
    assert await tenants_repo.get_tenant(db, tenant_id) is None

    # apartment then building can follow
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ok:a:{apartment_id}", update_id=303),
        headers=WEBHOOK_HEADERS,
    )
    assert await repo.get_apartment(db, apartment_id) is None

    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ok:b:{building_id}", update_id=304),
        headers=WEBHOOK_HEADERS,
    )
    assert await repo.get_building(db, building_id) is None


async def test_delete_refused_when_history_exists(web, db) -> None:
    data = await seed(db)
    await pay(db, data["tenant"])
    building_id = data["building"]["id"]
    apartment_id = data["apartment"]["id"]
    tenant_id = data["tenant"]["id"]
    _, client, session = web

    # tenant: has a payment -> confirmation screen is a refusal, nothing deleted
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ask:t:{tenant_id}", update_id=401),
        headers=WEBHOOK_HEADERS,
    )
    assert any("دفعة مسجلة" in text for text in _texts_to(session, 111222333))

    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ok:t:{tenant_id}", update_id=402),
        headers=WEBHOOK_HEADERS,
    )
    assert await tenants_repo.get_tenant(db, tenant_id) is not None

    # force the callbacks anyway: the DB layer must refuse too
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ok:a:{apartment_id}", update_id=403),
        headers=WEBHOOK_HEADERS,
    )
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ok:b:{building_id}", update_id=404),
        headers=WEBHOOK_HEADERS,
    )
    assert await repo.get_apartment(db, apartment_id) is not None
    assert await repo.get_building(db, building_id) is not None
