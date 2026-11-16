"""Daily report bookkeeping: claim, finish, fail, history."""

from __future__ import annotations

import datetime as dt
from typing import Any

from psycopg.types.json import Jsonb

from app.db.connection import Database


async def claim_daily_report(
    db: Database,
    report_date: dt.date,
    *,
    stale_minutes: int = 15,
    max_attempts: int = 3,
) -> dict:
    """Try to become the sender of ``report_date``'s report.

    Returns ``{"claimed": bool, "reason": str, "report_id": int | None}``.

    Guarantees at most one *successful* delivery per report date:
      * a new date is claimed with an INSERT ... ON CONFLICT DO NOTHING,
      * an already ``sent`` report is never re-sent,
      * a ``failed`` report is retried while attempts remain,
      * a stale ``sending``/``pending`` row (crashed worker) is reclaimed.
    """
    async with db.transaction() as conn:
        cur = await conn.execute(
            """
            INSERT INTO daily_reports (report_date, status, attempts)
            VALUES (%s, 'sending', 1)
            ON CONFLICT (report_date) DO NOTHING
            RETURNING id
            """,
            (report_date,),
        )
        inserted = await cur.fetchone()
        if inserted is not None:
            return {"claimed": True, "reason": "new", "report_id": inserted["id"]}

        cur = await conn.execute(
            "SELECT * FROM daily_reports WHERE report_date = %s FOR UPDATE", (report_date,)
        )
        existing = await cur.fetchone()
        if existing is None:  # pragma: no cover - defensive
            return {"claimed": False, "reason": "unknown", "report_id": None}

        if existing["status"] == "sent":
            return {"claimed": False, "reason": "already_sent", "report_id": existing["id"]}

        if existing["status"] == "failed":
            if existing["attempts"] >= max_attempts:
                return {"claimed": False, "reason": "retry_limit", "report_id": existing["id"]}
            cur = await conn.execute(
                """
                UPDATE daily_reports
                SET status = 'sending', attempts = attempts + 1, updated_at = now()
                WHERE id = %s
                RETURNING id
                """,
                (existing["id"],),
            )
            row = await cur.fetchone()
            return {"claimed": True, "reason": "retry", "report_id": row["id"]}

        # pending / sending: only reclaim when the previous worker is stale.
        cur = await conn.execute(
            """
            UPDATE daily_reports
            SET status = 'sending', attempts = attempts + 1, updated_at = now()
            WHERE id = %s
              AND updated_at < now() - (%s || ' minutes')::interval
              AND attempts < %s
            RETURNING id
            """,
            (existing["id"], stale_minutes, max_attempts),
        )
        row = await cur.fetchone()
        if row is not None:
            return {"claimed": True, "reason": "stale_retry", "report_id": row["id"]}
        return {"claimed": False, "reason": "in_progress", "report_id": existing["id"]}


async def mark_report_sent(db: Database, report_id: int, summary: dict[str, Any]) -> None:
    await db.execute(
        """
        UPDATE daily_reports
        SET status = 'sent', sent_at = now(), error_message = NULL,
            summary = %s, updated_at = now()
        WHERE id = %s
        """,
        (Jsonb(summary), report_id),
    )


async def mark_report_failed(db: Database, report_id: int, error_message: str) -> None:
    await db.execute(
        """
        UPDATE daily_reports
        SET status = 'failed', error_message = %s, updated_at = now()
        WHERE id = %s AND status <> 'sent'
        """,
        (error_message[:2000], report_id),
    )


async def get_report_for_date(db: Database, report_date: dt.date) -> dict | None:
    return await db.fetch_one("SELECT * FROM daily_reports WHERE report_date = %s", (report_date,))


async def recent_reports(db: Database, limit: int = 10) -> list[dict]:
    return await db.fetch_all(
        "SELECT * FROM daily_reports ORDER BY report_date DESC LIMIT %s", (limit,)
    )
