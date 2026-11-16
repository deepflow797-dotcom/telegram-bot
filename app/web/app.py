"""ASGI application: Telegram webhook, daily report endpoint, health checks.

The app is serverless-friendly:
* no long polling, updates are fed to aiogram explicitly,
* conversation state lives in PostgreSQL,
* every request opens connections from a lazily created pool.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.bot.dispatcher import build_bot, create_dispatcher, register_bot_commands
from app.bot.utils import today as today_of
from app.config import ConfigError, Settings
from app.core.dates import parse_date
from app.db import repository as repo
from app.db.connection import Database
from app.db.fsm import purge_stale_states
from app.db.payments import expire_pending_payments
from app.db.reports import get_report_for_date
from app.logging_config import log_event, setup_logging
from app.services.daily_report import run_daily_report
from app.services.reports_data import tenant_states
from app.web.security import require_cron_secret, require_telegram_secret

logger = logging.getLogger(__name__)

ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "my_chat_member",
]


class Runtime:
    """Container for the long-lived objects shared by requests."""

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.db = Database(cfg.database_url)
        self.bot = build_bot(cfg)
        self.dispatcher = create_dispatcher(cfg, self.db)

    async def start(self) -> None:
        await self.db.open()

    async def stop(self) -> None:
        await self.db.close()
        await self.bot.session.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config_error: ConfigError | None = getattr(app.state, "config_error", None)
    runtime: Runtime | None = getattr(app.state, "runtime", None)
    if runtime is not None:
        await runtime.start()
    try:
        yield
    finally:
        if runtime is not None:
            await runtime.stop()
        if config_error is not None:
            logger.error("application started with configuration errors")


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="Rental Management Telegram Bot",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    try:
        cfg = settings or Settings.from_env()
    except ConfigError as exc:
        app.state.config_error = exc
        app.state.runtime = None
        setup_logging("INFO")
        logger.error(
            "configuration is incomplete",
            extra={"extra_fields": {"problems": exc.problems}},
        )
    else:
        app.state.config_error = None
        app.state.runtime = Runtime(cfg)
        setup_logging(cfg.log_level)
        log_event(
            logger,
            "application configured",
            timezone=cfg.app_timezone,
            currency=cfg.default_currency,
            owner_id=cfg.telegram_owner_id,
        )

    @app.get("/")
    async def index(request: Request) -> Any:
        runtime = _runtime_or_error(request)
        report = await get_report_for_date(runtime.db, today_of(runtime.cfg))
        return {
            "service": "rental-management-telegram-bot",
            "status": "ok",
            "timezone": runtime.cfg.app_timezone,
            "today": today_of(runtime.cfg).isoformat(),
            "todays_report": report["status"] if report else None,
            "webhook": "/api/telegram/webhook",
            "cron": "/api/cron/daily-report",
        }

    @app.get("/api/health")
    async def health(request: Request) -> Any:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None:
            error: ConfigError | None = getattr(request.app.state, "config_error", None)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "misconfigured",
                    "problems": error.problems if error else ["unknown configuration error"],
                },
            )
        db_ok = await runtime.db.ping()
        body = {
            "status": "ok" if db_ok else "degraded",
            "database": "up" if db_ok else "down",
            "timezone": runtime.cfg.app_timezone,
            "today": today_of(runtime.cfg).isoformat(),
        }
        return JSONResponse(status_code=200 if db_ok else 503, content=body)

    @app.post("/api/telegram/webhook")
    async def telegram_webhook(request: Request) -> Any:
        runtime = _runtime_or_error(request)
        require_telegram_secret(request, runtime.cfg.telegram_webhook_secret)

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="invalid JSON body") from None

        try:
            update = Update.model_validate(payload, context={"bot": runtime.bot})
        except Exception:  # noqa: BLE001
            logger.warning("webhook rejected: invalid update payload")
            return {"ok": False, "reason": "invalid_update"}

        if update.update_id:
            fresh = await repo.claim_update(runtime.db, update.update_id)
            if not fresh:
                log_event(
                    logger, "duplicate telegram update ignored", update_id=update.update_id
                )
                return {"ok": True, "duplicate": True}

        try:
            await runtime.dispatcher.feed_update(runtime.bot, update)
        except Exception:  # noqa: BLE001 - Telegram must still get 200
            logger.exception("failed to process update")
            return {"ok": False, "reason": "processing_error"}
        return {"ok": True}

    @app.api_route("/api/cron/daily-report", methods=["GET", "POST"])
    async def daily_report_cron(request: Request) -> Any:
        runtime = _runtime_or_error(request)
        require_cron_secret(request, runtime.cfg.cron_secret)

        cfg = runtime.cfg
        settings_map = await repo.get_app_settings(runtime.db)
        report_date = today_of(cfg)
        requested = request.query_params.get("date")
        if requested:
            parsed = parse_date(requested)
            if parsed is None:
                raise HTTPException(status_code=400, detail="invalid date parameter")
            if parsed > report_date:
                raise HTTPException(status_code=400, detail="cannot report a future date")
            report_date = parsed

        force = request.query_params.get("force") == "1"
        if settings_map.get("report_enabled", "true") != "true" and not force:
            return {"status": "disabled", "report_date": report_date.isoformat()}

        from app.bot.utils import make_sender

        send = make_sender(runtime.bot, cfg.owner_id)
        result = await run_daily_report(
            runtime.db,
            tz=cfg.tz,
            report_date=report_date,
            now=dt.datetime.now(cfg.tz),
            send=send,
            upcoming_days=int(settings_map.get("upcoming_days", "3") or 3),
        )

        # housekeeping: cheap, safe, idempotent
        await repo.purge_old_updates(runtime.db, keep_days=7)
        await expire_pending_payments(runtime.db, older_than_hours=48)
        await purge_stale_states(runtime.db, keep_days=7)

        if result.status == "failed":
            _notify_owner_failure(runtime, result)
        return result.as_dict()

    @app.api_route("/api/admin/setup-webhook", methods=["GET", "POST"])
    async def setup_webhook(request: Request) -> Any:
        runtime = _runtime_or_error(request)
        require_cron_secret(request, runtime.cfg.cron_secret)

        url = request.query_params.get("url") or runtime.cfg.webhook_url
        if not url:
            raise HTTPException(
                status_code=400,
                detail="provide ?url=https://<your-deployment>/api/telegram/webhook "
                "or set WEBHOOK_URL",
            )
        if not url.startswith("https://"):
            raise HTTPException(status_code=400, detail="webhook url must be https")

        ok = await runtime.bot.set_webhook(
            url=url,
            secret_token=runtime.cfg.telegram_webhook_secret,
            allowed_updates=ALLOWED_UPDATES,
            drop_pending_updates=False,
        )
        await register_bot_commands(runtime.bot)
        info = await runtime.bot.get_webhook_info()
        log_event(logger, "webhook configured", url=url, ok=bool(ok))
        return {
            "ok": bool(ok),
            "url": info.url,
            "pending_update_count": info.pending_update_count,
            "last_error_message": info.last_error_message,
        }

    @app.get("/api/admin/webhook-info")
    async def webhook_info(request: Request) -> Any:
        runtime = _runtime_or_error(request)
        require_cron_secret(request, runtime.cfg.cron_secret)
        info = await runtime.bot.get_webhook_info()
        return {
            "url": info.url,
            "pending_update_count": info.pending_update_count,
            "last_error_date": info.last_error_date,
            "last_error_message": info.last_error_message,
            "allowed_updates": info.allowed_updates,
        }

    @app.get("/api/admin/tenants")
    async def tenants_overview(request: Request) -> Any:
        """Read-only JSON snapshot, handy for verifying data after deployment."""
        runtime = _runtime_or_error(request)
        require_cron_secret(request, runtime.cfg.cron_secret)
        states = await tenant_states(runtime.db, today=today_of(runtime.cfg))
        return {
            "today": today_of(runtime.cfg).isoformat(),
            "tenants": [
                {
                    "id": row["id"],
                    "name": row["full_name"],
                    "building": row["building_name"],
                    "apartment": row["apartment_number"],
                    "rent": str(state.monthly_rent),
                    "currency": state.currency,
                    "next_due": state.next_due.isoformat(),
                    "status": state.status.value,
                    "outstanding": str(state.outstanding),
                }
                for row, state in states
            ],
        }

    return app


def _runtime_or_error(request: Request) -> Runtime:
    runtime: Runtime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:
        error: ConfigError | None = getattr(request.app.state, "config_error", None)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "configuration_incomplete",
                "problems": error.problems if error else ["unknown configuration error"],
            },
        )
    return runtime


def _notify_owner_failure(runtime: Runtime, result: Any) -> None:
    """Best effort owner alert; never raises."""
    import asyncio

    async def _send() -> None:
        try:
            await runtime.bot.send_message(
                runtime.cfg.owner_id,
                "⚠️ تعذر إرسال التقرير اليومي.\n"
                f"التاريخ: {result.report_date.isoformat()}\n"
                "سيتم إعادة المحاولة في التشغيل القادم. راجع سجلات الخادم.",
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not notify owner about failed report")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send())
    except RuntimeError:  # pragma: no cover
        pass


def summarise_settings(cfg: Settings) -> dict[str, Any]:
    """Safe configuration summary (no secrets) for logs and diagnostics."""
    safe = dataclasses.asdict(cfg)
    for key in list(safe):
        if "secret" in key or "token" in key or "key" in key or "url" in key and "database" in key:
            safe[key] = "***" if safe[key] else None
    return safe
