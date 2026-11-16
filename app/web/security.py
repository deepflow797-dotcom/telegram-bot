"""Request authentication for the webhook and cron endpoints."""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def telegram_secret_matches(request: Request, expected: str) -> bool:
    provided = request.headers.get(TELEGRAM_SECRET_HEADER) or ""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def require_telegram_secret(request: Request, expected: str) -> None:
    if not telegram_secret_matches(request, expected):
        logger.warning(
            "webhook rejected: bad secret token",
            extra={"extra_fields": {"path": request.url.path}},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook secret"
        )


def _cron_candidates(request: Request) -> list[str]:
    values: list[str] = []
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        values.append(authorization[7:].strip())
    header_value = request.headers.get("x-cron-secret")
    if header_value:
        values.append(header_value.strip())
    return values


def require_cron_secret(request: Request, expected: str) -> None:
    """Accept ``Authorization: Bearer <CRON_SECRET>`` (Vercel Cron) or X-Cron-Secret."""
    if not expected:
        raise HTTPException(status_code=500, detail="cron secret is not configured")
    for candidate in _cron_candidates(request):
        if hmac.compare_digest(candidate, expected):
            return
    logger.warning(
        "cron rejected: bad or missing secret",
        extra={"extra_fields": {"path": request.url.path}},
    )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid cron secret")
