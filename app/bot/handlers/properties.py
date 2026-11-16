"""Buildings and apartments management."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from psycopg.errors import UniqueViolation

from app.bot import keyboards as kb
from app.bot import texts
from app.bot.states import ApartmentForm, BuildingForm, EditForm
from app.bot.utils import (
    answer_callback,
    edit_or_send,
    is_cancel,
    parse_int,
    skipped,
    to_html,
)
from app.bot.views import apartment_text, building_text
from app.config import Settings
from app.db import repository as repo

logger = logging.getLogger(__name__)
router = Router(name="properties")


# ---------------------------------------------------------------------------
# buildings — list & card
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "b:list")
async def list_buildings(callback: CallbackQuery, db) -> None:
    await answer_callback(callback)
    buildings = await repo.list_buildings(db, active_only=True)
    if not buildings:
        await edit_or_send(callback, texts.NO_BUILDINGS, kb.properties_menu())
        return
    await edit_or_send(callback, f"🏢 *المباني* ({len(buildings)})", kb.buildings_list(buildings))


@router.callback_query(F.data.startswith("b:view:"))
async def view_building(callback: CallbackQuery, db) -> None:
    building_id = parse_int(callback.data.split(":")[2])
    building = await repo.get_building(db, building_id)
    await answer_callback(callback)
    if building is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المبنى.", kb.properties_menu())
        return
    await edit_or_send(
        callback, building_text(building), kb.building_card(building_id, building["is_active"])
    )


@router.callback_query(F.data.startswith("b:apts:"))
async def building_apartments(callback: CallbackQuery, db) -> None:
    building_id = parse_int(callback.data.split(":")[2])
    building = await repo.get_building(db, building_id)
    await answer_callback(callback)
    if building is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المبنى.", kb.properties_menu())
        return
    apartments = await repo.list_apartments(db, building_id=building_id, active_only=True)
    if not apartments:
        await edit_or_send(
            callback,
            f"ℹ️ لا توجد شقق في *{building['name']}* بعد.",
            kb.building_card(building_id),
        )
        return
    await edit_or_send(
        callback,
        f"🚪 *شقق {building['name']}* ({len(apartments)})",
        kb.apartments_list(apartments, back=f"b:view:{building_id}"),
    )


# ---------------------------------------------------------------------------
# buildings — add
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "b:add")
async def add_building_start(callback: CallbackQuery, state: FSMContext) -> None:
    await answer_callback(callback)
    await state.clear()
    await state.set_state(BuildingForm.name)
    await edit_or_send(callback, texts.ASK_BUILDING_NAME, kb.cancel_keyboard("b:list"))


@router.message(BuildingForm.name)
async def add_building_name(message: Message, state: FSMContext) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer(texts.INVALID_INPUT)
        return
    await state.update_data(name=name)
    await state.set_state(BuildingForm.address)
    await message.answer(texts.ASK_BUILDING_ADDRESS, reply_markup=kb.cancel_keyboard("b:list"))


@router.message(BuildingForm.address)
async def add_building_address(message: Message, state: FSMContext) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    await state.update_data(address=skipped(message.text))
    await state.set_state(BuildingForm.description)
    await message.answer(texts.ASK_BUILDING_DESCRIPTION, reply_markup=kb.cancel_keyboard("b:list"))


@router.message(BuildingForm.description)
async def add_building_description(message: Message, state: FSMContext, db, cfg: Settings) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    data = await state.get_data()
    await state.clear()
    building = await repo.create_building(
        db,
        name=data.get("name", ""),
        address=data.get("address"),
        description=skipped(message.text),
    )
    await repo.log_audit(
        db,
        actor_id=cfg.owner_id,
        action="building_created",
        entity_type="building",
        entity_id=building["id"],
        details={"name": building["name"]},
    )
    await message.answer("✅ تم إضافة المبنى بنجاح.")
    building = await repo.get_building(db, building["id"])
    await message.answer(
        to_html(building_text(building)),
        reply_markup=kb.building_card(building["id"], True),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# buildings — edit / archive
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("b:edit:"))
async def edit_building_field(callback: CallbackQuery, state: FSMContext, db) -> None:
    parts = callback.data.split(":")  # b:edit:<field>:<id>
    field = parts[2] if len(parts) > 3 else "name"
    building_id = parse_int(parts[-1])
    building = await repo.get_building(db, building_id)
    await answer_callback(callback)
    if building is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المبنى.", kb.properties_menu())
        return
    await state.clear()
    await state.set_state(EditForm.value)
    await state.update_data(entity="building", entity_id=building_id, field=field)
    labels = {
        "name": "الاسم الجديد",
        "address": "العنوان الجديد",
        "description": "الوصف الجديد",
    }
    await edit_or_send(
        callback,
        f"✏️ أرسل {labels.get(field, 'القيمة الجديدة')} للمبنى:",
        kb.cancel_keyboard(f"b:view:{building_id}"),
    )


@router.callback_query(F.data.startswith("b:arch:"))
async def archive_building_confirm(callback: CallbackQuery, db) -> None:
    building_id = parse_int(callback.data.split(":")[2])
    building = await repo.get_building(db, building_id)
    await answer_callback(callback)
    if building is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المبنى.", kb.properties_menu())
        return
    await edit_or_send(
        callback,
        f"🗄 هل تريد أرشفة *{building['name']}*؟\n"
        "سيتم أرشفة جميع شققه. لا يمكن حذف البيانات أو سجل المدفوعات.",
        kb.confirm_archive("b", building_id),
    )


@router.callback_query(F.data.startswith("b:archok:"))
async def archive_building(callback: CallbackQuery, db, cfg: Settings) -> None:
    building_id = parse_int(callback.data.split(":")[2])
    active = await repo.building_active_tenants_count(db, building_id)
    if active:
        await answer_callback(
            callback, f"⚠️ لا يمكن أرشفة المبنى: يوجد {active} مستأجر نشط.", alert=True
        )
        return
    building = await repo.archive_building(db, building_id)
    await answer_callback(callback, "تم الأرشفة")
    if building is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المبنى.", kb.properties_menu())
        return
    await repo.log_audit(
        db,
        actor_id=cfg.owner_id,
        action="building_archived",
        entity_type="building",
        entity_id=building_id,
    )
    buildings = await repo.list_buildings(db, active_only=True)
    await edit_or_send(
        callback,
        "✅ تم أرشفة المبنى.",
        kb.buildings_list(buildings) if buildings else kb.properties_menu(),
    )


# ---------------------------------------------------------------------------
# apartments — list & card
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "a:list")
async def list_apartments(callback: CallbackQuery, db) -> None:
    await answer_callback(callback)
    apartments = await repo.list_apartments(db, active_only=True)
    if not apartments:
        await edit_or_send(callback, texts.NO_APARTMENTS, kb.properties_menu())
        return
    await edit_or_send(
        callback, f"🚪 *الشقق* ({len(apartments)})", kb.apartments_list(apartments)
    )


@router.callback_query(F.data.startswith("a:view:"))
async def view_apartment(callback: CallbackQuery, db) -> None:
    apartment_id = parse_int(callback.data.split(":")[2])
    apartment = await repo.get_apartment(db, apartment_id)
    await answer_callback(callback)
    if apartment is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على الشقة.", kb.properties_menu())
        return
    await edit_or_send(callback, apartment_text(apartment), kb.apartment_card(apartment))


@router.callback_query(F.data.startswith("a:edit:"))
async def edit_apartment_field(callback: CallbackQuery, state: FSMContext, db) -> None:
    parts = callback.data.split(":")  # a:edit:<field>:<id>
    field = parts[2] if len(parts) > 3 else "number"
    apartment_id = parse_int(parts[-1])
    apartment = await repo.get_apartment(db, apartment_id)
    await answer_callback(callback)
    if apartment is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على الشقة.", kb.properties_menu())
        return
    await state.clear()
    await state.set_state(EditForm.value)
    await state.update_data(entity="apartment", entity_id=apartment_id, field=field)
    labels = {
        "number": "رقم الشقة الجديد",
        "floor": "الطابق الجديد",
        "description": "الوصف الجديد",
    }
    await edit_or_send(
        callback,
        f"✏️ أرسل {labels.get(field, 'القيمة الجديدة')}:",
        kb.cancel_keyboard(f"a:view:{apartment_id}"),
    )


@router.callback_query(F.data.startswith("a:arch:"))
async def archive_apartment_confirm(callback: CallbackQuery, db) -> None:
    apartment_id = parse_int(callback.data.split(":")[2])
    apartment = await repo.get_apartment(db, apartment_id)
    await answer_callback(callback)
    if apartment is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على الشقة.", kb.properties_menu())
        return
    await edit_or_send(
        callback,
        f"🗄 هل تريد أرشفة شقة *{apartment['apartment_number']}*؟",
        kb.confirm_archive("a", apartment_id),
    )


@router.callback_query(F.data.startswith("a:archok:"))
async def archive_apartment(callback: CallbackQuery, db, cfg: Settings) -> None:
    apartment_id = parse_int(callback.data.split(":")[2])
    tenant = await repo.apartment_active_tenant(db, apartment_id)
    if tenant:
        await answer_callback(
            callback, "⚠️ لا يمكن أرشفة شقة بها مستأجر نشط.", alert=True
        )
        return
    apartment = await repo.archive_apartment(db, apartment_id)
    await answer_callback(callback, "تم الأرشفة")
    if apartment is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على الشقة.", kb.properties_menu())
        return
    await repo.log_audit(
        db,
        actor_id=cfg.owner_id,
        action="apartment_archived",
        entity_type="apartment",
        entity_id=apartment_id,
    )
    apartments = await repo.list_apartments(db, active_only=True)
    await edit_or_send(
        callback,
        "✅ تم أرشفة الشقة.",
        kb.apartments_list(apartments) if apartments else kb.properties_menu(),
    )


# ---------------------------------------------------------------------------
# apartments — add
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "a:add")
async def add_apartment_start(callback: CallbackQuery, state: FSMContext, db) -> None:
    await answer_callback(callback)
    buildings = await repo.list_buildings(db, active_only=True)
    if not buildings:
        await edit_or_send(
            callback, "⚠️ أضف مبنى أولاً قبل إضافة الشقق.", kb.properties_menu()
        )
        return
    await state.clear()
    await state.set_state(ApartmentForm.building)
    await edit_or_send(callback, "🏢 اختر المبنى:", kb.buildings_picker(buildings, "a:pickb", "m:props"))


@router.callback_query(F.data.startswith("b:addapt:"))
async def add_apartment_for_building(callback: CallbackQuery, state: FSMContext, db) -> None:
    building_id = parse_int(callback.data.split(":")[2])
    building = await repo.get_building(db, building_id)
    await answer_callback(callback)
    if building is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المبنى.", kb.properties_menu())
        return
    await state.clear()
    await state.update_data(building_id=building_id)
    await state.set_state(ApartmentForm.number)
    await edit_or_send(
        callback,
        f"🚪 أضف شقة في *{building['name']}*\n\n{texts.ASK_APARTMENT_NUMBER}",
        kb.cancel_keyboard(f"b:view:{building_id}"),
    )


@router.callback_query(F.data.startswith("a:pickb:"))
async def add_apartment_building_chosen(callback: CallbackQuery, state: FSMContext, db) -> None:
    building_id = parse_int(callback.data.split(":")[2])
    building = await repo.get_building(db, building_id)
    await answer_callback(callback)
    if building is None:
        await edit_or_send(callback, "⚠️ لم يتم العثور على المبنى.", kb.properties_menu())
        return
    await state.update_data(building_id=building_id)
    await state.set_state(ApartmentForm.number)
    await edit_or_send(
        callback,
        f"🚪 أضف شقة في *{building['name']}*\n\n{texts.ASK_APARTMENT_NUMBER}",
        kb.cancel_keyboard(f"b:view:{building_id}"),
    )


@router.message(ApartmentForm.number)
async def add_apartment_number(message: Message, state: FSMContext) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    number = (message.text or "").strip()
    if not number:
        await message.answer(texts.INVALID_INPUT)
        return
    await state.update_data(apartment_number=number)
    await state.set_state(ApartmentForm.floor)
    await message.answer(texts.ASK_APARTMENT_FLOOR, reply_markup=kb.cancel_keyboard("m:props"))


@router.message(ApartmentForm.floor)
async def add_apartment_floor(message: Message, state: FSMContext) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    await state.update_data(floor=skipped(message.text))
    await state.set_state(ApartmentForm.description)
    await message.answer(texts.ASK_APARTMENT_DESCRIPTION, reply_markup=kb.cancel_keyboard("m:props"))


@router.message(ApartmentForm.description)
async def add_apartment_description(message: Message, state: FSMContext, db, cfg: Settings) -> None:
    if is_cancel(message.text):
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    data = await state.get_data()
    await state.clear()
    try:
        apartment = await repo.create_apartment(
            db,
            building_id=int(data["building_id"]),
            apartment_number=data.get("apartment_number", ""),
            floor=data.get("floor"),
            description=skipped(message.text),
        )
    except UniqueViolation:
        await message.answer(
            "⚠️ يوجد شقة بنفس الرقم في هذا المبنى. أرسل رقماً آخر عبر القائمة.",
            reply_markup=kb.main_menu(),
        )
        return
    await repo.log_audit(
        db,
        actor_id=cfg.owner_id,
        action="apartment_created",
        entity_type="apartment",
        entity_id=apartment["id"],
        details={"building_id": data.get("building_id"), "number": apartment["apartment_number"]},
    )
    await message.answer("✅ تم إضافة الشقة بنجاح.")
    await message.answer(
        to_html(apartment_text(apartment)),
        reply_markup=kb.apartment_card(apartment),
        parse_mode="HTML",
    )
