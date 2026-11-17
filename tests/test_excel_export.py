"""Excel export: content, per-account isolation and the Telegram handler."""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal

import openpyxl

from app.db import payments as payments_repo
from app.db import platform
from app.db import repository as repo
from app.db import tenants as tenants_repo
from app.db.context import use_account
from app.services.excel_export import build_export

TZ = dt.UTC
TODAY = dt.date(2026, 1, 20)


async def seed(db, *, building="مبنى النور", tenant="محمد أحمد", rent="10000") -> dict:
    b = await repo.create_building(db, name=building, address="شارع 1")
    apartment = await repo.create_apartment(db, building_id=b["id"], apartment_number="3")
    row = await tenants_repo.create_tenant(
        db,
        full_name=tenant,
        phone="22334455",
        apartment_id=apartment["id"],
        monthly_rent=Decimal(rent),
        currency="MRU",
        rental_start_date=dt.date(2026, 1, 15),
    )
    return {"building": b, "apartment": apartment, "tenant": row}


async def add_payment(db, tenant: dict, amount: str) -> None:
    pending = await payments_repo.create_pending_payment(
        db, tenant_id=tenant["id"], amount=Decimal(amount), created_by=1
    )
    await payments_repo.register_payment(
        db,
        pending_id=pending["id"],
        registered_by=1,
        now=dt.datetime(2026, 1, 15, 9, 0, tzinfo=TZ),
    )


async def test_export_contains_every_sheet_and_the_real_numbers(db) -> None:
    data = await seed(db)
    await add_payment(db, data["tenant"], "10000")

    payload, filename = await build_export(db, tz=TZ, today=TODAY, currency="MRU")
    assert filename == "rentals-2026-01-20.xlsx"
    assert payload[:2] == b"PK"

    book = openpyxl.load_workbook(io.BytesIO(payload))
    assert book.sheetnames == [
        "ملخص",
        "المباني",
        "الشقق",
        "المستأجرون",
        "الدفعات",
        "المتأخرات",
    ]

    summary = dict(book["ملخص"].iter_rows(min_row=2, values_only=True))
    assert summary["عدد المباني"] == 1
    assert summary["عدد الشقق"] == 1
    assert summary["الشقق المشغولة"] == 1
    assert summary["المستأجرون النشطون"] == 1
    assert float(summary["إجمالي المبالغ المستلمة"]) == 10000
    assert summary["العملة الافتراضية"] == "MRU"

    building_rows = list(book["المباني"].iter_rows(min_row=2, values_only=True))
    assert building_rows[0][0] == "مبنى النور"
    assert building_rows[0][1] == "شارع 1"

    tenant_rows = list(book["المستأجرون"].iter_rows(min_row=2, values_only=True))
    assert tenant_rows[0][0] == "محمد أحمد"
    assert tenant_rows[0][1] == "22334455"
    assert tenant_rows[0][2] == "مبنى النور"
    assert tenant_rows[0][3] == "3"
    assert float(tenant_rows[0][4]) == 10000

    payments = list(book["الدفعات"].iter_rows(min_row=2, values_only=True))
    assert len(payments) == 1
    assert payments[0][1] == "محمد أحمد"
    assert float(payments[0][4]) == 10000


async def test_export_is_isolated_per_account(db) -> None:
    """A customer's export never contains the owner's rows (and vice versa)."""
    owner_data = await seed(db, building="مبنى المالك", tenant="مستأجر المالك")

    customer = await platform.start_trial(
        db, telegram_id=777000111, display_name="عميل", username=None, days=30
    )
    customer_account = customer[0]

    with use_account(customer_account["id"]):
        empty_payload, _ = await build_export(db, tz=TZ, today=TODAY, currency="MRU")

    empty_book = openpyxl.load_workbook(io.BytesIO(empty_payload))
    assert list(empty_book["المباني"].iter_rows(min_row=2, values_only=True)) == []
    assert list(empty_book["المستأجرون"].iter_rows(min_row=2, values_only=True)) == []

    owner_payload, _ = await build_export(db, tz=TZ, today=TODAY, currency="MRU")
    owner_book = openpyxl.load_workbook(io.BytesIO(owner_payload))
    names = [row[0] for row in owner_book["المباني"].iter_rows(min_row=2, values_only=True)]
    assert names == [owner_data["building"]["name"]]


async def test_arrears_sheet_lists_overdue_tenants(db) -> None:
    data = await seed(db, rent="5000")
    payload, _ = await build_export(db, tz=TZ, today=TODAY, currency="MRU")
    book = openpyxl.load_workbook(io.BytesIO(payload))

    arrears = list(book["المتأخرات"].iter_rows(min_row=2, values_only=True))
    assert len(arrears) == 1
    assert arrears[0][0] == data["tenant"]["full_name"]
    assert float(arrears[0][6]) > 0
    assert arrears[0][7] == "MRU"


async def test_export_button_sends_a_document(web, db) -> None:
    """The reports menu button delivers a real .xlsx file through Telegram."""
    from tests.test_web import WEBHOOK_HEADERS, callback_update

    await seed(db)
    _, client, session = web

    response = await client.post(
        "/api/telegram/webhook",
        json=callback_update("r:xlsx", update_id=501),
        headers=WEBHOOK_HEADERS,
    )
    assert response.status_code == 200

    documents = [call for call in session.calls if call[0] == "SendDocument"]
    assert len(documents) == 1
    payload = documents[0][1]
    document = payload["document"]
    assert document.filename.endswith(".xlsx")
    assert "Excel" in payload["caption"]

    # the delivered bytes are a workbook with the account's sheets
    book = openpyxl.load_workbook(io.BytesIO(document.data))
    assert "المستأجرون" in book.sheetnames
