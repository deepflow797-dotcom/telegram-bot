"""Permanent delete for buildings, apartments and tenants (two-step, guarded).

Archiving stays the default for records that carry history. A hard delete is
offered separately and is refused when something still depends on the record:

* building  → blocked while it still has apartments,
* apartment → blocked while any tenant (active or archived) points at it,
* tenant    → blocked once payments exist; archive it to keep the money trail.

Every successful delete is written to ``audit_log``.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot import keyboards as kb
from app.bot import policy as bot_policy
from app.bot.utils import answer_callback, edit_or_send
from app.config import Settings
from app.db import repository as repo
from app.db import tenants as tenants_repo

router = Router(name="deletes")

KIND_BUILDING = "b"
KIND_APARTMENT = "a"
KIND_TENANT = "t"


async def _ask_building(callback: CallbackQuery, db) -> None:
    building_id = int(callback.data.split(":")[3])
    building = await repo.get_building(db, building_id)
    if building is None:
        await answer_callback(callback)
        await edit_or_send(callback, "⚠️ لم يتم العثور على المبنى.", kb.properties_menu())
        return
    counts = await repo.building_dependency_counts(db, building_id)
    await answer_callback(callback)
    if counts["apartments"]:
        await edit_or_send(
            callback,
            f"⛔️ لا يمكن حذف المبنى «{building['name']}» لأنه يحتوي على "
            f"<b>{counts['apartments']}</b> شقة.\n\n"
            "احذف شققه أولاً، أو استخدم 🗄 أرشفة المبنى (يحفظ كل السجل).",
            kb.building_card(building_id, bool(building.get("is_active"))),
        )
        return
    await edit_or_send(
        callback,
        f"🗑 <b>حذف المبنى نهائياً</b>\n\n"
        f"المبنى: <b>{building['name']}</b>\n"
        f"العنوان: {building.get('address') or '—'}\n\n"
        "لا توجد شقق مرتبطة به. سيُحذف السجل نهائياً ولا يمكن استرجاعه.",
        kb.confirm_delete(KIND_BUILDING, building_id, f"b:view:{building_id}"),
    )


async def _ask_apartment(callback: CallbackQuery, db) -> None:
    apartment_id = int(callback.data.split(":")[3])
    apartment = await repo.get_apartment(db, apartment_id)
    if apartment is None:
        await answer_callback(callback)
        await edit_or_send(callback, "⚠️ لم يتم العثور على الشقة.", kb.properties_menu())
        return
    counts = await repo.apartment_dependency_counts(db, apartment_id)
    await answer_callback(callback)
    if counts["tenants"]:
        await edit_or_send(
            callback,
            f"⛔️ لا يمكن حذف الشقة «{apartment['apartment_number']}» لوجود "
            f"<b>{counts['tenants']}</b> مستأجر مرتبط بها (حتى المؤرشف يمنع الحذف).\n\n"
            "احذف المستأجر أولاً إن كان بلا دفعات، أو استخدم 🗄 أرشفة الشقة.",
            kb.apartment_card(apartment),
        )
        return
    await edit_or_send(
        callback,
        f"🗑 <b>حذف الشقة نهائياً</b>\n\n"
        f"الشقة: <b>{apartment['apartment_number']}</b> — "
        f"{apartment.get('building_name') or ''}\n\n"
        "لا يوجد مستأجر مرتبط بها. سيُحذف السجل نهائياً ولا يمكن استرجاعه.",
        kb.confirm_delete(KIND_APARTMENT, apartment_id, f"a:view:{apartment_id}"),
    )


async def _ask_tenant(callback: CallbackQuery, db) -> None:
    tenant_id = int(callback.data.split(":")[3])
    tenant = await tenants_repo.get_tenant(db, tenant_id)
    if tenant is None:
        await answer_callback(callback)
        await edit_or_send(callback, "⚠️ لم يتم العثور على المستأجر.", kb.tenants_menu())
        return
    counts = await tenants_repo.tenant_dependency_counts(db, tenant_id)
    await answer_callback(callback)
    if counts["payments"] or counts["allocations"]:
        await edit_or_send(
            callback,
            f"⛔️ لا يمكن حذف المستأجر «{tenant['full_name']}» لأن له "
            f"<b>{counts['payments']}</b> دفعة مسجلة.\n\n"
            "حذف المستأجر في هذه الحالة يمحو سجل المدفوعات، لذلك يُستخدم "
            "🗄 أرشفة المستأجر: يحرّر الشقة ويحفظ كل السجل المالي.",
            kb.tenant_profile(tenant),
        )
        return
    await edit_or_send(
        callback,
        f"🗑 <b>حذف المستأجر نهائياً</b>\n\n"
        f"الاسم: <b>{tenant['full_name']}</b>\n"
        f"الشقة: {tenant.get('apartment_number') or '—'} — "
        f"{tenant.get('building_name') or ''}\n\n"
        "لا توجد دفعات مسجلة له. سيُحذف السجل نهائياً وتُحرَّر الشقة.",
        kb.confirm_delete(KIND_TENANT, tenant_id, f"t:view:{tenant_id}"),
    )


@router.callback_query(F.data.startswith("d:ask:"))
async def ask_delete(callback: CallbackQuery, db) -> None:
    if not bot_policy.allow_delete():
        await answer_callback(callback, "الحذف النهائي متوقف من الإعدادات", alert=True)
        await edit_or_send(
            callback,
            "⛔️ الحذف النهائي متوقف في حسابك.\n\n"
            "لتشغيله: ⚙️ الإعدادات ← 🗑 الحذف والأرشفة ← 🗑 الحذف النهائي.\n"
            "تقدر تستخدم 🗄 الأرشفة بدلاً منه (تحفظ كل السجل).",
            kb.settings_hint("s:delpol"),
        )
        return
    parts = (callback.data or "").split(":")
    kind = parts[2] if len(parts) > 3 else ""
    if kind == KIND_BUILDING:
        await _ask_building(callback, db)
    elif kind == KIND_APARTMENT:
        await _ask_apartment(callback, db)
    elif kind == KIND_TENANT:
        await _ask_tenant(callback, db)
    else:  # pragma: no cover - defensive
        await answer_callback(callback, "خيار غير معروف", alert=True)


async def _delete_building(callback: CallbackQuery, db, cfg: Settings, entity_id: int) -> None:
    building = await repo.get_building(db, entity_id)
    if building is None:
        await answer_callback(callback, "المبنى غير موجود", alert=True)
        await edit_or_send(callback, "⚠️ لم يتم العثور على المبنى.", kb.properties_menu())
        return
    deleted = await repo.delete_building(db, entity_id)
    if deleted is None:
        await answer_callback(callback, "فيه شقق مرتبطة — احذفها أولاً", alert=True)
        return
    await repo.log_audit(
        db,
        actor_id=cfg.owner_id,
        action="building_deleted",
        entity_type="building",
        entity_id=entity_id,
        details={"name": deleted["name"]},
    )
    await answer_callback(callback, "تم الحذف")
    buildings = await repo.list_buildings(db, active_only=True)
    await edit_or_send(
        callback,
        f"✅ حُذف المبنى «{deleted['name']}» نهائياً.",
        kb.buildings_list(buildings) if buildings else kb.properties_menu(),
    )


async def _delete_apartment(callback: CallbackQuery, db, cfg: Settings, entity_id: int) -> None:
    apartment = await repo.get_apartment(db, entity_id)
    if apartment is None:
        await answer_callback(callback, "الشقة غير موجودة", alert=True)
        await edit_or_send(callback, "⚠️ لم يتم العثور على الشقة.", kb.properties_menu())
        return
    deleted = await repo.delete_apartment(db, entity_id)
    if deleted is None:
        await answer_callback(callback, "فيها مستأجر مرتبط — احذفه أولاً", alert=True)
        return
    await repo.log_audit(
        db,
        actor_id=cfg.owner_id,
        action="apartment_deleted",
        entity_type="apartment",
        entity_id=entity_id,
        details={"number": deleted["apartment_number"], "building_id": deleted["building_id"]},
    )
    await answer_callback(callback, "تم الحذف")
    siblings = await repo.list_apartments(db, building_id=deleted["building_id"], active_only=False)
    await edit_or_send(
        callback,
        f"✅ حُذفت الشقة «{deleted['apartment_number']}» نهائياً.",
        kb.apartments_list(siblings, f"b:view:{deleted['building_id']}")
        if siblings
        else kb.building_card(deleted["building_id"]),
    )


async def _delete_tenant(callback: CallbackQuery, db, cfg: Settings, entity_id: int) -> None:
    tenant = await tenants_repo.get_tenant(db, entity_id)
    if tenant is None:
        await answer_callback(callback, "المستأجر غير موجود", alert=True)
        await edit_or_send(callback, "⚠️ لم يتم العثور على المستأجر.", kb.tenants_menu())
        return
    deleted = await tenants_repo.delete_tenant(db, entity_id)
    if deleted is None:
        await answer_callback(callback, "له دفعات مسجلة — استخدم الأرشفة", alert=True)
        return
    await repo.log_audit(
        db,
        actor_id=cfg.owner_id,
        action="tenant_deleted",
        entity_type="tenant",
        entity_id=entity_id,
        details={"name": deleted["full_name"]},
    )
    await answer_callback(callback, "تم الحذف")
    await edit_or_send(
        callback,
        f"✅ حُذف المستأجر «{deleted['full_name']}» نهائياً وتحرّرت الشقة.",
        kb.tenants_menu(),
    )


@router.callback_query(F.data.startswith("d:ok:"))
async def confirm_delete(callback: CallbackQuery, db, cfg: Settings) -> None:
    if not bot_policy.allow_delete():
        await answer_callback(callback, "الحذف النهائي متوقف من الإعدادات", alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 4:
        await answer_callback(callback, "طلب غير صحيح", alert=True)
        return
    kind, entity_id = parts[2], int(parts[3])
    if kind == KIND_BUILDING:
        await _delete_building(callback, db, cfg, entity_id)
    elif kind == KIND_APARTMENT:
        await _delete_apartment(callback, db, cfg, entity_id)
    elif kind == KIND_TENANT:
        await _delete_tenant(callback, db, cfg, entity_id)
    else:  # pragma: no cover - defensive
        await answer_callback(callback, "خيار غير معروف", alert=True)
