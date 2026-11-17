"""Structured (JSON) logging with secret redaction.

Logs never contain tokens, webhook secrets, cron secrets or database
passwords. Telegram ids and entity ids are kept for traceability.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"(bot\d{6,}:[A-Za-z0-9_-]{20,})"),
    re.compile(r"(postgres(?:ql)?://[^:\s]+:)([^@\s]+)(@)"),
    re.compile(r"((?:token|secret|password|apikey|api_key)\s*[=:]\s*)(\S+)", re.IGNORECASE),
]


def scrub(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    out = _SECRET_PATTERNS[0].sub("bot<redacted>", value)
    out = _SECRET_PATTERNS[1].sub(r"\1***\3", out)
    out = _SECRET_PATTERNS[2].sub(r"\1***", out)
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub(record.getMessage()),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update({k: scrub(v) for k, v in extra.items()})
        if record.exc_info:
            payload["exception"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    if (level or "").upper() in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, (level or "INFO").upper(), logging.INFO))
    # aiogram logs full update payloads at DEBUG only; keep third parties calm.
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, message: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log a message with structured fields."""
    logger.log(level, message, extra={"extra_fields": fields})
