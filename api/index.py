"""Vercel serverless entrypoint (also works with any ASGI host).

Two deployment realities are handled here:

* the repository root must be importable, otherwise ``import app.*`` fails on
  hosts that only copy ``api/`` into the function bundle;
* Vercel rewrites every request to this single function, so the original path
  is restored by :class:`app.web.routing.VercelPathMiddleware` (see
  ``vercel.json``).

If the application cannot even be imported (missing dependency, bad
configuration, ...) the function still answers with a JSON error instead of an
opaque ``500 FUNCTION_INVOCATION_FAILED``, so the deployment can be diagnosed
from the outside. Secrets are redacted from that message.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any

# Make the repository root importable regardless of the host's working dir.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SECRET_PATTERNS = (
    re.compile(r"(bot\d{6,}:[A-Za-z0-9_-]{20,})"),
    re.compile(r"(postgres(?:ql)?://[^:\s]+:)([^@\s]+)(@)"),
)


def _scrub(text: str) -> str:
    """Remove credentials from a traceback before it leaves the process."""
    cleaned = _SECRET_PATTERNS[0].sub("bot<redacted>", text)
    return _SECRET_PATTERNS[1].sub(r"\1***\3", cleaned)


def _build_application() -> Any:
    from app.web.app import create_app
    from app.web.routing import VercelPathMiddleware

    return VercelPathMiddleware(create_app())


try:
    application: Any = _build_application()
    startup_error: str | None = None
except Exception:  # noqa: BLE001 - a broken boot must still produce a response
    application = None
    startup_error = _scrub(traceback.format_exc())
    print(startup_error, file=sys.stderr, flush=True)


async def _diagnostic_app(scope: dict, receive: Any, send: Any) -> None:
    """Minimal ASGI app used when the real application failed to boot."""
    if scope.get("type") != "http":
        return
    body = json.dumps(
        {
            "status": "error",
            "error": "startup_failed",
            "detail": startup_error,
        },
        ensure_ascii=False,
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


app = application if application is not None else _diagnostic_app
