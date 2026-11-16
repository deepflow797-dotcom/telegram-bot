"""Restore the original request path after a serverless rewrite.

Vercel (and similar hosts) route every request to a single function file
(``api/index.py``) with a rewrite rule. The runtime then exposes the
*destination* path (``/api/index``) to the ASGI app instead of the path the
client requested, which would make FastAPI answer ``{"detail":"Not Found"}``
for every endpoint.

The rewrite therefore forwards the original path as a query parameter
(``__path``) and :class:`VercelPathMiddleware` puts it back on the ASGI scope
before FastAPI performs routing. The helpers below are pure functions so they
can be unit tested without a running server.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote_plus

#: Query parameter used by ``vercel.json`` to carry the original path.
REWRITE_PARAM = "__path"

#: Paths that mean "the function was invoked directly, not through a rewrite".
_FUNCTION_PATHS = frozenset({"/api", "/api/", "/api/index", "/api/index.py"})


def normalize_path(path: str) -> str:
    """Return a clean absolute path (``""`` -> ``"/"``, no trailing slash)."""
    cleaned = unquote_plus(path or "/").strip()
    if not cleaned:
        return "/"
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    if len(cleaned) > 1:
        cleaned = cleaned.rstrip("/") or "/"
    return cleaned


def split_rewrite_path(query: str) -> tuple[str | None, str]:
    """Split ``__path`` out of a raw query string.

    Returns ``(captured_path_or_None, remaining_query_string)``; the remaining
    query keeps its original encoding and parameter order.
    """
    if not query:
        return None, ""
    captured: str | None = None
    kept: list[str] = []
    for chunk in query.split("&"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        if unquote_plus(key) == REWRITE_PARAM:
            captured = unquote_plus(value)
            continue
        kept.append(chunk)
    return captured, "&".join(kept)


def resolve_request_path(path: str, query: str) -> tuple[str, str]:
    """Resolve the path FastAPI should route on, plus the cleaned query string."""
    captured, rest = split_rewrite_path(query)
    if captured:
        return normalize_path(captured), rest
    if path in _FUNCTION_PATHS:
        # No rewrite parameter and the host exposed the function path: the
        # caller most likely asked for the service root.
        return "/", rest
    return normalize_path(path), rest


class VercelPathMiddleware:
    """ASGI middleware that restores the client path before routing."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            raw_query = scope.get("query_string", b"")
            if isinstance(raw_query, bytes):
                raw_query = raw_query.decode("latin-1")
            path, query = resolve_request_path(scope.get("path", "/"), raw_query)
            scope = dict(scope)
            scope["path"] = path
            scope["raw_path"] = path.encode("latin-1")
            scope["query_string"] = query.encode("latin-1")
        await self.app(scope, receive, send)
