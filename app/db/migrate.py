"""Minimal forward-only SQL migration runner.

Usage::

    python -m app.db.migrate                 # uses DATABASE_URL
    python -m app.db.migrate --dsn postgresql://...

Migrations live in ``app/db/migrations`` and are applied in filename order.
Each file runs inside a transaction and is recorded in ``schema_migrations``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

from psycopg import AsyncConnection
from psycopg.rows import dict_row

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_VERSION_RE = re.compile(r"^(\d+)_")


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(p for p in directory.glob("*.sql") if _VERSION_RE.match(p.name))
    return files


async def apply_migrations(dsn: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; returns the list of newly applied versions."""
    applied: list[str] = []
    async with await AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        await conn.commit()
        cur = await conn.execute("SELECT version FROM schema_migrations")
        done = {row["version"] for row in await cur.fetchall()}
        for path in discover_migrations(directory):
            version = path.stem
            if version in done:
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
                    (version,),
                )
            applied.append(version)
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply PostgreSQL migrations")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args(argv)
    if not args.dsn:
        print("error: DATABASE_URL is not set and --dsn was not provided", file=sys.stderr)
        return 2
    applied = asyncio.run(apply_migrations(args.dsn))
    if applied:
        print("applied migrations:")
        for version in applied:
            print(f"  - {version}")
    else:
        print("database is up to date")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
