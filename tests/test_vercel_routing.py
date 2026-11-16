"""Tests for the serverless rewrite handling (Vercel routes everything to one file)."""

from __future__ import annotations

import httpx
import pytest

from app.web.app import create_app
from app.web.routing import (
    VercelPathMiddleware,
    normalize_path,
    resolve_request_path,
    split_rewrite_path,
)

SECRET_ENV = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_OWNER_ID",
    "TELEGRAM_WEBHOOK_SECRET",
    "DATABASE_URL",
    "CRON_SECRET",
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "/"),
        ("/", "/"),
        ("api/health", "/api/health"),
        ("/api/health/", "/api/health"),
        ("%2Fapi%2Fhealth", "/api/health"),
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected


def test_split_rewrite_path_extracts_parameter() -> None:
    captured, rest = split_rewrite_path("__path=/api/health&x=1")
    assert captured == "/api/health"
    assert rest == "x=1"


def test_split_rewrite_path_without_parameter() -> None:
    assert split_rewrite_path("a=1&b=2") == (None, "a=1&b=2")
    assert split_rewrite_path("") == (None, "")


def test_split_rewrite_path_decodes_value() -> None:
    captured, rest = split_rewrite_path("__path=%2Fapi%2Ftelegram%2Fwebhook")
    assert captured == "/api/telegram/webhook"
    assert rest == ""


def test_resolve_request_path_prefers_rewrite_parameter() -> None:
    assert resolve_request_path("/api/index", "__path=/api/health") == ("/api/health", "")


def test_resolve_request_path_strips_function_path() -> None:
    # Direct invocation of the function file must land on the service root.
    assert resolve_request_path("/api/index", "") == ("/", "")
    assert resolve_request_path("/api", "") == ("/", "")


def test_resolve_request_path_keeps_original_path() -> None:
    assert resolve_request_path("/api/health", "a=1") == ("/api/health", "a=1")
    assert resolve_request_path("/api/telegram/webhook", "") == ("/api/telegram/webhook", "")


async def test_middleware_restores_scope_for_asgi_app() -> None:
    seen: dict = {}

    async def inner(scope: dict, receive, send) -> None:
        seen.update(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    awaited = VercelPathMiddleware(inner)
    await awaited(
        {
            "type": "http",
            "path": "/api/index",
            "raw_path": b"/api/index",
            "query_string": b"__path=/api/health&x=1",
            "headers": [],
        },
        receive,
        send,
    )

    assert seen["path"] == "/api/health"
    assert seen["raw_path"] == b"/api/health"
    assert seen["query_string"] == b"x=1"
    assert sent[0]["status"] == 204


async def test_rewritten_request_reaches_the_real_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a rewritten request must hit FastAPI's ``/api/health`` route."""
    for name in SECRET_ENV:
        monkeypatch.delenv(name, raising=False)

    app = VercelPathMiddleware(create_app())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        rewritten = await client.get("/api/index?__path=/api/health")
        direct = await client.get("/api/health")

    # Without the middleware both requests would be 404 (path mismatch); with it
    # both reach the health route, which reports the missing configuration.
    assert rewritten.status_code == 503
    assert rewritten.json()["status"] == "misconfigured"
    assert direct.json() == rewritten.json()
