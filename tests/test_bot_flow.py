"""End-to-end bot flows driven through the webhook endpoint.

These tests exercise the real handlers, FSM storage, keyboards and SQL against
PostgreSQL — the closest thing to an owner actually using the bot.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest_asyncio

from app.config import Settings
from app.web.app import create_app
from tests.conftest import OWNER_ID, TEST_ENV
from tests.fake_bot import attach_fake_session

HEADERS = {"X-Telegram-Bot-Api-Secret-Token": TEST_ENV["TELEGRAM_WEBHOOK_SECRET"]}
CHAT = {"id": OWNER_ID, "type": "private"}
FROM = {"id": OWNER_ID, "is_bot": False, "first_name": "Owner"}


class Bot:
    """Tiny driver that posts updates and counts message ids."""

    def __init__(self, client: httpx.AsyncClient, session) -> None:
        self.client = client
        self.session = session
        self.update_id = 1000
        self.message_id = 500

    async def text(self, text: str) -> None:
        self.update_id += 1
        self.message_id += 1
        payload = {
            "update_id": self.update_id,
            "message": {
                "message_id": self.message_id,
                "date": int(dt.datetime.now(dt.timezone.utc).timestamp()),
                "chat": CHAT,
                "from": FROM,
                "text": text,
            },
        }
        response = await self.client.post("/api/telegram/webhook", json=payload, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True}, response.json()

    async def tap(self, data: str) -> None:
        self.update_id += 1
        self.message_id += 1
        payload = {
            "update_id": self.update_id,
            "callback_query": {
                "id": str(self.update_id),
                "from": FROM,
                "chat_instance": "ci-1",
                "data": data,
                "message": {
                    "message_id": self.message_id,
                    "date": int(dt.datetime.now(dt.timezone.utc).timestamp()),
                    "chat": CHAT,
                    "from": FROM,
                    "text": "القائمة",
                },
            },
        }
        response = await self.client.post("/api/telegram/webhook", json=payload, headers=HEADERS)
        assert response.status_code == 200, response.text

    def last_text(self) -> str:
        return self.session.sent_texts()[-1] if self.session.calls else ""


@pytest_asyncio.fixture
async def bot(postgres_dsn, db):
    cfg = Settings.from_env({**TEST_ENV, "DATABASE_URL": postgres_dsn})
    app = create_app(cfg)
    async with app.router.lifespan_context(app):
        session = attach_fake_session(app)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield Bot(client, session)


async def test_full_owner_journey(bot, db) -> None:
    # --- building ---------------------------------------------------------
    await bot.tap("b:add")
    assert "اسم المبنى" in bot.last_text()
    await bot.text("مبنى النور")
    await bot.text("نواكشوط، تفرغ زينة")
    await bot.text("مبنى سكني")
    buildings = await db.fetch_all("SELECT * FROM buildings")
    assert len(buildings) == 1
    assert buildings[0]["name"] == "مبنى النور"
    building_id = buildings[0]["id"]
    await bot.tap(f"b:view:{building_id}")
    assert "مبنى النور" in bot.last_text()

    # --- apartment --------------------------------------------------------
    await bot.tap(f"b:addapt:{building_id}")
    assert "رقم أو اسم الشقة" in bot.last_text()
    await bot.text("1")
    await bot.text("0")
    await bot.text("-")
    apartments = await db.fetch_all("SELECT * FROM apartments")
    assert len(apartments) == 1
    apartment_id = apartments[0]["id"]
    assert apartments[0]["occupancy_status"] == "vacant"

    # --- tenant -----------------------------------------------------------
    await bot.tap("t:add")
    await bot.tap(f"t:pickb:{building_id}")
    await bot.tap(f"t:apt:{apartment_id}")
    assert "الاسم الكامل" in bot.last_text()
    await bot.text("محمد أحمد سالم")
    await bot.text("22212345678")
    await bot.text("10000")
    await bot.text("اليوم")
    await bot.text("مستأجر جيد")
    assert "مراجعة بيانات المستأجر" in bot.last_text()
    await bot.tap("t:save")
    tenants = await db.fetch_all("SELECT * FROM tenants")
    assert len(tenants) == 1
    tenant = tenants[0]
    assert tenant["full_name"] == "محمد أحمد سالم"
    assert tenant["monthly_rent"] == Decimal("10000.00")
    assert tenant["currency"] == "MRU"
    apartment_after = await db.fetch_one("SELECT * FROM apartments WHERE id = %s", (apartment_id,))
    assert apartment_after["occupancy_status"] == "occupied"

    # --- profile ----------------------------------------------------------
    await bot.tap(f"t:view:{tenant['id']}")
    profile = bot.last_text()
    assert "محمد أحمد سالم" in profile
    assert "الاستحقاق القادم" in profile

    # --- payment ----------------------------------------------------------
    await bot.tap(f"pay:sel:{tenant['id']}")
    assert "المبلغ المستلم" in bot.last_text()
    await bot.text("10000")
    await bot.text("-")
    assert "تأكيد تسجيل الدفعة" in bot.last_text()
    pending = await db.fetch_one("SELECT * FROM pending_payments ORDER BY created_at DESC LIMIT 1")
    assert pending is not None
    await bot.tap(f"p:ok:{pending['id']}")
    success = bot.last_text()
    assert "تم تسجيل الدفعة بنجاح" in success

    payments = await db.fetch_all("SELECT * FROM payments")
    assert len(payments) == 1
    assert payments[0]["amount"] == Decimal("10000.00")
    refreshed = await db.fetch_one("SELECT * FROM tenants WHERE id = %s", (tenant["id"],))
    assert refreshed["next_due_date"] > tenant["next_due_date"]

    # --- duplicate confirmation is rejected ------------------------------
    await bot.tap(f"p:ok:{pending['id']}")
    assert await db.fetch_val("SELECT count(*) FROM payments WHERE tenant_id = %s", (tenant["id"],)) == 1

    # --- history & reports ------------------------------------------------
    await bot.tap(f"t:hist:{tenant['id']}")
    assert "سجل مدفوعات" in bot.last_text()

    await bot.tap("r:today")
    assert any("الملخص العام" in text for text in bot.session.sent_texts())

    await bot.tap("r:revenue")
    assert "إجمالي الإيرادات" in bot.last_text()

    await bot.tap("r:month")
    assert "تقرير الشهر" in bot.last_text()

    # --- search -----------------------------------------------------------
    await bot.text("🔎 البحث")
    await bot.text("محمد")
    assert "نتائج البحث" in bot.last_text()

    # --- archive ----------------------------------------------------------
    await bot.tap(f"t:arch:{tenant['id']}")
    assert "هل تريد أرشفة" in bot.last_text()
    await bot.tap(f"t:archok:{tenant['id']}")
    archived = await db.fetch_one("SELECT * FROM tenants WHERE id = %s", (tenant["id"],))
    assert archived["is_active"] is False
    apartment_final = await db.fetch_one("SELECT * FROM apartments WHERE id = %s", (apartment_id,))
    assert apartment_final["occupancy_status"] == "vacant"
    # payment history is preserved after archiving
    assert await db.fetch_val("SELECT count(*) FROM payments") == 1


async def test_partial_payment_flow(bot, db) -> None:
    await bot.tap("b:add")
    await bot.text("مبنى النور")
    await bot.text("-")
    await bot.text("-")
    building_id = int(await db.fetch_val("SELECT id FROM buildings"))
    await bot.tap(f"b:addapt:{building_id}")
    await bot.text("A1")
    await bot.text("-")
    await bot.text("-")
    apartment_id = int(await db.fetch_val("SELECT id FROM apartments"))

    await bot.tap(f"t:add:{apartment_id}")
    await bot.text("فاطمة بنت أحمد")
    await bot.text("-")
    await bot.text("12000")
    await bot.text("اليوم")
    await bot.text("-")
    await bot.tap("t:save")
    tenant_id = int(await db.fetch_val("SELECT id FROM tenants"))

    await bot.tap(f"pay:sel:{tenant_id}")
    await bot.text("5000")
    await bot.text("دفعة أولى")
    assert "دفعة جزئية" in bot.last_text()
    pending = await db.fetch_one("SELECT * FROM pending_payments ORDER BY created_at DESC LIMIT 1")
    await bot.tap(f"p:ok:{pending['id']}")
    payment = await db.fetch_one("SELECT * FROM payments")
    assert payment["payment_type"] == "partial"
    tenant = await db.fetch_one("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
    assert tenant["credit_amount"] == Decimal("5000.00")


async def test_invalid_inputs_do_not_break_the_flow(bot, db) -> None:
    await bot.tap("b:add")
    await bot.text("x")  # too short
    assert "المدخل غير صحيح" in bot.last_text()
    await bot.text("مبنى صحيح")
    await bot.text("/cancel")
    recent = "\n".join(bot.session.sent_texts()[-2:])
    assert "تم إلغاء" in recent
    assert await db.fetch_val("SELECT count(*) FROM buildings") == 0


async def test_expired_callback_is_handled(bot) -> None:
    await bot.tap("t:view:999999")
    assert "لم يتم العثور" in bot.last_text()


async def test_archiving_building_with_active_tenant_is_blocked(bot, db) -> None:
    await bot.tap("b:add")
    await bot.text("مبنى النور")
    await bot.text("-")
    await bot.text("-")
    building_id = int(await db.fetch_val("SELECT id FROM buildings"))
    await bot.tap(f"b:addapt:{building_id}")
    await bot.text("1")
    await bot.text("-")
    await bot.text("-")
    apartment_id = int(await db.fetch_val("SELECT id FROM apartments"))
    await bot.tap(f"t:add:{apartment_id}")
    await bot.text("محمد أحمد")
    await bot.text("-")
    await bot.text("9000")
    await bot.text("اليوم")
    await bot.text("-")
    await bot.tap("t:save")

    await bot.tap(f"b:archok:{building_id}")
    building = await db.fetch_one("SELECT * FROM buildings WHERE id = %s", (building_id,))
    assert building["is_active"] is True


async def test_tenant_cannot_be_added_twice_to_same_apartment(bot, db) -> None:
    await bot.tap("b:add")
    await bot.text("مبنى")
    await bot.text("-")
    await bot.text("-")
    building_id = int(await db.fetch_val("SELECT id FROM buildings"))
    await bot.tap(f"b:addapt:{building_id}")
    await bot.text("1")
    await bot.text("-")
    await bot.text("-")
    apartment_id = int(await db.fetch_val("SELECT id FROM apartments"))

    await bot.tap(f"t:add:{apartment_id}")
    await bot.text("الأول")
    await bot.text("-")
    await bot.text("5000")
    await bot.text("اليوم")
    await bot.text("-")
    await bot.tap("t:save")

    # apartment is no longer vacant, so the picker must not offer it
    await bot.tap("t:add")
    assert "لا توجد شقق فارغة" in bot.last_text()
    assert await db.fetch_val("SELECT count(*) FROM tenants") == 1
