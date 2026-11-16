"""Vercel serverless entrypoint (also works with any ASGI host)."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repository root importable regardless of the host's working dir.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.web.app import create_app  # noqa: E402

app = create_app()
