#!/usr/bin/env python3
"""Apply SQL migrations to DATABASE_URL (thin wrapper around app.db.migrate)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.migrate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
