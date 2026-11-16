# Database reference

PostgreSQL 13+ (tested on 16). Schema lives in
`app/db/migrations/0001_init.sql`; apply with `python -m app.db.migrate`.

Money columns are `NUMERIC(12, 2)` (never floating point). Instants are
`TIMESTAMPTZ`; rental cycle dates are `DATE` (they are calendar days in the
owner's timezone, not instants).

## Entity relationships

```
buildings 1───n apartments 1───n tenants 1───n payments 1───n payment_allocations
                                    │
                                    └── pending_payments (confirmation drafts)

standalone: daily_reports, app_settings, audit_log,
            processed_updates (webhook idempotency), bot_fsm_states
```

## Tables

### buildings
| column | type | notes |
| --- | --- | --- |
| id | BIGSERIAL PK | |
| name | TEXT NOT NULL | non-empty |
| address | TEXT | optional |
| description | TEXT | optional |
| is_active | BOOLEAN | archived = false |
| created_at / updated_at | TIMESTAMPTZ | |

### apartments
| column | type | notes |
| --- | --- | --- |
| id | BIGSERIAL PK | |
| building_id | BIGINT FK → buildings, RESTRICT | |
| apartment_number | TEXT | unique per building |
| floor | TEXT | optional |
| description | TEXT | optional |
| occupancy_status | TEXT | `occupied` / `vacant` (kept in sync in the same transaction) |
| is_active | BOOLEAN | |
| created_at / updated_at | TIMESTAMPTZ | |

Constraints: `UNIQUE (building_id, apartment_number)`.

### tenants
| column | type | notes |
| --- | --- | --- |
| id | BIGSERIAL PK | |
| full_name | TEXT NOT NULL | |
| phone | TEXT | optional |
| apartment_id | BIGINT FK → apartments, RESTRICT | |
| monthly_rent | NUMERIC(12,2) | > 0 |
| currency | CHAR(3) | ISO code, per tenant |
| rental_start_date | DATE | |
| current_cycle_start | DATE | start of the funded period |
| next_due_date | DATE | next payment deadline, > current_cycle_start |
| credit_amount | NUMERIC(12,2) | money received not yet assigned to a funded period |
| notes | TEXT | |
| is_active | BOOLEAN | archived tenants keep all history |
| archived_at | TIMESTAMPTZ | |
| created_at / updated_at | TIMESTAMPTZ | |

Indexes: `tenants_next_due_idx`, `tenants_active_idx`, `lower(full_name)`, and a
partial unique index `tenants_one_active_per_apartment (apartment_id) WHERE
is_active` — an apartment can never hold two active tenants.

### payments (append-only)
| column | type | notes |
| --- | --- | --- |
| id | BIGSERIAL PK | |
| tenant_id | BIGINT FK → tenants, RESTRICT | |
| amount | NUMERIC(12,2) | > 0, exactly what was received |
| currency | CHAR(3) | currency at the time of payment |
| payment_date | TIMESTAMPTZ | when the owner registered it |
| period_start / period_end | DATE | the period this payment settles |
| payment_type | TEXT | `full` / `partial` / `overpayment` |
| notes | TEXT | |
| idempotency_key | TEXT UNIQUE | `pending:<uuid>` — blocks duplicate confirmations |
| registered_by | BIGINT | Telegram user id of the owner |
| created_at | TIMESTAMPTZ | |

Rows are never updated or deleted, so history survives edits and archiving.

### payment_allocations
Ledger of funded periods: `payment_id`, `tenant_id`, `period_start`,
`period_end`, `amount`. One payment that catches up arrears plus extra months
produces several rows. Used for audits and per-period reporting.

### pending_payments
Confirmation drafts: `id UUID PK`, `tenant_id`, `amount`, `notes`, `created_by`,
`created_at`, `consumed_at`, `payment_id`. `consumed_at` is set in the same
transaction that inserts the payment, so a double-tapped “confirm” button can
never register twice. Rows older than 24–48 h are purged by the cron endpoint.

### daily_reports
`report_date DATE UNIQUE`, `status` (`pending`/`sending`/`sent`/`failed`),
`attempts`, `sent_at`, `error_message`, `summary JSONB`, timestamps. The UNIQUE
date plus a row lock (`FOR UPDATE`) is what makes “one report per day” reliable
under retries and concurrent invocations.

### app_settings
Key/value configuration (`currency`, `report_enabled`, `report_time`,
`upcoming_days`). Defaults are seeded by the migration and the defaults are also
mirrored in `app/db/repository.py:SETTING_DEFAULTS`, so a missing row can never
break the UI.

### audit_log
`actor_id`, `action`, `entity_type`, `entity_id`, `details JSONB`, `created_at`.
Written for entity creation/edits/archiving, payment registration and settings
changes.

### processed_updates
`update_id BIGINT PK` — Telegram retries a webhook when it does not get a fast
200, so updates are claimed here before processing; duplicates are ignored.

### bot_fsm_states
`key TEXT PK`, `chat_id`, `user_id`, `state`, `data JSONB`, `updated_at`.
Backs the aiogram FSM storage so multi-step forms survive instances and restarts.

### schema_migrations
Applied migration filenames, managed by `app/db/migrate.py`.

## Operational notes

* Apply migrations before the first request; the app never creates tables itself.
* Backups: enable Supabase daily backups (or `pg_dump`) — payments are the asset.
* Housekeeping runs at the end of each daily-report invocation: purge
  `processed_updates` (7 days), `bot_fsm_states` (7 days) and stale
  `pending_payments` (48 h). Payment, tenant and report rows are never purged.
