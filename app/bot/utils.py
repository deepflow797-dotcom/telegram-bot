"""Small helpers shared by the handlers."""

from __future__ import annotations

import datetime as dt
import html
import re

from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot import texts
from app.config import Settings
from app.db.repository import report_time, upcoming_days

MAX_TG_LEN = 3800

_BOLD_RE = re.compile(r"\*([^*\n]{1,200})\*")


def to_html(text: str) -> str:
    """Escape user data and turn ``*bold*`` markers into HTML ``<b>`` tags.

    Escaping happens first, so tenant names, notes and other free text can
    never break Telegram's entity parser.
    """
    escaped = html.escape(text or "", quote=False)
    return _BOLD_RE.sub(r"<b>\1</b>", escaped)



def today(cfg: Settings) -> dt.date:
    return dt.datetime.now(cfg.tz).date()


def now(cfg: Settings) -> dt.datetime:
    return dt.datetime.now(cfg.tz)


def upcoming(settings: dict[str, str]) -> int:
    return upcoming_days(settings)


def daily_report_time(settings: dict[str, str]) -> tuple[int, int]:
    return report_time(settings)


def split_message(text: str, limit: int = MAX_TG_LEN) -> list[str]:
    """Split long text on line boundaries so Telegram never rejects it."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # pathological single line
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def send_long(
    message: Message,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    chunks = split_message(text)
    for index, chunk in enumerate(chunks):
        await message.answer(
            to_html(chunk),
            reply_markup=keyboard if index == len(chunks) - 1 else None,
            parse_mode="HTML",
        )


async def edit_or_send(
    callback: CallbackQuery,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit the callback message when possible, otherwise send a new one."""
    chunks = split_message(text)
    message = callback.message
    try:
        if isinstance(message, Message):
            await message.edit_text(
                to_html(chunks[0]), reply_markup=keyboard, parse_mode="HTML"
            )
            for chunk in chunks[1:]:
                await message.answer(to_html(chunk), parse_mode="HTML")
            return
    except Exception:  # noqa: BLE001 - message may be too old/identical
        pass
    if isinstance(message, Message):
        await send_long(message, text, keyboard)


def make_sender(bot: Bot, chat_id: int):
    """Build the ``send(text, keyboard)`` callable used by services."""

    async def _send(text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
        chunks = split_message(text)
        for index, chunk in enumerate(chunks):
            await bot.send_message(
                chat_id,
                to_html(chunk),
                reply_markup=keyboard if index == len(chunks) - 1 else None,
                parse_mode="HTML",
            )

    return _send


async def answer_callback(callback: CallbackQuery, text: str = "", alert: bool = False) -> None:
    try:
        await callback.answer(text=text, show_alert=alert)
    except Exception:  # noqa: BLE001 - callback may have expired
        pass


def parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def fmt_optional(value: str | None) -> str:
    return value.strip() if value and value.strip() and value.strip() != "-" else "—"


def skipped(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text in {"-", "—", "تخطي", "لا"}:
        return None
    return text


CANCEL_TEXT = "✖️ إلغاء"


def is_cancel(text: str | None) -> bool:
    return (text or "").strip() in {"/cancel", "/إلغاء", "إلغاء", CANCEL_TEXT, "الغاء"}


NOT_AUTHORIZED_TEXT = texts.NOT_AUTHORIZED
