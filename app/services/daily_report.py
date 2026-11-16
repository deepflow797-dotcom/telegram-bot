"""Daily report orchestration: claim → build → deliver → record.

Delivery is idempotent per report date (see ``app.db.reports.claim_daily_report``)
and never touches payment or rental cycle data.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.reports import MessageBlock, render_daily_report
from app.db import reports as reports_repo
from app.db.connection import Database
from app.logging_config import log_event
from app.services.reports_data import build_daily_report_for

logger = logging.getLogger(__name__)

Sender = Callable[[str, InlineKeyboardMarkup | None], Awaitable[None]]


@dataclass
class ReportRunResult:
    status: str  # "sent" | "skipped" | "failed"
    report_date: dt.date
    reason: str
    messages_sent: int = 0
    summary: dict | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "report_date": self.report_date.isoformat(),
            "reason": self.reason,
            "messages_sent": self.messages_sent,
            "error": self.error,
        }


def keyboard_for_block(block: MessageBlock) -> InlineKeyboardMarkup | None:
    """Inline buttons for the tenants listed in a report block."""
    if not block.tenants:
        return None
    builder = InlineKeyboardBuilder()
    for tenant in block.tenants[:8]:
        label = tenant.name if len(tenant.name) <= 22 else tenant.name[:21] + "…"
        builder.button(text=f"👤 {label}", callback_data=f"ten:{tenant.tenant_id}")
    for tenant in block.tenants[:4]:
        label = tenant.name if len(tenant.name) <= 18 else tenant.name[:17] + "…"
        builder.button(text=f"💰 دفعة {label}", callback_data=f"pay:sel:{tenant.tenant_id}")
    builder.adjust(1, 2, 2, 1, 2, 2)
    return builder.as_markup()


async def run_daily_report(
    db: Database,
    *,
    tz,
    report_date: dt.date,
    now: dt.datetime,
    send: Sender,
    upcoming_days: int = 3,
) -> ReportRunResult:
    claim = await reports_repo.claim_daily_report(db, report_date)
    if not claim["claimed"]:
        log_event(
            logger,
            "daily report skipped",
            report_date=report_date.isoformat(),
            reason=claim["reason"],
            report_id=claim["report_id"],
        )
        return ReportRunResult(
            status="skipped", report_date=report_date, reason=str(claim["reason"])
        )

    report_id = claim["report_id"]
    sent_messages = 0
    try:
        report = await build_daily_report_for(
            db, tz=tz, report_date=report_date, now=now, upcoming_days=upcoming_days
        )
        blocks = render_daily_report(report)
        for block in blocks:
            await send(block.text, keyboard_for_block(block))
            sent_messages += 1
        summary = report.summary_dict()
        await reports_repo.mark_report_sent(db, report_id, summary)
        log_event(
            logger,
            "daily report sent",
            report_date=report_date.isoformat(),
            messages=sent_messages,
            active_tenants=report.active_tenants,
        )
        return ReportRunResult(
            status="sent",
            report_date=report_date,
            reason=str(claim["reason"]),
            messages_sent=sent_messages,
            summary=summary,
        )
    except Exception as exc:  # noqa: BLE001 - must never break the caller
        logger.exception("daily report failed")
        await reports_repo.mark_report_failed(db, report_id, f"{type(exc).__name__}: {exc}")
        return ReportRunResult(
            status="failed",
            report_date=report_date,
            reason=str(claim["reason"]),
            messages_sent=sent_messages,
            error=f"{type(exc).__name__}: {exc}",
        )
