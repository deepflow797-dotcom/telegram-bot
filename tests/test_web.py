"""Web layer: webhook protection, owner authorization, cron endpoint, health."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
from aiogram.types import Message, User

from app.config import ConfigError, Settings
from app.db import repository as repo
from app.db import tenants as tenants_repo
from app.web.app import create_app
from tests.conftest import OWNER_ID, TEST_ENV
from tests.fake_bot import attach_fake_session

WEBHOOK_HEADERS = {"X-Telegram-Bot-Api-Secret-Token": TEST_ENV["TELEGRAM_WEBHOOK_SECRET"]}
CRON_HEADERS = {"Authorization": f"Bearer {TEST_ENV['CRON_SECRET']}"}


def message_update(text: str, user_id: int = OWNER_ID, update_id: int = 100) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10,
            "date": 1767225600,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Owner"},
            "text": text,
        },
    }


async def test_health_endpoint(web) -> None:
    _, client, _ = web
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"
    assert body["timezone"] == "Africa/Nouakchott"

    index = await client.get("/")
    assert index.status_code == 200
    assert index.json()["service"] == "rental-management-telegram-bot"


async def test_health_reports_missing_configuration() -> None:
    broken_app = create_app()
    broken_app.state.runtime = None
    broken_app.state.config_error = ConfigError(["TELEGRAM_BOT_TOKEN is missing"])
    transport = httpx.ASGITransport(app=broken_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/health")
        assert response.status_code == 503
        assert "TELEGRAM_BOT_TOKEN is missing" in response.json()["problems"]
        webhook = await client.post("/api/telegram/webhook", json={})
        assert webhook.status_code == 503


async def test_webhook_rejects_missing_secret(web) -> None:
    _, client, session = web
    response = await client.post("/api/telegram/webhook", json=message_update("/start"))
    assert response.status_code == 401
    assert session.calls == []


async def test_webhook_rejects_wrong_secret(web) -> None:
    _, client, session = web
    response = await client.post(
        "/api/telegram/webhook",
        json=message_update("/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret-value-123456"},
    )
    assert response.status_code == 401
    assert session.calls == []


async def test_webhook_rejects_invalid_json(web) -> None:
    _, client, _ = web
    response = await client.post(
        "/api/telegram/webhook", content=b"not-json", headers=WEBHOOK_HEADERS
    )
    assert response.status_code == 400


async def test_first_start_gets_a_free_trial(web, db) -> None:
    """A brand-new Telegram account is activated on a 7-day trial, not blocked."""
    from app.db import platform

    _, client, session = web
    user_id = 999888777
    response = await client.post(
        "/api/telegram/webhook",
        json=message_update("/start", user_id=user_id),
        headers=WEBHOOK_HEADERS,
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    account = await platform.get_account_by_telegram(db, user_id)
    assert account is not None
    assert account["status"] == platform.STATUS_ACTIVE
    assert account["plan"] == platform.PLAN_TRIAL
    assert platform.days_left(account, platform.dt.date.today()) == 7

    texts = session.sent_texts()
    assert any("تجربة مجانية لمدة 7 أيام" in text for text in texts)
    assert any("22222222" in text for text in texts)  # owner contact
    assert any("حساب جديد بدأ التجربة" in text for text in _texts_to(session, OWNER_ID))
    assert any("مرحباً" in text for text in _texts_to(session, user_id))


async def test_owner_start_command(web) -> None:
    _, client, session = web
    response = await client.post(
        "/api/telegram/webhook", json=message_update("/start", update_id=101), headers=WEBHOOK_HEADERS
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    texts = session.sent_texts()
    assert len(texts) == 2  # welcome + inline menu
    assert "مرحباً" in texts[0]


async def test_owner_can_open_properties_menu(web) -> None:
    _, client, session = web
    response = await client.post(
        "/api/telegram/webhook",
        json=message_update("🏠 إدارة العقارات", update_id=102),
        headers=WEBHOOK_HEADERS,
    )
    assert response.status_code == 200
    assert any("إدارة العقارات" in text for text in session.sent_texts())


async def test_duplicate_update_is_ignored(web) -> None:
    _, client, session = web
    payload = message_update("/start", update_id=555)
    first = await client.post("/api/telegram/webhook", json=payload, headers=WEBHOOK_HEADERS)
    calls_after_first = len(session.calls)
    second = await client.post("/api/telegram/webhook", json=payload, headers=WEBHOOK_HEADERS)
    assert first.json() == {"ok": True}
    assert second.json() == {"ok": True, "duplicate": True}
    assert len(session.calls) == calls_after_first


async def test_cron_rejects_missing_secret(web) -> None:
    _, client, _ = web
    response = await client.post("/api/cron/daily-report")
    assert response.status_code == 401


async def test_cron_rejects_wrong_secret(web) -> None:
    _, client, _ = web
    response = await client.post(
        "/api/cron/daily-report", headers={"Authorization": "Bearer wrong-cron-secret"}
    )
    assert response.status_code == 401


async def test_cron_sends_report_once(web, db) -> None:
    _, client, session = web
    building = await repo.create_building(db, name="مبنى النور")
    apartment = await repo.create_apartment(db, building_id=building["id"], apartment_number="1")
    await tenants_repo.create_tenant(
        db,
        full_name="محمد أحمد",
        apartment_id=apartment["id"],
        monthly_rent=Decimal(10000),
        currency="MRU",
        rental_start_date=dt.date.today(),
    )

    first = await client.post("/api/cron/daily-report", headers=CRON_HEADERS)
    calls_after_first = len(session.calls)
    second = await client.post("/api/cron/daily-report", headers=CRON_HEADERS)

    assert first.status_code == 200
    assert first.json()["status"] == "sent"
    assert calls_after_first >= 1
    assert second.json()["status"] == "skipped"
    assert second.json()["reason"] == "already_sent"
    assert len(session.calls) == calls_after_first
    assert any("الملخص العام" in text for text in session.sent_texts())


async def test_cron_respects_disabled_setting(web, db) -> None:
    _, client, session = web
    await repo.set_app_setting(db, "report_enabled", "false")
    response = await client.post("/api/cron/daily-report", headers=CRON_HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert session.calls == []


async def test_cron_rejects_future_date(web) -> None:
    _, client, _ = web
    response = await client.post(
        "/api/cron/daily-report?date=2999-01-01", headers=CRON_HEADERS
    )
    assert response.status_code == 400


async def test_setup_webhook_requires_secret_and_url(web) -> None:
    _, client, _ = web
    assert (await client.post("/api/admin/setup-webhook")).status_code == 401
    assert (await client.post("/api/admin/setup-webhook", headers=CRON_HEADERS)).status_code == 400
    response = await client.post(
        "/api/admin/setup-webhook?url=http://insecure.example/hook", headers=CRON_HEADERS
    )
    assert response.status_code == 400


async def test_setup_webhook_success(web) -> None:
    _, client, session = web
    response = await client.post(
        "/api/admin/setup-webhook?url=https://bot.example.vercel.app/api/telegram/webhook",
        headers=CRON_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    names = [call[0] for call in session.calls]
    assert "SetWebhook" in names
    set_webhook = next(call[1] for call in session.calls if call[0] == "SetWebhook")
    assert set_webhook["secret_token"] == TEST_ENV["TELEGRAM_WEBHOOK_SECRET"]
    assert set_webhook["url"].endswith("/api/telegram/webhook")


async def test_tenants_admin_endpoint_protected(web, db) -> None:
    _, client, _ = web
    assert (await client.get("/api/admin/tenants")).status_code == 401
    response = await client.get("/api/admin/tenants", headers=CRON_HEADERS)
    assert response.status_code == 200
    assert response.json()["tenants"] == []


# ---------------------------------------------------------------------------
# middleware unit tests
# ---------------------------------------------------------------------------
def _user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="x")


async def test_middleware_blocks_foreign_user(db, settings) -> None:
    """With the trial switched off, an unknown user only gets the request screen."""
    from app.db import platform

    await platform.set_platform_setting(db, "trial_days", "0")
    from app.bot.dispatcher import build_bot
    from app.bot.middlewares import AccessMiddleware
    from tests.fake_bot import FakeSession

    bot = build_bot(settings)
    bot.session = FakeSession()
    middleware = AccessMiddleware(db, settings)
    called = False

    async def handler(event, data):
        nonlocal called
        called = True
        return "handled"

    message = Message.model_validate(
        {
            "message_id": 1,
            "date": 1767225600,
            "chat": {"id": 424242, "type": "private"},
            "text": "hi",
        }
    ).as_(bot)
    result = await middleware(handler, message, {"event_from_user": _user(424242), "bot": bot})
    assert result is None
    assert called is False
    await bot.session.close()

    from app.db import platform

    account = await platform.get_account_by_telegram(db, 424242)
    assert account is not None
    assert account["status"] == "pending"


async def test_middleware_allows_owner(db, settings) -> None:
    from app.bot.middlewares import AccessMiddleware

    middleware = AccessMiddleware(db, settings)

    async def handler(event, data):
        assert data["is_owner"] is True
        assert data["account"]["id"] == 1
        return "handled"

    result = await middleware(handler, "event", {"event_from_user": _user(OWNER_ID)})
    assert result == "handled"


async def test_schema_health_and_migration_endpoint(postgres_dsn) -> None:
    """A fresh database reports a missing schema until migrations are applied."""
    import uuid

    import psycopg

    name = f"fresh_{uuid.uuid4().hex[:8]}"
    async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as conn:
        await conn.execute(f'CREATE DATABASE "{name}"')
    fresh_dsn = postgres_dsn.replace("/postgres?", f"/{name}?")
    cfg = Settings.from_env({**TEST_ENV, "DATABASE_URL": fresh_dsn})
    app = create_app(cfg)
    async with app.router.lifespan_context(app):
        attach_fake_session(app)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            before = await client.get("/api/health")
            assert before.status_code == 503
            assert before.json()["schema"] == "missing"
            assert "hint" in before.json()

            assert (await client.post("/api/admin/migrate")).status_code == 401
            applied = await client.post("/api/admin/migrate", headers=CRON_HEADERS)
            assert applied.status_code == 200
            assert applied.json()["applied"] == ["0001_init", "0002_accounts"]
            assert applied.json()["schema_ready"] is True

            after = await client.get("/api/health")
            assert after.status_code == 200
            assert after.json()["schema"] == "ready"
            # idempotent
            again = await client.post("/api/admin/migrate", headers=CRON_HEADERS)
            assert again.json()["applied"] == []


async def test_auto_setup_migrates_and_registers_webhook_once(postgres_dsn) -> None:
    """AUTO_SETUP=true brings a brand-new deployment to a working state."""
    import uuid

    import psycopg

    name = f"auto_{uuid.uuid4().hex[:8]}"
    async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as conn:
        await conn.execute(f'CREATE DATABASE "{name}"')
    fresh_dsn = postgres_dsn.replace("/postgres?", f"/{name}?")
    env = {
        **TEST_ENV,
        "DATABASE_URL": fresh_dsn,
        "AUTO_SETUP": "true",
        "WEBHOOK_URL": "https://rental.example.vercel.app/api/telegram/webhook",
    }
    cfg = Settings.from_env(env)

    first_app = create_app(cfg)
    first_session = attach_fake_session(first_app)
    async with first_app.router.lifespan_context(first_app):
        transport = httpx.ASGITransport(app=first_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["schema"] == "ready"
            assert health.json()["auto_setup"] is True

    names = [call[0] for call in first_session.calls]
    assert names.count("SetWebhook") == 1
    set_webhook = next(call[1] for call in first_session.calls if call[0] == "SetWebhook")
    assert set_webhook["url"] == cfg.webhook_url
    assert set_webhook["secret_token"] == TEST_ENV["TELEGRAM_WEBHOOK_SECRET"]

    # a second cold start must not register the webhook again
    second_app = create_app(cfg)
    second_session = attach_fake_session(second_app)
    async with second_app.router.lifespan_context(second_app):
        transport = httpx.ASGITransport(app=second_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            assert (await client.get("/api/health")).status_code == 200
    assert [call[0] for call in second_session.calls].count("SetWebhook") == 0


def callback_update(data: str, user_id: int = OWNER_ID, update_id: int = 900) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": user_id, "is_bot": False, "first_name": "Owner"},
            "chat_instance": "chat-instance-1",
            "data": data,
            "message": {
                "message_id": 11,
                "date": 1767225600,
                "chat": {"id": user_id, "type": "private"},
                "text": "📥 طلب تفعيل جديد",
            },
        },
    }


def _texts_to(session, chat_id: int) -> list[str]:
    return [
        str(call[1].get("text", ""))
        for call in session.calls
        if call[0] in {"SendMessage", "EditMessageText"} and call[1].get("chat_id") == chat_id
    ]


async def test_new_customer_request_is_approved_end_to_end(web, db) -> None:
    """start → request → owner approves → customer activated with an empty dataset."""
    from app.db import platform
    from app.db.context import use_account
    from app.db.repository import list_buildings

    await platform.set_platform_setting(db, "trial_days", "0")
    _, client, session = web
    customer_id = 555111222

    first = await client.post(
        "/api/telegram/webhook",
        json=message_update("/start", user_id=customer_id, update_id=201),
        headers=WEBHOOK_HEADERS,
    )
    assert first.status_code == 200

    owner_texts = _texts_to(session, OWNER_ID)
    assert any("طلب تفعيل جديد" in text for text in owner_texts)
    assert "adm:ok:" in str(session.calls[0][1])

    customer_texts = _texts_to(session, customer_id)
    assert customer_texts and "22222222" in customer_texts[0]

    rows = await platform.list_accounts(db, statuses=[platform.STATUS_PENDING])
    assert len(rows) == 1
    account_id = rows[0]["id"]

    session.calls.clear()
    approved = await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"adm:ok:{account_id}:30", update_id=202),
        headers=WEBHOOK_HEADERS,
    )
    assert approved.status_code == 200

    stored = await platform.get_account(db, account_id)
    assert stored["status"] == platform.STATUS_ACTIVE
    assert stored["paid_until"] is not None
    assert any("تم تفعيل البوت" in text for text in _texts_to(session, customer_id))

    # the new account owns an empty, separate dataset
    with use_account(account_id):
        assert await list_buildings(db) == []

    # and after activation the customer sees the normal menu, not the request screen
    session.calls.clear()
    again = await client.post(
        "/api/telegram/webhook",
        json=message_update("/start", user_id=customer_id, update_id=203),
        headers=WEBHOOK_HEADERS,
    )
    assert again.status_code == 200
    texts = _texts_to(session, customer_id)
    assert texts and "مرحباً" in texts[0]
    assert all("بالاشتراك" not in text for text in texts)


async def test_rejected_customer_cannot_use_the_bot(web, db) -> None:
    from app.db import platform

    await platform.set_platform_setting(db, "trial_days", "0")
    _, client, session = web
    customer_id = 555333444

    await client.post(
        "/api/telegram/webhook",
        json=message_update("/start", user_id=customer_id, update_id=301),
        headers=WEBHOOK_HEADERS,
    )
    rows = await platform.list_accounts(db, statuses=[platform.STATUS_PENDING])
    account_id = rows[0]["id"]

    session.calls.clear()
    await client.post(
        "/api/telegram/webhook",
        json=callback_update(f"adm:no:{account_id}", update_id=302),
        headers=WEBHOOK_HEADERS,
    )
    assert any("رفض" in text for text in _texts_to(session, customer_id))

    session.calls.clear()
    await client.post(
        "/api/telegram/webhook",
        json=message_update("/start", user_id=customer_id, update_id=303),
        headers=WEBHOOK_HEADERS,
    )
    texts = _texts_to(session, customer_id)
    assert texts
    assert all("مرحباً" not in text for text in texts)
    assert any("رفض" in text for text in texts)


async def test_owner_sets_trial_days_from_the_bot(web, db) -> None:
    """Owner → 📞 بيانات التواصل → 🎁 مدة التجربة → sends a number."""
    from app.db import platform

    _, client, session = web

    opened = await client.post(
        "/api/telegram/webhook",
        json=callback_update("adm:set:trial", update_id=401),
        headers=WEBHOOK_HEADERS,
    )
    assert opened.status_code == 200
    assert any("عدد أيام التجربة" in text for text in _texts_to(session, OWNER_ID))

    saved = await client.post(
        "/api/telegram/webhook",
        json=message_update("14", update_id=402),
        headers=WEBHOOK_HEADERS,
    )
    assert saved.status_code == 200
    assert await platform.get_trial_days(db) == 14

    session.calls.clear()
    await client.post(
        "/api/telegram/webhook",
        json=message_update("/start", user_id=555999111, update_id=403),
        headers=WEBHOOK_HEADERS,
    )
    account = await platform.get_account_by_telegram(db, 555999111)
    assert platform.days_left(account, platform.dt.date.today()) == 14
    assert any("14 أيام" in text for text in _texts_to(session, 555999111))


async def test_owner_rejects_invalid_trial_days(web, db) -> None:
    from app.db import platform

    _, client, session = web
    await client.post(
        "/api/telegram/webhook",
        json=callback_update("adm:set:trial", update_id=411),
        headers=WEBHOOK_HEADERS,
    )
    session.calls.clear()
    await client.post(
        "/api/telegram/webhook",
        json=message_update("خمسة", update_id=412),
        headers=WEBHOOK_HEADERS,
    )
    assert await platform.get_trial_days(db) == 7
    assert any("رقماً بين" in text for text in _texts_to(session, OWNER_ID))

    session.calls.clear()
    await client.post(
        "/api/telegram/webhook",
        json=message_update("999", update_id=413),
        headers=WEBHOOK_HEADERS,
    )
    assert await platform.get_trial_days(db) == 7

