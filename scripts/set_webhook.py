#!/usr/bin/env python3
"""Register the Telegram webhook and bot commands.

Usage:
    python scripts/set_webhook.py https://your-app.vercel.app/api/telegram/webhook
    python scripts/set_webhook.py --info
    python scripts/set_webhook.py --delete

Requires TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET and optionally WEBHOOK_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402

from app.bot.dispatcher import BOT_COMMANDS  # noqa: E402
from app.config import ConfigError, Settings  # noqa: E402
from app.web.app import ALLOWED_UPDATES  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    try:
        cfg = Settings.from_env()
    except ConfigError as exc:
        print("Configuration error:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    bot = Bot(cfg.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        if args.delete:
            await bot.delete_webhook(drop_pending_updates=args.drop_pending)
            print("webhook deleted")
            return 0

        if args.info:
            info = await bot.get_webhook_info()
            print(f"url: {info.url or '(none)'}")
            print(f"pending updates: {info.pending_update_count}")
            print(f"last error: {info.last_error_message or '(none)'}")
            return 0

        url = args.url or cfg.webhook_url
        if not url:
            print("error: provide a URL or set WEBHOOK_URL", file=sys.stderr)
            return 2
        if not url.startswith("https://"):
            print("error: Telegram requires an https webhook URL", file=sys.stderr)
            return 2

        await bot.set_webhook(
            url=url,
            secret_token=cfg.telegram_webhook_secret,
            allowed_updates=ALLOWED_UPDATES,
            drop_pending_updates=args.drop_pending,
        )
        await bot.set_my_commands(BOT_COMMANDS)
        info = await bot.get_webhook_info()
        print(f"webhook set to {info.url}")
        print(f"pending updates: {info.pending_update_count}")
        return 0
    finally:
        await bot.session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the Telegram webhook")
    parser.add_argument("url", nargs="?", help="https webhook URL")
    parser.add_argument("--info", action="store_true", help="show webhook status")
    parser.add_argument("--delete", action="store_true", help="remove the webhook")
    parser.add_argument("--drop-pending", action="store_true", help="drop queued updates")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
