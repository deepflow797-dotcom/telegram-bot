"""PostgreSQL-backed FSM storage.

Keep the aiogram conversation state in the database instead of process memory:
serverless platforms may answer the next update from a different instance, and
a restart must never leave the owner stuck mid-form.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from psycopg.types.json import Jsonb

from app.db.connection import Database


def _key_id(bot: Any) -> str:
    return ":".join(
        [
            str(bot.bot_id),
            str(bot.chat_id),
            str(bot.user_id),
            str(bot.thread_id or 0),
            str(bot.business_connection_id or "-"),
            str(bot.destiny),
        ]
    )


class PostgresStorage(BaseStorage):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def set_state(self, key: StorageKey, state: State | str | None = None) -> None:
        if isinstance(state, State):
            state = state.state
        await self.db.execute(
            """
            INSERT INTO bot_fsm_states (key, chat_id, user_id, state, data, updated_at)
            VALUES (%s, %s, %s, %s, '{}'::jsonb, now())
            ON CONFLICT (key) DO UPDATE
                SET state = EXCLUDED.state, updated_at = now()
            """,
            (_key_id(key), key.chat_id, key.user_id, state),
        )
        if state is None:
            await self.db.execute(
                "DELETE FROM bot_fsm_states WHERE key = %s AND data = '{}'::jsonb",
                (_key_id(key),),
            )

    async def get_state(self, key: StorageKey) -> str | None:
        value = await self.db.fetch_value(
            "SELECT state FROM bot_fsm_states WHERE key = %s", (_key_id(key),)
        )
        return value

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(data), ensure_ascii=False, default=str)
        await self.db.execute(
            """
            INSERT INTO bot_fsm_states (key, chat_id, user_id, data, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (key) DO UPDATE
                SET data = EXCLUDED.data, updated_at = now()
            """,
            (_key_id(key), key.chat_id, key.user_id, Jsonb(json.loads(payload))),
        )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await self.db.fetch_one(
            "SELECT data FROM bot_fsm_states WHERE key = %s", (_key_id(key),)
        )
        if not row or not row.get("data"):
            return {}
        return dict(row["data"])

    async def close(self) -> None:  # pragma: no cover - nothing to close
        return None


async def purge_stale_states(db: Database, keep_days: int = 7) -> int:
    return await db.execute(
        "DELETE FROM bot_fsm_states WHERE updated_at < now() - (%s || ' days')::interval",
        (keep_days,),
    )
