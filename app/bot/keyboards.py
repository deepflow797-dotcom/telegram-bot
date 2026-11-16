"""Keyboard builders. All navigation is button based — no commands required."""

from __future__ import annotations

from decimal import Decimal

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.formatting import format_money, status_label
from app.core.rent import TenantState

BTN_PROPERTIES = "🏠 إدارة العقارات"
BTN_TENANTS = "👥 المستأجرون"
BTN_PAY = "💰 تسجيل الدفع"
BTN_REPORTS = "📊 التقارير"
BTN_SEARCH = "🔎 البحث"
BTN_SETTINGS = "⚙️ الإعدادات"

TENANTS_PAGE_SIZE = 8
PAY_TENANTS_PAGE_SIZE = 8


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PROPERTIES), KeyboardButton(text=BTN_TENANTS)],
            [KeyboardButton(text=BTN_PAY), KeyboardButton(text=BTN_REPORTS)],
            [KeyboardButton(text=BTN_SEARCH), KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="اختر من القائمة…",
    )


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def main_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🏠 إدارة العقارات", "m:props"), _btn("👥 المستأجرون", "m:tenants")],
            [_btn("💰 تسجيل الدفع", "m:pay"), _btn("📊 التقارير", "m:reports")],
            [_btn("🔎 البحث", "m:search"), _btn("⚙️ الإعدادات", "m:settings")],
        ]
    )


def back_only(target: str = "m:main", label: str = "🔙 رجوع") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(label, target)]])


def cancel_keyboard(target: str = "m:main", label: str = "✖️ إلغاء") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(label, target)]])


def properties_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🏢 المباني", "b:list"), _btn("🚪 الشقق", "a:list")],
            [_btn("➕ إضافة مبنى", "b:add"), _btn("➕ إضافة شقة", "a:add")],
            [_btn("🔙 القائمة الرئيسية", "m:main")],
        ]
    )


def tenants_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("👥 جميع المستأجرين", "t:list:0"), _btn("➕ إضافة مستأجر", "t:add")],
            [_btn("⚠️ المتأخرون", "t:overdue"), _btn("⏳ المستحقون قريباً", "t:upcoming")],
            [_btn("🔎 البحث عن مستأجر", "m:search")],
            [_btn("🔙 القائمة الرئيسية", "m:main")],
        ]
    )


def reports_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📅 تقرير اليوم", "r:today"), _btn("📆 تقرير الشهر", "r:month")],
            [_btn("🧾 سجل المدفوعات", "r:history"), _btn("⚠️ المتأخرات", "r:overdue")],
            [_btn("💰 إجمالي الإيرادات", "r:revenue"), _btn("📤 إرسال التقرير اليومي", "r:send")],
            [_btn("🔙 القائمة الرئيسية", "m:main")],
        ]
    )


def settings_menu(currency: str, report_time: str, report_enabled: bool) -> InlineKeyboardMarkup:
    state = "✅ مفعّل" if report_enabled else "⛔️ متوقف"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(f"💱 العملة الافتراضية: {currency}", "s:currency")],
            [_btn(f"⏰ وقت التقرير: {report_time}", "s:time")],
            [_btn(f"🔔 التقرير اليومي: {state}", "s:report")],
            [_btn("ℹ️ معلومات البوت", "s:info")],
            [_btn("🔙 القائمة الرئيسية", "m:main")],
        ]
    )


def currency_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("أوقية MRU", "s:setcur:MRU"), _btn("دولار USD", "s:setcur:USD")],
            [_btn("يورو EUR", "s:setcur:EUR"), _btn("ريال SAR", "s:setcur:SAR")],
            [_btn("درهم AED", "s:setcur:AED"), _btn("✏️ عملة أخرى", "s:customcur")],
            [_btn("🔙 رجوع", "m:settings")],
        ]
    )


def buildings_list(buildings: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for building in buildings:
        count = building.get("apartments_count", 0)
        builder.row(
            _btn(
                f"🏢 {building['name']} — {building.get('occupied_count', 0)}/{count} مشغولة",
                f"b:view:{building['id']}",
            )
        )
    builder.row(_btn("➕ إضافة مبنى", "b:add"), _btn("🔙 رجوع", "m:props"))
    return builder.as_markup()


def building_card(building_id: int, is_active: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [_btn("🚪 شقق المبنى", f"b:apts:{building_id}"), _btn("➕ إضافة شقة", f"b:addapt:{building_id}")],
        [_btn("✏️ تعديل الاسم", f"b:edit:name:{building_id}"), _btn("📍 تعديل العنوان", f"b:edit:address:{building_id}")],
        [_btn("📝 تعديل الوصف", f"b:edit:description:{building_id}")],
    ]
    if is_active:
        rows.append([_btn("🗄 أرشفة المبنى", f"b:arch:{building_id}")])
    rows.append([_btn("🔙 رجوع", "b:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def apartments_list(apartments: list[dict], back: str = "m:props") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for apartment in apartments:
        occupant = apartment.get("tenant_name")
        mark = "🟢" if occupant else "⚪️"
        label = f"{mark} {apartment['apartment_number']} — {apartment.get('building_name', '')}"
        builder.row(_btn(label, f"a:view:{apartment['id']}"))
    builder.row(_btn("➕ إضافة شقة", "a:add"), _btn("🔙 رجوع", back))
    return builder.as_markup()


def apartment_card(apartment: dict) -> InlineKeyboardMarkup:
    rows = []
    if apartment.get("tenant_id"):
        rows.append([_btn("👤 ملف المستأجر", f"t:view:{apartment['tenant_id']}")])
    else:
        rows.append([_btn("➕ إضافة مستأجر لهذه الشقة", f"t:add:{apartment['id']}")])
    rows.append(
        [
            _btn("✏️ تعديل الرقم", f"a:edit:number:{apartment['id']}"),
            _btn("🏗 تعديل الطابق", f"a:edit:floor:{apartment['id']}"),
        ]
    )
    rows.append([_btn("📝 تعديل الوصف", f"a:edit:description:{apartment['id']}")])
    if apartment.get("is_active"):
        rows.append([_btn("🗄 أرشفة الشقة", f"a:arch:{apartment['id']}")])
    rows.append([_btn("🔙 رجوع", f"b:apts:{apartment['building_id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buildings_picker(buildings: list[dict], prefix: str, back: str = "b:list") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for building in buildings:
        builder.row(_btn(f"🏢 {building['name']}", f"{prefix}:{building['id']}"))
    builder.row(_btn("🔙 رجوع", back))
    return builder.as_markup()


def vacant_apartments_picker(apartments: list[dict], back: str = "t:add") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for apartment in apartments:
        builder.row(
            _btn(
                f"🚪 {apartment['building_name']} — شقة {apartment['apartment_number']}",
                f"t:apt:{apartment['id']}",
            )
        )
    builder.row(_btn("🔙 رجوع", back))
    return builder.as_markup()


def tenant_list(
    tenants: list[dict],
    *,
    page: int,
    has_next: bool,
    back: str = "m:tenants",
    prefix: str = "t:view",
    page_prefix: str = "t:list",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tenant in tenants:
        builder.row(
            _btn(
                f"👤 {tenant['full_name']} — {tenant.get('apartment_number', '')}",
                f"{prefix}:{tenant['id']}",
            )
        )
    nav = []
    if page > 0:
        nav.append(_btn("⬅️ السابق", f"{page_prefix}:{page - 1}"))
    if has_next:
        nav.append(_btn("التالي ➡️", f"{page_prefix}:{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(_btn("➕ إضافة مستأجر", "t:add"), _btn("🔙 رجوع", back))
    return builder.as_markup()


def tenant_profile(tenant: dict) -> InlineKeyboardMarkup:
    tenant_id = tenant["id"]
    rows = [
        [_btn("💰 تسجيل الدفع", f"pay:sel:{tenant_id}"), _btn("📄 سجل المدفوعات", f"t:hist:{tenant_id}")],
        [_btn("✏️ تعديل البيانات", f"t:edit:{tenant_id}"), _btn("🔙 رجوع", "t:list:0")],
    ]
    if tenant.get("is_active"):
        rows.insert(2, [_btn("🗄 أرشفة المستأجر", f"t:arch:{tenant_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


EDITABLE_TENANT_FIELDS = [
    ("full_name", "الاسم"),
    ("phone", "الهاتف"),
    ("monthly_rent", "الإيجار"),
    ("currency", "العملة"),
    ("rental_start_date", "تاريخ البداية"),
    ("notes", "الملاحظات"),
    ("apartment_id", "الشقة"),
]


def tenant_edit_menu(tenant_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for field, label in EDITABLE_TENANT_FIELDS:
        builder.row(_btn(f"✏️ {label}", f"t:field:{field}:{tenant_id}"))
    builder.row(_btn("🔙 رجوع", f"t:view:{tenant_id}"))
    return builder.as_markup()


def payment_tenant_picker(
    tenants: list[dict],
    states: dict[int, TenantState],
    *,
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tenant in tenants:
        state = states.get(tenant["id"])
        due = format_money(state.required_to_close, state.currency) if state else ""
        builder.row(_btn(f"👤 {tenant['full_name']} — {due}", f"pay:sel:{tenant['id']}"))
    nav = []
    if page > 0:
        nav.append(_btn("⬅️ السابق", f"pay:list:{page - 1}"))
    if has_next:
        nav.append(_btn("التالي ➡️", f"pay:list:{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(_btn("🔙 رجوع", "m:main"))
    return builder.as_markup()


def confirm_payment(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ تأكيد وتسجيل الدفعة", f"p:ok:{pending_id}")],
            [_btn("✖️ إلغاء", "p:no")],
        ]
    )


def confirm_archive(kind: str, entity_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ نعم، أرشف", f"{kind}:archok:{entity_id}"), _btn("✖️ إلغاء", f"{kind}:view:{entity_id}")],
        ]
    )


def payment_method_note(state: TenantState) -> str:
    return f"{format_money(state.required_to_close, state.currency)} — {status_label(state.status)}"


def generic_rows(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(text, cb) for text, cb in row] for row in rows]
    )


def amount_hint(amount: Decimal, currency: str) -> str:
    return format_money(amount, currency)
