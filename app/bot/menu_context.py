"""Whether the current update comes from the platform owner (menu extras)."""

from __future__ import annotations

from contextvars import ContextVar, Token

_owner_menu: ContextVar[bool] = ContextVar("owner_menu", default=False)


def owner_menu() -> bool:
    return _owner_menu.get()


def set_owner_menu(value: bool) -> Token:
    return _owner_menu.set(bool(value))


def reset_owner_menu(token: Token) -> None:
    try:
        _owner_menu.reset(token)
    except ValueError:  # different context
        pass
