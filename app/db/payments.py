"""Payment persistence: confirmation capture, atomic registration, history.

Registration is the most sensitive operation in the bot, so it runs in one
transaction with row locks and a UNIQUE idempotency key:

    SELECT pending_payment ... FOR UPDATE   (consumed check)
    SELECT tenant ...          FOR UPDATE   (balance check)
    INSERT payment  ON CONFLICT (idempotency_key) DO NOTHING
    INSERT payment_allocations
    UPDATE tenants (cycle dates, credit)
    UPDATE pending_payments (consumed_at)
    INSERT audit_log
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from app.core.dates import add_months
from app.core.rent import PaymentPlan, money, plan_payment
from app.db.connection import Database


class PaymentError(Exception):
    """Raised when a payment cannot be registered."""


async def create_pending_payment(
    db: Database,
    *,
    tenant_id: int,
    amount: Decimal,
    notes: str | None = None,
    created_by: int | None = None,
) -> dict:
    """Persist a confirmation screen draft; returns the row (with its UUID)."""
    pending_id = uuid.uuid4()
    query = """
        INSERT INTO pending_payments (id, tenant_id, amount, notes, created_by)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, tenant_id, amount, notes, created_at
    """
    async with db.transaction() as conn:
        cur = await conn.execute(query, (pending_id, tenant_id, amount, notes, created_by))
        row = await cur.fetchone()
    return row


async def get_pending_payment(db: Database, pending_id: uuid.UUID) -> dict | None:
    return await db.fetch_one(
        """
        SELECT p.*, t.full_name, t.currency, t.monthly_rent
        FROM pending_payments p
        JOIN tenants t ON t.id = p.tenant_id
        WHERE p.id = %s
        """,
        (pending_id,),
    )


async def expire_pending_payments(db: Database, older_than_hours: int = 24) -> int:
    return await db.execute(
        """
        DELETE FROM pending_payments
        WHERE consumed_at IS NULL
          AND created_at < now() - (%s || ' hours')::interval
        """,
        (older_than_hours,),
    )


async def register_payment(
    db: Database,
    *,
    pending_id: uuid.UUID,
    registered_by: int | None,
    now: dt.datetime,
    max_periods: int = 36,
) -> dict:
    """Register the payment of a pending confirmation atomically.

    Returns a dict with ``status`` one of:
      ``ok``, ``duplicate``, ``pending_not_found``, ``tenant_missing``,
      ``tenant_inactive``.
    """
    payment_day = now.date()
    async with db.transaction() as conn:
        cur = await conn.execute(
            "SELECT * FROM pending_payments WHERE id = %s FOR UPDATE", (pending_id,)
        )
        pending = await cur.fetchone()
        if pending is None:
            return {"status": "pending_not_found"}
        if pending["consumed_at"] is not None:
            return {
                "status": "duplicate",
                "payment_id": pending["payment_id"],
                "pending_id": str(pending_id),
            }

        cur = await conn.execute("SELECT * FROM tenants WHERE id = %s FOR UPDATE", (pending["tenant_id"],))
        tenant = await cur.fetchone()
        if tenant is None:
            return {"status": "tenant_missing"}
        if not tenant["is_active"]:
            return {"status": "tenant_inactive", "tenant_id": tenant["id"]}

        plan: PaymentPlan = plan_payment(
            monthly_rent=tenant["monthly_rent"],
            currency=tenant["currency"],
            cycle_start=tenant["current_cycle_start"],
            next_due=tenant["next_due_date"],
            credit=tenant["credit_amount"],
            amount=pending["amount"],
            payment_date=payment_day,
            max_periods=max_periods,
        )

        idempotency_key = f"pending:{pending_id}"
        cur = await conn.execute(
            """
            INSERT INTO payments (
                tenant_id, amount, currency, payment_date, period_start, period_end,
                payment_type, notes, idempotency_key, registered_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """,
            (
                tenant["id"],
                money(pending["amount"]),
                tenant["currency"],
                now,
                tenant["next_due_date"],
                plan.new_next_due,
                plan.payment_type.value,
                pending["notes"],
                idempotency_key,
                registered_by,
            ),
        )
        payment = await cur.fetchone()
        if payment is None:  # pragma: no cover - defensive
            return {"status": "duplicate", "pending_id": str(pending_id)}

        for allocation in plan.allocations:
            await conn.execute(
                """
                INSERT INTO payment_allocations
                    (payment_id, tenant_id, period_start, period_end, amount)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    payment["id"],
                    tenant["id"],
                    allocation.period_start,
                    allocation.period_end,
                    allocation.amount,
                ),
            )

        cur = await conn.execute(
            """
            UPDATE tenants
            SET current_cycle_start = %s,
                next_due_date = %s,
                credit_amount = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (plan.new_cycle_start, plan.new_next_due, plan.new_credit, tenant["id"]),
        )
        updated_tenant = await cur.fetchone()

        await conn.execute(
            "UPDATE pending_payments SET consumed_at = now(), payment_id = %s WHERE id = %s",
            (payment["id"], pending_id),
        )
        await conn.execute(
            """
            INSERT INTO audit_log (actor_id, action, entity_type, entity_id, details)
            VALUES (%s, 'payment_registered', 'payment', %s, %s)
            """,
            (
                registered_by,
                payment["id"],
                Jsonb(
                    {
                        "tenant_id": tenant["id"],
                        "amount": str(payment["amount"]),
                        "currency": payment["currency"],
                        "payment_type": payment["payment_type"],
                        "previous_next_due": str(tenant["next_due_date"]),
                        "new_next_due": str(plan.new_next_due),
                        "pending_id": str(pending_id),
                    }
                ),
            ),
        )

    return {
        "status": "ok",
        "payment": payment,
        "plan": plan,
        "tenant": updated_tenant,
        "previous_next_due": tenant["next_due_date"],
    }


async def list_payments(
    db: Database,
    *,
    tenant_id: int | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if tenant_id is not None:
        clauses.append("p.tenant_id = %s")
        params.append(tenant_id)
    if start is not None:
        clauses.append("p.payment_date >= %s")
        params.append(start)
    if end is not None:
        clauses.append("p.payment_date < %s")
        params.append(end)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.extend([limit, offset])
    return await db.fetch_all(
        f"""
        SELECT p.*, t.full_name, b.name AS building_name, a.apartment_number
        FROM payments p
        JOIN tenants t ON t.id = p.tenant_id
        JOIN apartments a ON a.id = t.apartment_id
        JOIN buildings b ON b.id = a.building_id
        {where}
        ORDER BY p.payment_date DESC, p.id DESC
        LIMIT %s OFFSET %s
        """,
        params,
    )


async def sum_payments(
    db: Database, *, start: dt.datetime, end: dt.datetime
) -> tuple[Decimal, int]:
    row = await db.fetch_one(
        """
        SELECT coalesce(sum(amount), 0) AS total, count(*) AS payments_count
        FROM payments
        WHERE payment_date >= %s AND payment_date < %s
        """,
        (start, end),
    )
    return (
        money(row["total"]) if row else money(0),
        int(row["payments_count"]) if row else 0,
    )


async def totals_by_currency(
    db: Database, *, start: dt.datetime | None = None, end: dt.datetime | None = None
) -> dict[str, tuple[Decimal, int]]:
    """Grouped payment totals per currency: ``{currency: (total, count)}``."""
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("payment_date >= %s")
        params.append(start)
    if end is not None:
        clauses.append("payment_date < %s")
        params.append(end)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await db.fetch_all(
        f"""
        SELECT currency, coalesce(sum(amount), 0) AS total, count(*) AS payments_count
        FROM payments {where}
        GROUP BY currency
        ORDER BY currency
        """,
        params,
    )
    return {row["currency"]: (money(row["total"]), int(row["payments_count"])) for row in rows}


async def ledger_for_tenant(db: Database, tenant_id: int, limit: int = 12) -> list[dict]:
    return await db.fetch_all(
        """
        SELECT * FROM payment_allocations
        WHERE tenant_id = %s
        ORDER BY period_start DESC
        LIMIT %s
        """,
        (tenant_id, limit),
    )


async def current_period_paid(db: Database, tenant_id: int, period_start: dt.date) -> Decimal:
    value = await db.fetch_value(
        """
        SELECT coalesce(sum(amount), 0)
        FROM payment_allocations
        WHERE tenant_id = %s AND period_start = %s
        """,
        (tenant_id, period_start),
    )
    return money(value or 0)


def next_period_bounds(next_due: dt.date) -> tuple[dt.date, dt.date]:
    return next_due, add_months(next_due, 1)
