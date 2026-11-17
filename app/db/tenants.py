"""Tenant data access."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from app.core.dates import add_months
from app.db.connection import Database

_UNSET = object()

_TENANT_SELECT = """
    SELECT t.*,
           a.apartment_number, a.floor, a.building_id,
           b.name AS building_name
    FROM tenants t
    JOIN apartments a ON a.id = t.apartment_id
    JOIN buildings b ON b.id = a.building_id
"""

EDITABLE_FIELDS = {
    "full_name",
    "phone",
    "monthly_rent",
    "currency",
    "rental_start_date",
    "notes",
    "apartment_id",
}


async def create_tenant(
    db: Database,
    *,
    full_name: str,
    apartment_id: int,
    monthly_rent: Decimal,
    currency: str,
    rental_start_date: dt.date,
    phone: str | None = None,
    notes: str | None = None,
) -> dict:
    """Insert a tenant and mark the apartment occupied (single transaction).

    The first payment is due on the rental start date: ``next_due_date`` equals
    ``rental_start_date`` and the funded-period start sits one month before it.
    """
    cycle_start = add_months(rental_start_date, -1)
    async with db.transaction() as conn:
        cur = await conn.execute(
            """
            INSERT INTO tenants (
                full_name, phone, apartment_id, monthly_rent, currency,
                rental_start_date, current_cycle_start, next_due_date, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                full_name.strip(),
                (phone or "").strip() or None,
                apartment_id,
                monthly_rent,
                currency.upper(),
                rental_start_date,
                cycle_start,
                rental_start_date,
                (notes or "").strip() or None,
            ),
        )
        row = await cur.fetchone()
        await conn.execute(
            "UPDATE apartments SET occupancy_status = 'occupied', updated_at = now() WHERE id = %s",
            (apartment_id,),
        )
    return await get_tenant(db, row["id"])


async def get_tenant(db: Database, tenant_id: int) -> dict | None:
    return await db.fetch_one(_TENANT_SELECT + " WHERE t.id = %s", (tenant_id,))


async def list_tenants(
    db: Database,
    *,
    active_only: bool = True,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    where = "WHERE t.is_active" if active_only else ""
    return await db.fetch_all(
        f"{_TENANT_SELECT} {where} ORDER BY t.next_due_date, t.full_name LIMIT %s OFFSET %s",
        (limit, offset),
    )


async def count_tenants(db: Database, *, active_only: bool = True) -> int:
    value = await db.fetch_value(
        "SELECT count(*) FROM tenants t" + (" WHERE t.is_active" if active_only else "")
    )
    return int(value or 0)


async def search_tenants(
    db: Database,
    query: str,
    *,
    active_only: bool = True,
    limit: int = 12,
) -> list[dict]:
    """Search by name, phone, building or apartment number."""
    pattern = f"%{query.strip()}%"
    where_active = "AND t.is_active" if active_only else ""
    return await db.fetch_all(
        f"""
        {_TENANT_SELECT}
        WHERE (t.full_name ILIKE %s
               OR coalesce(t.phone, '') ILIKE %s
               OR b.name ILIKE %s
               OR a.apartment_number ILIKE %s)
        {where_active}
        ORDER BY t.is_active DESC, t.full_name
        LIMIT %s
        """,
        (pattern, pattern, pattern, pattern, limit),
    )


async def update_tenant(db: Database, tenant_id: int, **fields: Any) -> dict | None:
    """Update editable tenant fields. Payment history is never touched."""
    sets: list[str] = []
    params: list[Any] = []
    for column, value in fields.items():
        if column not in EDITABLE_FIELDS:
            raise ValueError(f"field not editable: {column}")
        if value is _UNSET:
            continue
        if column in {"phone", "notes"}:
            value = (value or "").strip() or None
        if column == "full_name":
            value = (value or "").strip()
        if column == "currency" and value:
            value = str(value).upper()
        sets.append(f"{column} = %s")
        params.append(value)
    if not sets:
        return await get_tenant(db, tenant_id)
    sets.append("updated_at = now()")
    params.append(tenant_id)
    async with db.transaction() as conn:
        cur = await conn.execute(
            f"UPDATE tenants SET {', '.join(sets)} WHERE id = %s RETURNING id", params
        )
        if await cur.fetchone() is None:
            return None
    return await get_tenant(db, tenant_id)


async def archive_tenant(db: Database, tenant_id: int) -> dict | None:
    """Archive a tenant, freeing the apartment when no other active tenant uses it."""
    async with db.transaction() as conn:
        cur = await conn.execute(
            """
            UPDATE tenants
            SET is_active = FALSE, archived_at = now(), updated_at = now()
            WHERE id = %s AND is_active
            RETURNING id, apartment_id
            """,
            (tenant_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        await conn.execute(
            """
            UPDATE apartments
            SET occupancy_status = 'vacant', updated_at = now()
            WHERE id = %s
              AND NOT EXISTS (
                  SELECT 1 FROM tenants t WHERE t.apartment_id = %s AND t.is_active
              )
            """,
            (row["apartment_id"], row["apartment_id"]),
        )
    return await get_tenant(db, tenant_id)


async def change_tenant_apartment(db: Database, tenant_id: int, apartment_id: int) -> dict | None:
    """Move an active tenant to another apartment inside one transaction."""
    async with db.transaction() as conn:
        cur = await conn.execute(
            "SELECT id, apartment_id FROM tenants WHERE id = %s AND is_active FOR UPDATE",
            (tenant_id,),
        )
        tenant = await cur.fetchone()
        if tenant is None:
            return None
        cur = await conn.execute(
            "SELECT id, is_active, occupancy_status FROM apartments WHERE id = %s",
            (apartment_id,),
        )
        apartment = await cur.fetchone()
        if apartment is None or not apartment["is_active"]:
            raise ValueError("apartment_not_available")
        occupied_by_other = await conn.execute(
            "SELECT 1 FROM tenants WHERE apartment_id = %s AND is_active AND id <> %s",
            (apartment_id, tenant_id),
        )
        if await occupied_by_other.fetchone() is not None:
            raise ValueError("apartment_occupied")
        await conn.execute(
            "UPDATE tenants SET apartment_id = %s, updated_at = now() WHERE id = %s",
            (apartment_id, tenant_id),
        )
        await conn.execute(
            "UPDATE apartments SET occupancy_status = 'occupied', updated_at = now() WHERE id = %s",
            (apartment_id,),
        )
        await conn.execute(
            """
            UPDATE apartments SET occupancy_status = 'vacant', updated_at = now()
            WHERE id = %s
              AND NOT EXISTS (SELECT 1 FROM tenants t WHERE t.apartment_id = %s AND t.is_active)
            """,
            (tenant["apartment_id"], tenant["apartment_id"]),
        )
    return await get_tenant(db, tenant_id)


async def payment_stats(db: Database, tenant_id: int) -> dict:
    """Aggregated payment figures for one tenant (never mutates anything)."""
    row = await db.fetch_one(
        """
        SELECT count(*) AS payments_count,
               coalesce(sum(amount), 0) AS total_paid,
               max(payment_date) AS last_payment_at
        FROM payments
        WHERE tenant_id = %s
        """,
        (tenant_id,),
    )
    return row or {"payments_count": 0, "total_paid": Decimal(0), "last_payment_at": None}
