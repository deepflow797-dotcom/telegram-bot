"""Per-account policy for destructive actions (delete vs archive).

Both actions are configurable per account from ``⚙️ الإعدادات → 🗑 الحذف والأرشفة``:

* ``allow_delete``  — show/accept ``🗑 حذف نهائياً`` (default on),
* ``allow_archive`` — show/accept ``🗄 أرشفة`` (default on).

``ContextMiddleware`` loads the account's settings once per update and stores the
resolved flags in a :class:`~contextvars.ContextVar`, so keyboard builders can
hide the buttons without touching the database or receiving extra arguments.
Handlers still re-check the policy before acting (defence in depth).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from typing import Any

ALLOW_DELETE = "allow_delete"
ALLOW_ARCHIVE = "allow_archive"


def _flag(settings: Mapping[str, Any], key: str, default: str = "true") -> bool:
    return str(settings.get(key, default)).strip().lower() in {"true", "1", "yes", "on"}


def from_settings(settings: Mapping[str, Any]) -> dict[str, bool]:
    """Resolve the policy dict from an ``app_settings`` mapping."""
    return {
        ALLOW_DELETE: _flag(settings, ALLOW_DELETE),
        ALLOW_ARCHIVE: _flag(settings, ALLOW_ARCHIVE),
    }


_ENABLED: dict[str, bool] = {ALLOW_DELETE: True, ALLOW_ARCHIVE: True}
_current: ContextVar[dict[str, bool]] = ContextVar("bot_policy", default=_ENABLED)


def set_policy(policy: dict[str, bool]) -> Token:
    return _current.set(policy)


def reset_policy(token: Token) -> None:
    _current.reset(token)


def allow_delete() -> bool:
    return _current.get()[ALLOW_DELETE]


def allow_archive() -> bool:
    return _current.get()[ALLOW_ARCHIVE]
