"""Per-account settings that switch permanent delete / archive on and off."""

from __future__ import annotations

from app.bot import policy as bot_policy
from app.db import repository as repo
from app.db import tenants as tenants_repo
from tests.test_deletes import seed
from tests.test_web import WEBHOOK_HEADERS, callback_update


async def test_policy_defaults_to_enabled() -> None:
    flags = bot_policy.from_settings({})
    assert flags == {bot_policy.ALLOW_DELETE: True, bot_policy.ALLOW_ARCHIVE: True}


async def test_policy_parses_false_values() -> None:
    flags = bot_policy.from_settings({"allow_delete": "false", "allow_archive": "0"})
    assert flags[bot_policy.ALLOW_DELETE] is False
    assert flags[bot_policy.ALLOW_ARCHIVE] is False


async def test_policy_menu_shows_current_state(web, db) -> None:
    _, client, session = web
    await client.post(
        "/api/telegram/webhook",
        json=callback_update("s:delpol", update_id=601),
        headers=WEBHOOK_HEADERS,
    )
    markup = str(session.calls[-1][1])
    assert "s:delpol:del" in markup and "s:delpol:arch" in markup
    assert "✅ مفعّل" in markup and "✅ مفعّلة" in markup


async def test_toggle_delete_off_hides_and_blocks(web, db) -> None:
    data = await seed(db)
    tenant_id = data["tenant"]["id"]
    _, client, session = web

    await client.post(
        "/api/telegram/webhook",
        json=callback_update("s:delpol:del", update_id=611),
        headers=WEBHOOK_HEADERS,
    )
    assert await repo.get_app_setting(db, "allow_delete") == "false"

    # the card no longer offers a delete button
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"a:view:{data['apartment']['id']}", update_id=612),
        headers=WEBHOOK_HEADERS,
    )
    assert f"d:ask:a:{data['apartment']['id']}" not in str(session.calls[-1][1])

    # a stale/hand-made callback is refused, not executed
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ok:t:{tenant_id}", update_id=613),
        headers=WEBHOOK_HEADERS,
    )
    assert await tenants_repo.get_tenant(db, tenant_id) is not None
    assert any(
        "الحذف النهائي متوقف" in str(call[1].get("text", "")) for call in session.calls
    )

    # back on -> delete works again
    await client.post(
        "/api/telegram/webhook",
        json=callback_update("s:delpol:del", update_id=614),
        headers=WEBHOOK_HEADERS,
    )
    assert await repo.get_app_setting(db, "allow_delete") == "true"
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"d:ok:t:{tenant_id}", update_id=615),
        headers=WEBHOOK_HEADERS,
    )
    assert await tenants_repo.get_tenant(db, tenant_id) is None


async def test_toggle_archive_off_hides_and_blocks(web, db) -> None:
    data = await seed(db)
    building_id = data["building"]["id"]
    _, client, session = web

    await client.post(
        "/api/telegram/webhook",
        json=callback_update("s:delpol:arch", update_id=621),
        headers=WEBHOOK_HEADERS,
    )
    assert await repo.get_app_setting(db, "allow_archive") == "false"

    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"b:view:{building_id}", update_id=622),
        headers=WEBHOOK_HEADERS,
    )
    assert f"b:arch:{building_id}" not in str(session.calls[-1][1])

    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"b:archok:{building_id}", update_id=623),
        headers=WEBHOOK_HEADERS,
    )
    building = await repo.get_building(db, building_id)
    assert building["is_active"] is True


async def test_policy_is_per_account(web, db) -> None:
    """Turning delete off for one account must not affect another account."""
    from app.db import platform
    from app.db.context import use_account

    await repo.set_app_setting(db, "allow_delete", "false")

    customer, _ = await platform.start_trial(
        db, telegram_id=777000222, display_name="عميل", username=None, days=30
    )
    with use_account(customer["id"]):
        settings = await repo.get_app_settings(db)
        assert settings["allow_delete"] == "true"
        await repo.set_app_setting(db, "allow_delete", "false")
        assert (await repo.get_app_settings(db))["allow_delete"] == "false"

    assert await repo.get_app_setting(db, "allow_delete") == "false"
