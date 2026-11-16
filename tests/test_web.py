"""Web layer: webhook protection, owner authorization, cron endpoint, health."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest_asyncio
from aiogram.types import Message, User

from app.bot.middlewares import OwnerOnlyMiddleware
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


@pytest_asyncio.fixture
async def web(postgres_dsn, db):
    cfg = Settings.from_env({**TEST_ENV, "DATABASE_URL": postgres_dsn})
    app = create_app(cfg)
    async with app.router.lifespan_context(app):
        session = attach_fake_session(app)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield app, client, session


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


async def test_unauthorized_user_is_rejected(web) -> None:
    _, client, session = web
    response = await client.post(
        "/api/telegram/webhook",
        json=message_update("/start", user_id=999888777),
        headers=WEBHOOK_HEADERS,
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    texts = session.sent_texts()
    assert len(texts) == 1
    assert "خاص بالمالك" in texts[0] or "غير مصرح" in texts[0]
    assert "مرحباً" not in texts[0]


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
        monthly_rent=Decimal("10000"),
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


async def test_middleware_blocks_foreign_user() -> None:
    middleware = OwnerOnlyMiddleware(OWNER_ID)
    called = False

    async def handler(event, data):
        nonlocal called
        called = True
        return "handled"

    message = Message.model_validate(
        {
            "message_id": 1,
            "date": 1767225600,
            "chat": {"id": OWNER_ID, "type": "private"},
            "text": "hi",
        }
    )
    result = await middleware(handler, message, {"event_from_user": _user(424242)})
    assert result is None
    assert called is False


async def test_middleware_allows_owner() -> None:
    middleware = OwnerOnlyMiddleware(OWNER_ID)

    async def handler(event, data):
        return "handled"

    result = await middleware(handler, "event", {"event_from_user": _user(OWNER_ID)})
    assert result == "handled"
