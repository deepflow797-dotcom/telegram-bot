#!/usr/bin/env python3
"""Send today's daily report from a scheduler that cannot call HTTP endpoints.

Alternative to /api/cron/daily-report (e.g. systemd timer, Render cron job):

    python scripts/send_daily_report.py
    python scripts/send_daily_report.py --date 2026-11-15

Idempotent: running it twice on the same date sends only one report.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402

from app.bot.utils import make_sender  # noqa: E402
from app.config import ConfigError, Settings  # noqa: E402
from app.core.dates import parse_date  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.db.connection import Database  # noqa: E402
from app.services.daily_report import run_daily_report  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    try:
        cfg = Settings.from_env()
    except ConfigError as exc:
        print("Configuration error:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    db = Database(cfg.database_url, min_size=1, max_size=2)
    bot = Bot(cfg.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await db.open()
        settings = await repo.get_app_settings(db)
        report_date = parse_date(args.date) if args.date else dt.datetime.now(cfg.tz).date()
        if report_date is None:
            print("error: invalid --date", file=sys.stderr)
            return 2
        result = await run_daily_report(
            db,
            tz=cfg.tz,
            report_date=report_date,
            now=dt.datetime.now(cfg.tz),
            send=make_sender(bot, cfg.owner_id),
            upcoming_days=int(settings.get("upcoming_days", "3") or 3),
        )
        print(result.as_dict())
        return 0 if result.status in {"sent", "skipped"} else 1
    finally:
        await db.close()
        await bot.session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Send the daily rental report")
    parser.add_argument("--date", help="report date (YYYY-MM-DD), defaults to today")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
