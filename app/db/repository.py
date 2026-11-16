"""Data access for buildings, apartments, app settings and the audit log.

All SQL is parameterised. Money columns are NUMERIC and come back as Decimal.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.db.connection import Database

_UNSET = object()


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text or None


# ---------------------------------------------------------------------------
# buildings
# ---------------------------------------------------------------------------
async def create_building(
    db: Database,
    *,
    name: str,
    address: str | None = None,
    description: str | None = None,
) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """
            INSERT INTO buildings (name, address, description)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (_clean(name), _clean(address), _clean(description)),
        )
        return await cur.fetchone()


async def get_building(db: Database, building_id: int) -> dict | None:
    return await db.fetch_one(
        """
        SELECT b.*,
               (SELECT count(*) FROM apartments a WHERE a.building_id = b.id AND a.is_active) AS apartments_count,
               (SELECT count(*) FROM apartments a WHERE a.building_id = b.id AND a.is_active
                    AND a.occupancy_status = 'occupied') AS occupied_count
        FROM buildings b
        WHERE b.id = %s
        """,
        (building_id,),
    )


async def list_buildings(db: Database, *, active_only: bool = True) -> list[dict]:
    sql = """
        SELECT b.*,
               (SELECT count(*) FROM apartments a WHERE a.building_id = b.id AND a.is_active) AS apartments_count,
               (SELECT count(*) FROM apartments a WHERE a.building_id = b.id AND a.is_active
                    AND a.occupancy_status = 'occupied') AS occupied_count
        FROM buildings b
        {where}
        ORDER BY b.name
    """
    where = "WHERE b.is_active" if active_only else ""
    return await db.fetch_all(sql.format(where=where))


async def update_building(
    db: Database,
    building_id: int,
    *,
    name: Any = _UNSET,
    address: Any = _UNSET,
    description: Any = _UNSET,
) -> dict | None:
    sets: list[str] = []
    params: list[Any] = []
    for column, value in (("name", name), ("address", address), ("description", description)):
        if value is _UNSET:
            continue
        sets.append(f"{column} = %s")
        params.append(_clean(value) if column != "name" else (value or "").strip())
    if not sets:
        return await get_building(db, building_id)
    sets.append("updated_at = now()")
    params.append(building_id)
    async with db.transaction() as conn:
        cur = await conn.execute(
            f"UPDATE buildings SET {', '.join(sets)} WHERE id = %s RETURNING *", params
        )
        return await cur.fetchone()


async def archive_building(db: Database, building_id: int) -> dict | None:
    """Archive a building and all its apartments (kept for history)."""
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE apartments SET is_active = FALSE, updated_at = now() WHERE building_id = %s",
            (building_id,),
        )
        cur = await conn.execute(
            "UPDATE buildings SET is_active = FALSE, updated_at = now() WHERE id = %s RETURNING *",
            (building_id,),
        )
        return await cur.fetchone()


async def building_active_tenants_count(db: Database, building_id: int) -> int:
    value = await db.fetch_value(
        """
        SELECT count(*)
        FROM tenants t
        JOIN apartments a ON a.id = t.apartment_id
        WHERE a.building_id = %s AND t.is_active
        """,
        (building_id,),
    )
    return int(value or 0)


# ---------------------------------------------------------------------------
# apartments
# ---------------------------------------------------------------------------
_APARTMENT_SELECT = """
    SELECT a.*, b.name AS building_name,
           t.id AS tenant_id, t.full_name AS tenant_name
    FROM apartments a
    JOIN buildings b ON b.id = a.building_id
    LEFT JOIN tenants t ON t.apartment_id = a.id AND t.is_active
"""


async def create_apartment(
    db: Database,
    *,
    building_id: int,
    apartment_number: str,
    floor: str | None = None,
    description: str | None = None,
) -> dict:
    async with db.transaction() as conn:
        cur = await conn.execute(
            """
            INSERT INTO apartments (building_id, apartment_number, floor, description)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (building_id, (apartment_number or "").strip(), _clean(floor), _clean(description)),
        )
        row = await cur.fetchone()
    return await get_apartment(db, row["id"])


async def get_apartment(db: Database, apartment_id: int) -> dict | None:
    return await db.fetch_one(_APARTMENT_SELECT + " WHERE a.id = %s", (apartment_id,))


async def list_apartments(
    db: Database,
    *,
    building_id: int | None = None,
    active_only: bool = True,
    only_vacant: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    clauses = []
    params: list[Any] = []
    if building_id is not None:
        clauses.append("a.building_id = %s")
        params.append(building_id)
    if active_only:
        clauses.append("a.is_active")
    if only_vacant:
        clauses.append("a.occupancy_status = 'vacant'")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.extend([limit, offset])
    sql = f"{_APARTMENT_SELECT} {where} ORDER BY b.name, a.apartment_number LIMIT %s OFFSET %s"
    return await db.fetch_all(sql, params)


async def count_apartments(db: Database, *, active_only: bool = True) -> int:
    value = await db.fetch_value(
        "SELECT count(*) FROM apartments a" + (" WHERE a.is_active" if active_only else "")
    )
    return int(value or 0)


async def update_apartment(
    db: Database,
    apartment_id: int,
    *,
    apartment_number: Any = _UNSET,
    floor: Any = _UNSET,
    description: Any = _UNSET,
) -> dict | None:
    sets: list[str] = []
    params: list[Any] = []
    if apartment_number is not _UNSET:
        sets.append("apartment_number = %s")
        params.append((apartment_number or "").strip())
    if floor is not _UNSET:
        sets.append("floor = %s")
        params.append(_clean(floor))
    if description is not _UNSET:
        sets.append("description = %s")
        params.append(_clean(description))
    if not sets:
        return await get_apartment(db, apartment_id)
    sets.append("updated_at = now()")
    params.append(apartment_id)
    async with db.transaction() as conn:
        await conn.execute(f"UPDATE apartments SET {', '.join(sets)} WHERE id = %s", params)
    return await get_apartment(db, apartment_id)


async def set_apartment_occupancy(db: Database, apartment_id: int, occupied: bool) -> None:
    await db.execute(
        """
        UPDATE apartments
        SET occupancy_status = %s, updated_at = now()
        WHERE id = %s
        """,
        ("occupied" if occupied else "vacant", apartment_id),
    )


async def archive_apartment(db: Database, apartment_id: int) -> dict | None:
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE apartments SET is_active = FALSE, updated_at = now() WHERE id = %s",
            (apartment_id,),
        )
    return await get_apartment(db, apartment_id)


async def apartment_active_tenant(db: Database, apartment_id: int) -> dict | None:
    return await db.fetch_one(
        "SELECT * FROM tenants WHERE apartment_id = %s AND is_active", (apartment_id,)
    )


# ---------------------------------------------------------------------------
# app settings
# ---------------------------------------------------------------------------
SETTING_DEFAULTS: dict[str, str] = {
    "currency": "MRU",
    "report_enabled": "true",
    "report_time": "08:00",
    "upcoming_days": "3",
}


async def get_app_settings(db: Database) -> dict[str, str]:
    """All settings merged over defaults (never raises on missing keys)."""
    rows = await db.fetch_all("SELECT key, value FROM app_settings")
    settings = dict(SETTING_DEFAULTS)
    settings.update({row["key"]: row["value"] for row in rows})
    return settings


async def get_app_setting(db: Database, key: str) -> str | None:
    return await db.fetch_value("SELECT value FROM app_settings WHERE key = %s", (key,))


async def set_app_setting(db: Database, key: str, value: str) -> None:
    await db.execute(
        """
        INSERT INTO app_settings (key, value, updated_at) VALUES (%s, %s, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        (key, str(value)),
    )


def upcoming_days(settings: Mapping[str, str]) -> int:
    try:
        value = int(settings.get("upcoming_days", "3"))
    except (TypeError, ValueError):
        return 3
    return value if value >= 0 else 3


def report_time(settings: Mapping[str, str]) -> tuple[int, int]:
    raw = settings.get("report_time", "08:00")
    try:
        hours, minutes = raw.split(":")
        return int(hours), int(minutes)
    except (ValueError, AttributeError):
        return 8, 0


# ---------------------------------------------------------------------------
# audit log
# ---------------------------------------------------------------------------
async def log_audit(
    db: Database,
    *,
    actor_id: int | None,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict | None = None,
) -> None:
    from psycopg.types.json import Jsonb

    await db.execute(
        """
        INSERT INTO audit_log (actor_id, action, entity_type, entity_id, details)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (actor_id, action, entity_type, entity_id, Jsonb(details or {})),
    )


# ---------------------------------------------------------------------------
# webhook idempotency
# ---------------------------------------------------------------------------
async def claim_update(db: Database, update_id: int) -> bool:
    """Return True when this Telegram update is new (safe to process)."""
    async with db.transaction() as conn:
        cur = await conn.execute(
            "INSERT INTO processed_updates (update_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING update_id",
            (update_id,),
        )
        return await cur.fetchone() is not None


async def purge_old_updates(db: Database, keep_days: int = 7) -> int:
    return await db.execute(
        "DELETE FROM processed_updates WHERE processed_at < now() - (%s || ' days')::interval",
        (keep_days,),
    )
