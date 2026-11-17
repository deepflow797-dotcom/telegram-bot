"""Which account's dataset is the current unit of work allowed to touch?

Every account owns a PostgreSQL schema named ``acct_<id>``. The request that is
being handled sets the active account here, and :class:`app.db.connection.Database`
points ``search_path`` at that schema before running any query — so a handler
physically cannot read another account's rows, even if it forgets a filter.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

#: Account 1 is the platform owner; it is the safe default for migrations,
#: tests and any code path that runs before an account is resolved.
DEFAULT_ACCOUNT_ID = 1

_current: ContextVar[int] = ContextVar("current_account_id", default=DEFAULT_ACCOUNT_ID)


def schema_for(account_id: int) -> str:
    """Schema name for an account (``acct_12``)."""
    return f"acct_{int(account_id)}"


def current_account_id() -> int:
    return _current.get()


def set_current_account(account_id: int | None) -> Token:
    return _current.set(int(account_id or DEFAULT_ACCOUNT_ID))


def reset_current_account(token: Token) -> None:
    try:
        _current.reset(token)
    except ValueError:  # different context — nothing to restore
        pass


@contextmanager
def use_account(account_id: int | None) -> Iterator[int]:
    """Temporarily scope database access to one account."""
    token = set_current_account(account_id)
    try:
        yield current_account_id()
    finally:
        reset_current_account(token)
