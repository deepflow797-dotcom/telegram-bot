"""In-memory Telegram session for tests (no network, records every call)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Message, WebhookInfo

BASE_DATE = int(dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.UTC).timestamp())


def _message(text: str | None, chat_id: int = 0) -> Message:
    return Message.model_validate(
        {
            "message_id": 1,
            "date": BASE_DATE,
            "chat": {"id": chat_id or 1, "type": "private"},
            "text": text or "",
        }
    )


class FakeSession(BaseSession):
    """Drop-in replacement for AiohttpSession."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def close(self) -> None:  # pragma: no cover - nothing to close
        return None

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        yield b""

    def sent_texts(self) -> list[str]:
        """Every text sent to the chat, whether by sendMessage or editMessageText."""
        return [
            str(call[1].get("text", ""))
            for call in self.calls
            if call[0] in {"SendMessage", "EditMessageText"}
        ]

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        name = type(method).__name__
        payload = method.model_dump(exclude_none=True)
        self.calls.append((name, payload))
        chat_id = payload.get("chat_id", 0)

        if name == "SendMessage":
            return _message(payload.get("text"), chat_id if isinstance(chat_id, int) else 0)
        if name in {"EditMessageText", "EditMessageReplyMarkup"}:
            return _message(payload.get("text"), chat_id if isinstance(chat_id, int) else 0)
        if name == "GetWebhookInfo":
            return WebhookInfo.model_validate(
                {
                    "url": payload.get("url", "https://example.test/hook"),
                    "has_custom_certificate": False,
                    "pending_update_count": 0,
                }
            )
        return True


def attach_fake_session(app) -> FakeSession:
    """Swap the app's real Telegram session for the fake one."""
    session = FakeSession()
    app.state.runtime.bot.session = session
    return session
