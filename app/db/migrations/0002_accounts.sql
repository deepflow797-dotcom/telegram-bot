-- ---------------------------------------------------------------------------
-- 0002_accounts.sql — multi-account platform (one PostgreSQL schema per account)
--
-- Model
-- -----
-- * ``public.accounts`` holds one row per Telegram account that may use the bot.
-- * ``public.platform_settings`` holds the platform-level settings that the
--   main owner edits from inside the bot (contact phone, contact note…).
-- * Every account gets its own schema ``acct_<id>`` with its own copy of the
--   business tables. Data of different accounts can never be mixed: the
--   application sets ``search_path`` to the active account before every query.
-- * The original single-owner dataset becomes account 1 (the platform owner).
--
-- Public (shared) tables: schema_migrations, accounts, platform_settings,
-- processed_updates, bot_fsm_states.
-- Per-account tables: buildings, apartments, tenants, payments,
-- payment_allocations, pending_payments, daily_reports, app_settings,
-- audit_log.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- platform tables
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id            BIGSERIAL PRIMARY KEY,
    telegram_id   BIGINT UNIQUE,
    display_name  TEXT,
    username      TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'frozen', 'rejected')),
    plan          TEXT,
    paid_until    DATE,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at   TIMESTAMPTZ,
    last_seen_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS accounts_status_idx ON accounts (status);
CREATE INDEX IF NOT EXISTS accounts_paid_until_idx ON accounts (paid_until);

CREATE TABLE IF NOT EXISTS platform_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO platform_settings (key, value) VALUES
    ('owner_phone', '22222222'),
    ('contact_note', 'أرسل اسم المستخدم (اليوزر) الخاص بك لهذا الرقم، وسيتم تفعيل البوت لك، وستصلك رسالة تأكيد.')
ON CONFLICT (key) DO NOTHING;

-- The platform owner is always account 1.
INSERT INTO accounts (id, status, display_name, plan)
VALUES (1, 'active', 'المالك', 'owner')
ON CONFLICT (id) DO NOTHING;

SELECT setval(
    pg_get_serial_sequence('accounts', 'id'),
    GREATEST((SELECT COALESCE(max(id), 1) FROM accounts), 1)
);

-- ---------------------------------------------------------------------------
-- move the original single-tenant data into acct_1
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
    data_tables TEXT[] := ARRAY[
        'buildings', 'apartments', 'tenants', 'payments', 'payment_allocations',
        'pending_payments', 'daily_reports', 'app_settings', 'audit_log'
    ];
BEGIN
    IF to_regclass('acct_1.buildings') IS NOT NULL
       OR to_regclass('public.buildings') IS NULL THEN
        RETURN;  -- already migrated (or nothing to move)
    END IF;

    CREATE SCHEMA IF NOT EXISTS acct_1;

    FOREACH t IN ARRAY data_tables LOOP
        IF to_regclass('public.' || t) IS NOT NULL
           AND to_regclass('acct_1.' || t) IS NULL THEN
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA acct_1', t);
        END IF;
    END LOOP;

    EXECUTE 'GRANT USAGE ON SCHEMA acct_1 TO public';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA acct_1 TO public';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA acct_1 TO public';
END $$;

-- ---------------------------------------------------------------------------
-- provisioning: create an empty private dataset for a new account
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app_provision_account(p_account_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    s        TEXT := 'acct_' || p_account_id;
    t        TEXT;
    data_tables TEXT[] := ARRAY[
        'buildings', 'apartments', 'tenants', 'payments', 'payment_allocations',
        'pending_payments', 'daily_reports', 'app_settings', 'audit_log'
    ];
BEGIN
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', s);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO public', s);

    FOREACH t IN ARRAY data_tables LOOP
        IF to_regclass(s || '.' || t) IS NULL THEN
            -- structure copied from the owner's schema (indexes + constraints)
            EXECUTE format(
                'CREATE TABLE %I.%I (LIKE acct_1.%I INCLUDING ALL)', s, t, t
            );
            -- LIKE copies the *owner's* nextval default; give the account its own
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = s AND table_name = t AND column_name = 'id'
                  AND column_default LIKE 'nextval%'
            ) THEN
                EXECUTE format('ALTER TABLE %I.%I ALTER COLUMN id DROP DEFAULT', s, t);
                EXECUTE format('CREATE SEQUENCE IF NOT EXISTS %I.%I', s, t || '_id_seq');
                EXECUTE format(
                    'ALTER SEQUENCE %I.%I OWNED BY %I.%I.id', s, t || '_id_seq', s, t
                );
                EXECUTE format(
                    'ALTER TABLE %I.%I ALTER COLUMN id SET DEFAULT nextval(%L::regclass)',
                    s, t, s || '.' || t || '_id_seq'
                );
            END IF;
        END IF;
    END LOOP;

    -- foreign keys (LIKE does not copy them, and pg_get_constraintdef would
    -- print them unqualified, so the relationships are spelled out explicitly)
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'apartments_building_id_fkey'
                     AND connamespace = s::regnamespace) THEN
        EXECUTE format(
            'ALTER TABLE %I.apartments ADD CONSTRAINT apartments_building_id_fkey '
            'FOREIGN KEY (building_id) REFERENCES %I.buildings(id) ON DELETE RESTRICT',
            s, s
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'tenants_apartment_id_fkey'
                     AND connamespace = s::regnamespace) THEN
        EXECUTE format(
            'ALTER TABLE %I.tenants ADD CONSTRAINT tenants_apartment_id_fkey '
            'FOREIGN KEY (apartment_id) REFERENCES %I.apartments(id) ON DELETE RESTRICT',
            s, s
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'payments_tenant_id_fkey'
                     AND connamespace = s::regnamespace) THEN
        EXECUTE format(
            'ALTER TABLE %I.payments ADD CONSTRAINT payments_tenant_id_fkey '
            'FOREIGN KEY (tenant_id) REFERENCES %I.tenants(id) ON DELETE RESTRICT',
            s, s
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'payment_allocations_payment_id_fkey'
                     AND connamespace = s::regnamespace) THEN
        EXECUTE format(
            'ALTER TABLE %I.payment_allocations ADD CONSTRAINT payment_allocations_payment_id_fkey '
            'FOREIGN KEY (payment_id) REFERENCES %I.payments(id) ON DELETE CASCADE',
            s, s
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'payment_allocations_tenant_id_fkey'
                     AND connamespace = s::regnamespace) THEN
        EXECUTE format(
            'ALTER TABLE %I.payment_allocations ADD CONSTRAINT payment_allocations_tenant_id_fkey '
            'FOREIGN KEY (tenant_id) REFERENCES %I.tenants(id) ON DELETE RESTRICT',
            s, s
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'pending_payments_tenant_id_fkey'
                     AND connamespace = s::regnamespace) THEN
        EXECUTE format(
            'ALTER TABLE %I.pending_payments ADD CONSTRAINT pending_payments_tenant_id_fkey '
            'FOREIGN KEY (tenant_id) REFERENCES %I.tenants(id) ON DELETE CASCADE',
            s, s
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'pending_payments_payment_id_fkey'
                     AND connamespace = s::regnamespace) THEN
        EXECUTE format(
            'ALTER TABLE %I.pending_payments ADD CONSTRAINT pending_payments_payment_id_fkey '
            'FOREIGN KEY (payment_id) REFERENCES %I.payments(id) ON DELETE SET NULL',
            s, s
        );
    END IF;

    -- default per-account settings
    EXECUTE format(
        'INSERT INTO %I.app_settings (key, value) VALUES '
        '(%L, %L), (%L, %L), (%L, %L), (%L, %L), (%L, %L) '
        'ON CONFLICT (key) DO NOTHING',
        s,
        'currency', 'MRU',
        'report_enabled', 'true',
        'report_time', '08:00',
        'upcoming_days', '3',
        'prepayment_prompt', 'true'
    );

    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO public', s
    );
    EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO public', s);
END $$;

-- every account schema created in the future keeps working with the same DDL
COMMENT ON FUNCTION app_provision_account(BIGINT) IS
    'Create the private schema + business tables for one account (idempotent).';
