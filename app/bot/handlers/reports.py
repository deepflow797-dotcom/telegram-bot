"""Reports: daily, monthly, history, overdue, upcoming and revenue."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.utils import (
    answer_callback,
    edit_or_send,
    now as now_of,
    send_long,
    today as today_of,
)
from app.config import Settings
from app.core.dates import month_bounds
from app.core.reports import (
    render_daily_report,
    render_monthly_report,
    render_overdue_report,
    render_payments_list,
    render_revenue_report,
    render_upcoming_report,
    to_report_tenant,
)
from app.core.rent import TenantStatus
from app.db import payments as payments_repo
from app.services.daily_report import keyboard_for_block, run_daily_report
from app.services.reports_data import (
    build_daily_report_for,
    build_monthly_report_for,
    revenue_totals,
    tenant_states,
)

logger = logging.getLogger(__name__)
router = Router(name="reports")


@router.callback_query(F.data == "r:today")
async def daily_report_now(callback: CallbackQuery, db, cfg: Settings, app_settings: dict) -> None:
    await answer_callback(callback, "جارٍ إعداد التقرير…")
    today = today_of(cfg)
    upcoming = int(app_settings.get("upcoming_days", "3") or 3)
    report = await build_daily_report_for(
        db, tz=cfg.tz, report_date=today, now=now_of(cfg), upcoming_days=upcoming
    )
    blocks = render_daily_report(report)
    message = callback.message
    for block in blocks:
        if isinstance(message, Message):
            await send_long(message, block.text, keyboard_for_block(block))


@router.callback_query(F.data == "r:send")
async def send_daily_report(callback: CallbackQuery, db, cfg: Settings, app_settings: dict) -> None:
    """Manual trigger of the official daily report (idempotent per date)."""
    await answer_callback(callback, "جارٍ إرسال التقرير…")
    chat_id = callback.message.chat.id if isinstance(callback.message, Message) else cfg.owner_id

    async def sender(text: str, keyboard=None) -> None:
        from app.bot.utils import make_sender

        bot = callback.bot
        await make_sender(bot, chat_id)(text, keyboard)

    result = await run_daily_report(
        db,
        tz=cfg.tz,
        report_date=today_of(cfg),
        now=now_of(cfg),
        send=sender,
        upcoming_days=int(app_settings.get("upcoming_days", "3") or 3),
    )
    if result.status == "sent":
        await callback.message.answer("✅ تم إرسال تقرير اليوم.") if isinstance(
            callback.message, Message
        ) else None
    elif result.status == "skipped":
        reason = {
            "already_sent": "تم إرسال تقرير اليوم مسبقاً.",
            "in_progress": "التقرير قيد الإرسال حالياً.",
            "retry_limit": "تم استنفاد محاولات إرسال تقرير اليوم.",
        }.get(result.reason, "لم يتم إرسال التقرير.")
        await answer_callback(callback, reason, alert=True)
    else:
        await answer_callback(callback, "⚠️ فشل إرسال التقرير. راجع السجلات.", alert=True)


@router.callback_query(F.data == "r:month")
async def monthly_report(callback: CallbackQuery, db, cfg: Settings, app_settings: dict) -> None:
    await answer_callback(callback, "جارٍ إعداد تقرير الشهر…")
    today = today_of(cfg)
    month_start, _ = month_bounds(today)
    report = await build_monthly_report_for(
        db,
        tz=cfg.tz,
        month_start=month_start,
        today=today,
        upcoming_days=int(app_settings.get("upcoming_days", "3") or 3),
    )
    message = callback.message
    if isinstance(message, Message):
        await send_long(message, "\n".join(render_monthly_report(report)), kb.back_only("m:reports"))


@router.callback_query(F.data == "r:history")
async def payments_history(callback: CallbackQuery, db) -> None:
    payments = await payments_repo.list_payments(db, limit=20)
    await answer_callback(callback)
    await edit_or_send(
        callback,
        "\n".join(render_payments_list(payments, "سجل المدفوعات")),
        kb.back_only("m:reports"),
    )


@router.callback_query(F.data == "r:overdue")
async def overdue_report(callback: CallbackQuery, db, cfg: Settings, app_settings: dict) -> None:
    await answer_callback(callback)
    today = today_of(cfg)
    states = await tenant_states(
        db, today=today, upcoming_days=int(app_settings.get("upcoming_days", "3") or 3)
    )
    entries = [
        to_report_tenant(row, st) for row, st in states if st.status is TenantStatus.OVERDUE
    ]
    await edit_or_send(callback, "\n".join(render_overdue_report(entries, today)), kb.back_only("m:reports"))


@router.callback_query(F.data == "r:upcoming")
async def upcoming_report(callback: CallbackQuery, db, cfg: Settings, app_settings: dict) -> None:
    await answer_callback(callback)
    today = today_of(cfg)
    days = int(app_settings.get("upcoming_days", "3") or 3)
    states = await tenant_states(db, today=today, upcoming_days=days)
    entries = [
        to_report_tenant(row, st)
        for row, st in states
        if st.status in (TenantStatus.UPCOMING, TenantStatus.DUE_TODAY)
    ]
    await edit_or_send(
        callback, "\n".join(render_upcoming_report(entries, today, days)), kb.back_only("m:reports")
    )


@router.callback_query(F.data == "r:revenue")
async def revenue_report(callback: CallbackQuery, db, cfg: Settings) -> None:
    await answer_callback(callback)
    data = await revenue_totals(db, tz=cfg.tz, today=today_of(cfg))
    text = "\n".join(
        render_revenue_report(
            today_total=data["today"],
            month_total=data["month"],
            year_total=data["year"],
            all_time_total=data["all_time"],
            payments_count=int(data["payments_count"]),
        )
    )
    await edit_or_send(callback, text, kb.back_only("m:reports"))


@router.callback_query(F.data == "r:upcoming_from_report")
async def noop(callback: CallbackQuery) -> None:
    await answer_callback(callback)
