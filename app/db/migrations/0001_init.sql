-- ---------------------------------------------------------------------------
-- 0001_init.sql — initial schema for the rental management bot
--
-- Target: PostgreSQL 13+ (Supabase compatible).
-- Money: NUMERIC(12, 2) — never floating point.
-- Time:  TIMESTAMPTZ for instants, DATE for rental cycle dates.
-- ---------------------------------------------------------------------------

-- Tracks which migration files have been applied.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- buildings
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS buildings (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(btrim(name)) > 0),
    address     TEXT,
    description TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS buildings_active_idx ON buildings (is_active);

-- ---------------------------------------------------------------------------
-- apartments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS apartments (
    id               BIGSERIAL PRIMARY KEY,
    building_id      BIGINT NOT NULL REFERENCES buildings (id) ON DELETE RESTRICT,
    apartment_number TEXT NOT NULL CHECK (length(btrim(apartment_number)) > 0),
    floor            TEXT,
    description      TEXT,
    occupancy_status TEXT NOT NULL DEFAULT 'vacant'
        CHECK (occupancy_status IN ('occupied', 'vacant')),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT apartments_building_number_key UNIQUE (building_id, apartment_number)
);

CREATE INDEX IF NOT EXISTS apartments_building_idx ON apartments (building_id);
CREATE INDEX IF NOT EXISTS apartments_active_idx ON apartments (is_active);

-- ---------------------------------------------------------------------------
-- tenants
--
-- Rental cycle model (see README "Rental cycle model"):
--   * The currently funded period is [current_cycle_start, next_due_date).
--   * next_due_date is the deadline of the next payment.
--   * credit_amount holds money received that is not yet assigned to a funded
--     period (partial payments / prepayments).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    id                  BIGSERIAL PRIMARY KEY,
    full_name           TEXT NOT NULL CHECK (length(btrim(full_name)) > 0),
    phone               TEXT,
    apartment_id        BIGINT NOT NULL REFERENCES apartments (id) ON DELETE RESTRICT,
    monthly_rent        NUMERIC(12, 2) NOT NULL CHECK (monthly_rent > 0),
    currency            CHAR(3) NOT NULL,
    rental_start_date   DATE NOT NULL,
    current_cycle_start DATE NOT NULL,
    next_due_date       DATE NOT NULL,
    credit_amount       NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (credit_amount >= 0),
    notes               TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    archived_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tenants_dates_check CHECK (next_due_date > current_cycle_start)
);

CREATE INDEX IF NOT EXISTS tenants_apartment_idx ON tenants (apartment_id);
CREATE INDEX IF NOT EXISTS tenants_active_idx ON tenants (is_active);
CREATE INDEX IF NOT EXISTS tenants_next_due_idx ON tenants (next_due_date);
CREATE INDEX IF NOT EXISTS tenants_name_idx ON tenants (lower(full_name));

-- An apartment must never have two active tenants at the same time.
CREATE UNIQUE INDEX IF NOT EXISTS tenants_one_active_per_apartment
    ON tenants (apartment_id)
    WHERE is_active;

-- ---------------------------------------------------------------------------
-- payments — append only. Rows are never updated or deleted by the bot.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       BIGINT NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency        CHAR(3) NOT NULL,
    payment_date    TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    payment_type    TEXT NOT NULL
        CHECK (payment_type IN ('full', 'partial', 'overpayment')),
    notes           TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    registered_by   BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS payments_tenant_idx ON payments (tenant_id, payment_date DESC);
CREATE INDEX IF NOT EXISTS payments_date_idx ON payments (payment_date DESC);

-- Ledger of funded rental periods (audit trail for payment allocation).
CREATE TABLE IF NOT EXISTS payment_allocations (
    id           BIGSERIAL PRIMARY KEY,
    payment_id   BIGINT NOT NULL REFERENCES payments (id) ON DELETE CASCADE,
    tenant_id    BIGINT NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    period_start DATE NOT NULL,
    period_end   DATE NOT NULL,
    amount       NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS payment_allocations_tenant_idx
    ON payment_allocations (tenant_id, period_start);

-- ---------------------------------------------------------------------------
-- pending_payments — confirmation screens awaiting the owner's "confirm".
-- Guarantees that a double-tapped confirmation can never register twice.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_payments (
    id          UUID PRIMARY KEY,
    tenant_id   BIGINT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    amount      NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    notes       TEXT,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ,
    payment_id  BIGINT REFERENCES payments (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS pending_payments_created_idx ON pending_payments (created_at);

-- ---------------------------------------------------------------------------
-- daily_reports — one row per reporting date (idempotent daily delivery).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_reports (
    id            BIGSERIAL PRIMARY KEY,
    report_date   DATE NOT NULL UNIQUE,
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    attempts      INTEGER NOT NULL DEFAULT 1,
    sent_at       TIMESTAMPTZ,
    error_message TEXT,
    summary       JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- app_settings — configurable settings editable from the bot.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- audit_log — important operations trail.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor_id    BIGINT,
    action      TEXT NOT NULL,
    entity_type TEXT,
    entity_id   BIGINT,
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_log_created_idx ON audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_entity_idx ON audit_log (entity_type, entity_id);

-- ---------------------------------------------------------------------------
-- processed_updates — webhook idempotency (Telegram retries must be safe).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processed_updates (
    update_id    BIGINT PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS processed_updates_time_idx ON processed_updates (processed_at);

-- ---------------------------------------------------------------------------
-- bot_fsm_states — conversation state persisted in PostgreSQL so multi-step
-- forms survive restarts and serverless instances.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bot_fsm_states (
    key        TEXT PRIMARY KEY,
    chat_id    BIGINT,
    user_id    BIGINT,
    state      TEXT,
    data       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS bot_fsm_states_updated_idx ON bot_fsm_states (updated_at);

-- ---------------------------------------------------------------------------
-- Default settings (the owner can change them from the bot).
-- ---------------------------------------------------------------------------
INSERT INTO app_settings (key, value) VALUES
    ('currency', 'MRU'),
    ('report_enabled', 'true'),
    ('report_time', '08:00'),
    ('upcoming_days', '3')
ON CONFLICT (key) DO NOTHING;
