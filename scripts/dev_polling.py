#!/usr/bin/env python3
"""Run the bot with long polling for LOCAL testing only.

Production uses the webhook (see README). Polling is convenient while trying
the bot on your machine because it needs no public URL.

    python scripts/dev_polling.py

It removes any registered webhook first (Telegram forbids polling + webhook at
the same time). Run scripts/set_webhook.py afterwards to go back to webhook mode.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bot.dispatcher import build_bot, create_dispatcher, register_bot_commands  # noqa: E402
from app.config import ConfigError, Settings  # noqa: E402
from app.db.connection import Database  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402

logger = logging.getLogger("dev_polling")


async def main() -> int:
    try:
        cfg = Settings.from_env()
    except ConfigError as exc:
        print("Configuration error:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    setup_logging(cfg.log_level)
    db = Database(cfg.database_url, min_size=1, max_size=4)
    await db.open()
    bot = build_bot(cfg)
    dp = create_dispatcher(cfg, db)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await register_bot_commands(bot)
        logger.info("polling started — press CTRL+C to stop")
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:  # pragma: no cover
        raise SystemExit(130) from None
