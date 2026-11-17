"""Multi-account platform: requests, approval, isolation and freezing."""

from __future__ import annotations

import datetime as dt

from app.db import platform
from app.db.context import use_account
from app.db.repository import create_building, list_buildings


async def test_pending_request_is_created_once(db) -> None:
    first, is_new = await platform.register_request(
        db, telegram_id=555000111, display_name="عميل", username="client_one"
    )
    assert is_new is True
    assert first["status"] == platform.STATUS_PENDING

    again, is_new_again = await platform.register_request(
        db, telegram_id=555000111, display_name="عميل", username="client_one"
    )
    assert is_new_again is False
    assert again["id"] == first["id"]

    rows = await platform.list_accounts(db, statuses=[platform.STATUS_PENDING])
    assert [row["id"] for row in rows] == [first["id"]]


async def test_approval_creates_an_empty_private_dataset(db) -> None:
    owner_building = await create_building(db, name="مبنى المالك")

    account, _ = await platform.register_request(
        db, telegram_id=555000222, display_name="عميل ثانٍ", username=None
    )
    approved = await platform.approve_account(db, account["id"], days=30)
    assert approved is not None
    assert approved["status"] == platform.STATUS_ACTIVE
    assert approved["paid_until"] is not None

    # the new account starts from scratch …
    with use_account(account["id"]):
        assert await list_buildings(db) == []
        mine = await create_building(db, name="مبنى العميل")

    # … and the owner cannot see the customer's data (and vice versa)
    with use_account(account["id"]):
        rows = await list_buildings(db)
        assert [row["name"] for row in rows] == ["مبنى العميل"]
        assert owner_building["name"] not in [row["name"] for row in rows]

    with use_account(platform.OWNER_ACCOUNT_ID):
        owner_rows = await list_buildings(db)
        assert [row["name"] for row in owner_rows] == ["مبنى المالك"]
        assert mine["name"] not in [row["name"] for row in owner_rows]


async def test_expired_subscription_is_frozen_not_deleted(db) -> None:
    account, _ = await platform.register_request(
        db, telegram_id=555000333, display_name="عميل ثالث", username=None
    )
    await platform.approve_account(db, account["id"], days=30, today=dt.date(2026, 1, 1))
    stored = await platform.get_account(db, account["id"])

    assert platform.effective_status(stored, dt.date(2026, 1, 15)) == platform.STATUS_ACTIVE
    assert platform.effective_status(stored, dt.date(2026, 3, 15)) == platform.STATUS_FROZEN
    # the row (and therefore the data) is kept
    assert stored["status"] == platform.STATUS_ACTIVE
    assert await platform.get_account(db, account["id"]) is not None


async def test_contact_settings_are_editable(db) -> None:
    settings = await platform.get_platform_settings(db)
    assert settings["owner_phone"] == "22222222"

    await platform.set_platform_setting(db, "owner_phone", "33333333")
    await platform.set_platform_setting(db, "contact_note", "أرسل يوزرك")
    updated = await platform.get_platform_settings(db)
    assert updated["owner_phone"] == "33333333"
    assert updated["contact_note"] == "أرسل يوزرك"


async def test_owner_account_is_bound_to_the_owner_telegram_id(db) -> None:
    account = await platform.ensure_owner_account(db, 999111222)
    assert account is not None
    assert account["id"] == platform.OWNER_ACCOUNT_ID
    assert account["telegram_id"] == 999111222
    assert account["status"] == platform.STATUS_ACTIVE


async def test_free_trial_starts_active_and_freezes_on_expiry(db) -> None:
    account, is_new = await platform.start_trial(
        db,
        telegram_id=555444555,
        display_name="تجربة",
        username="trial_user",
        days=7,
        today=dt.date(2026, 1, 1),
    )
    assert is_new is True
    assert account["status"] == platform.STATUS_ACTIVE
    assert account["plan"] == platform.PLAN_TRIAL
    assert account["paid_until"] == dt.date(2026, 1, 8)
    assert platform.days_left(account, dt.date(2026, 1, 4)) == 4

    # the trial account owns an empty dataset like any other customer
    with use_account(account["id"]):
        assert await list_buildings(db) == []

    # once the trial is over the account freezes (data untouched)
    assert platform.effective_status(account, dt.date(2026, 1, 20)) == platform.STATUS_FROZEN
    assert await platform.get_account(db, account["id"]) is not None


async def test_starting_again_does_not_restart_the_trial(db) -> None:
    first, _ = await platform.start_trial(
        db, telegram_id=555666777, display_name="عميل", username=None, days=7
    )
    again, is_new = await platform.start_trial(
        db, telegram_id=555666777, display_name="عميل", username=None, days=7
    )
    assert is_new is False
    assert again["id"] == first["id"]
    assert again["paid_until"] == first["paid_until"]


async def test_trial_length_is_a_platform_setting(db) -> None:
    assert await platform.get_trial_days(db) == 7  # default

    await platform.set_trial_days(db, 21)
    assert await platform.get_trial_days(db) == 21

    account, _ = await platform.start_trial(
        db, telegram_id=555777888, display_name="21", username=None
    )
    assert platform.days_left(account, dt.date.today()) == 21

    await platform.set_trial_days(db, 0)  # trial disabled -> request flow
    assert await platform.get_trial_days(db) == 0
