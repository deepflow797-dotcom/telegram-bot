# 🏠 بوت إدارة الإيجارات — Rental Management Telegram Bot

Arabic-first Telegram bot (aiogram 3 + FastAPI + PostgreSQL) that lets a single
property owner manage buildings, apartments, tenants, rent payments and rental
deadlines — replacing manual rent tracking with a reliable automated system.

Tenants never use the bot. The owner collects the money outside Telegram and
registers it with two taps; the bot does the arithmetic and the reporting.

---

## 1. What it does

| Area | Capabilities |
| --- | --- |
| Buildings | add, view, edit name/address, archive, permanent delete (empty buildings only), occupancy counters |
| Apartments | add (per building), view, edit number/floor, archive, permanent delete (no tenants), occupancy status |
| Tenants | add (building → apartment → details), edit any field, search, archive, permanent delete (no payments), profile with balance & history |
| Payments | full / partial / overpayment registration with a confirmation screen, immutable history, duplicate protection |
| Rental cycle | automatic calendar-month cycle rollover on confirmed payment, month-end clamping, arrears accounting |
| Statuses | covered, upcoming (≤ 3 days), due today, overdue, archived — derived from data, never stored by hand |
| Daily report | 08:00 Africa/Nouakchott, summary + four tenant lists, one report per date, retries |
| Reports on demand | today, month, payment history, overdue, upcoming, total revenue, **Excel export** |
| Settings | default currency, report time, enable/disable daily report, delete/archive policy, bot info |
| Accounts | multi-tenant: each Telegram account has its own isolated dataset, subscription and empty start |
| Subscriptions | request → owner approves (30/90 days) → active; on expiry the account is frozen (data kept, renewal screen) |
| Excel export | `📗 تصدير كل البيانات Excel` builds a real `.xlsx` (summary, buildings, apartments, tenants, payments, arrears) with numbers kept numeric and RTL sheets |
| Security | per-account isolation, owner-only admin screens, secret-protected webhook/cron/admin endpoints, audit log |

Everything in the chat is Arabic and button-driven — no commands required
(`/start`, `/menu`, `/cancel`, `/help`, `/id` exist as conveniences).

---

## 2. Architecture

```
                 ┌──────────────────────────┐
  Telegram ──►    │  Vercel (or Docker host) │
  webhook         │  FastAPI  /api/telegram/…│──┐
                  │  aiogram 3 dispatcher    │  │
  Vercel Cron ──► │  /api/cron/daily-report  │  │
  08:00 UTC       └──────────────────────────┘  │
                                                 ▼
                                        ┌───────────────────┐
                                        │ PostgreSQL        │
                                        │ (Supabase)        │
                                        │ tenants, payments │
                                        │ reports, fsm, …   │
                                        └───────────────────┘
```

Key decisions and *why*:

* **Webhook, not polling.** Serverless hosts cannot keep a polling process alive.
  Telegram POSTs updates to `/api/telegram/webhook`; the FastAPI route validates
  the secret header, de-duplicates by `update_id`, then feeds the update into
  aiogram's dispatcher manually (`dp.feed_update`). This runs identically on
  Vercel, any Docker host (`uvicorn`), or a laptop.
* **FastAPI instead of aiogram's aiohttp server.** Vercel's Python runtime
  speaks ASGI; a plain `aiohttp` webhook app is not an ASGI application.
* **Conversation state in PostgreSQL.** A serverless platform may serve the next
  button tap from a *different* instance, so a memory FSM would lose the form.
  `app/db/fsm.py` implements an aiogram `BaseStorage` on top of a `bot_fsm_states`
  table.
* **Connection-string database access (`DATABASE_URL`)** rather than Supabase's
  REST API: payment registration must be one atomic SQL transaction with row
  locks and a UNIQUE idempotency key. `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`
  are accepted for future use but are **not** required.
* **`NUMERIC(12,2)` for money, `Decimal` in Python.** No floats anywhere.
* **Money only changes in one function.** `app/core/rent.py::plan_payment` is a
  pure function (no clock, no I/O) that computes allocations and the new cycle;
  `app/db/payments.py::register_payment` persists it in a single transaction.

---

## 3. Rental cycle & payment rules (documented policy)

**Month-end policy.** The next due date is the same day-of-month as the anchor,
clamped to the last day of the target month:

| Anchor | Next due |
| --- | --- |
| 15/01/2026 | 15/02/2026 |
| 31/01/2023 | 28/02/2023 |
| 31/01/2024 | 29/02/2024 (leap) |
| 31/03/2026 | 30/04/2026 |
| 30/05/2026 | 30/06/2026 |

A calendar month is never modelled as 30 days.

**Rolling cycle.** A payment that completes the amount required for the pending
period makes the new period start on the **actual payment date**:
pay on 15/01 → covered 15/01 → next due 15/02.

**What the tenant state means.**
* `current_cycle_start … next_due_date` = the currently funded period.
  `next_due_date` is when the next payment is due.
* `credit_amount` = money already received but not yet needed (partial payment
  or overpayment). It is never discarded.
* A new tenant starts with `next_due_date = rental_start_date` (first month is
  due at move-in) and an empty cycle start one month earlier.

**Partial payments** are recorded, the due date does **not** move, and the status
stays “due today/overdue” until the period is fully covered. Multiple partial
payments accumulate; the payment that completes the period triggers the rollover.

**Overpayments** are recorded in full. Extra money funds following months
(one month at a time), and any remainder stays as `credit_amount` shown in the
profile as “رصيد دفعات مقدمة”.

**Arrears.** While unpaid, `next_due_date` stays fixed. Every complete calendar
month elapsed after it adds one month of rent to the amount required to become
current, so a tenant who stopped paying is never shown as up to date.

**Statuses** (`app/core/rent.py::status_for`):

| Status | Rule |
| --- | --- |
| Covered / paid | `today < next_due - 3 days` (current period funded) |
| Upcoming | `0 < next_due - today ≤ 3` |
| Due today | `next_due == today` |
| Overdue | `next_due < today` |
| Archived | tenant inactive |

Nothing in the system resets a timer except a confirmed payment: viewing
profiles, reports, notifications, editing details and sending the daily report
never touch cycle dates.

---

## 4. Project structure

```
api/index.py                 Vercel entrypoint (exports the ASGI app)
app/
  config.py                  environment validation (collects all problems)
  logging_config.py          JSON logs with secret redaction
  core/
    dates.py                 month math, Arabic digit/amount/date parsing
    rent.py                  statuses, balances, payment allocation (pure)
    reports.py               report data models + Arabic renderers
    formatting.py            money/date/status labels
    xlsx.py                  dependency-free .xlsx writer (OOXML zip)
  db/
    migrations/0001_init.sql schema (buildings, apartments, tenants, payments,
                             allocations, pending payments, daily reports,
                             settings, audit log, processed updates, FSM)
    migrate.py               forward-only migration runner
    connection.py            async psycopg pool, transactions
    repository.py            buildings, apartments, settings, audit, updates
    tenants.py               tenant CRUD, apartment transfer, stats
    payments.py              pending payments, atomic registration, history
    reports.py               daily report claim/finish/fail
    fsm.py                   PostgreSQL FSM storage
  bot/
    dispatcher.py            routers, middlewares, bot factory
    middlewares.py           owner-only access + context injection
    keyboards.py             all inline/reply keyboards
    views.py                 cards, receipts, success messages
    handlers/                common, properties, edits, tenants, payments,
                             reports, search, settings, deletes, errors
  services/
    reports_data.py          report data collection (bot + cron share it)
    daily_report.py          claim → render → send → record (idempotent)
    excel_export.py          builds the account's .xlsx workbook
  web/
    app.py                   FastAPI routes: webhook, cron, admin, health
    routing.py               restores the client path after the Vercel single-function rewrite
    security.py              constant-time secret checks
scripts/                     migrations, webhook setup, daily report, dev polling
tests/                       161 tests against real PostgreSQL
docs/DEPLOYMENT.md           guided external setup (Supabase, Telegram, Vercel)
docs/DATABASE.md             schema reference
```

---

## 5. Local setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then fill in real values
```

Get a PostgreSQL database (either works):

```bash
# option A — Docker
docker run --name rental-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:16
# DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres

# option B — free hosted database (Supabase, see docs/DEPLOYMENT.md step 1)
```

Apply the schema and start the API:

```bash
python -m app.db.migrate            # or: python scripts/apply_migrations.py
uvicorn api.index:app --reload --port 8000
curl localhost:8000/api/health      # {"status":"ok","database":"up",...}
```

Try the bot on your machine **without** a public URL (long polling):

```bash
python scripts/dev_polling.py
```

Before going live in webhook mode, remove polling and register the webhook:

```bash
python scripts/set_webhook.py https://<your-host>/api/telegram/webhook
```

---

## 6. Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | bot token from @BotFather |
| `TELEGRAM_OWNER_ID` | yes | the only Telegram user allowed to use the bot |
| `TELEGRAM_WEBHOOK_SECRET` | yes | Telegram sends it in `X-Telegram-Bot-Api-Secret-Token`; 16–256 chars `A-Za-z0-9_-` |
| `DATABASE_URL` | yes | PostgreSQL DSN (Supabase pooler string recommended); `SUPABASE_DB_URL` also accepted |
| `CRON_SECRET` | yes | ≥16 chars; protects `/api/cron/*` and `/api/admin/*` |
| `APP_TIMEZONE` | no | default `Africa/Nouakchott` (UTC+0, no DST) |
| `DEFAULT_CURRENCY` | no | default `MRU` (Mauritanian ouguiya) |
| `WEBHOOK_URL` | no | public webhook URL; with `AUTO_SETUP` it is registered automatically |
| `AUTO_SETUP` | no | `true` = on the first request apply missing migrations and register `WEBHOOK_URL` (idempotent, safe on every cold start) |
| `LOG_LEVEL` | no | default `INFO` |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | no | optional Supabase metadata, unused by the app |

Secrets are never logged (JSON logs redact bot tokens, DSN passwords and
`secret=…` pairs) and `.env` is git-ignored. `.env.example` contains placeholders
only.

---

## 7. Database setup

* Schema: `app/db/migrations/0001_init.sql` (business tables, originally single-owner)
  and `app/db/migrations/0002_accounts.sql` (multi-account platform) — see
  [docs/DATABASE.md](docs/DATABASE.md).
* `0002` moves the existing tables into the schema `acct_1` (the owner's private
  dataset), creates `public.accounts` + `public.platform_settings`, and installs
  `app_provision_account(id)` which builds an empty `acct_<id>` for every new
  customer. It is safe to run on a database that already has data.
* Apply with `python -m app.db.migrate`, the script
  `python scripts/apply_migrations.py`, or the protected endpoint
  `POST /api/admin/migrate` (header `Authorization: Bearer $CRON_SECRET`) — handy
  right after the first Vercel deploy, when only the platform knows the DSN.
  All three are idempotent, run each file in a transaction, record versions in
  `schema_migrations` and serialise concurrent runs with a Postgres advisory lock.
* `/api/health` reports `"schema": "ready" | "missing"` so a deployment with an
  un-migrated database is visible immediately.
* For Supabase, use **Settings → Database → Connection string → Session pooler**
  as `DATABASE_URL`. The pool is created with `prepare_threshold=None` so it also
  works through the transaction pooler.
* Data is fully persistent: nothing lives in the serverless filesystem.

---

## 8. Creating the Telegram bot

1. Open Telegram, talk to **@BotFather**, send `/newbot`, choose a name and a
   username ending in `bot`.
2. Copy the token (`123456789:AA…`) into `TELEGRAM_BOT_TOKEN`.
3. Send `/start` to your new bot from your own account, then send `/id` —
   **after** deployment — to read your user id, or ask **@userinfobot**. Put that
   number into `TELEGRAM_OWNER_ID`.
4. Generate the two secrets:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`

Any other Telegram account that messages the bot gets “⛔ هذا البوت خاص بالمالك فقط.”

---

## 9. Webhook configuration

With `AUTO_SETUP=true` and `WEBHOOK_URL` set, **nothing to do**: the first
request (health check or Telegram update) applies the migration if needed and
registers the webhook + commands once, recording the URL in `app_settings`.

Otherwise register it manually:

```bash
curl -X POST "https://<your-app>/api/admin/setup-webhook" \
  -H "Authorization: Bearer $CRON_SECRET"
# or, from your machine:
python scripts/set_webhook.py https://<your-app>/api/telegram/webhook
```

This calls `setWebhook` with `secret_token`, `allowed_updates`
(`message`, `edited_message`, `callback_query`, `my_chat_member`) and registers
the bot commands. Verify with:

```bash
curl "https://<your-app>/api/admin/webhook-info" -H "Authorization: Bearer $CRON_SECRET"
```

The webhook endpoint returns **401** for a missing/wrong secret header, **400**
for invalid JSON and **200** for valid updates — including duplicates, which are
ignored (Telegram retries are safe).

---

## 10. Deployment

### Vercel (primary)

1. Push the repository to GitHub (already done for this branch).
2. vercel.com → **Add New Project** → import the repo. No build command needed;
   Vercel detects `api/index.py` (FastAPI/ASGI) and installs `requirements.txt`.
3. Add every environment variable from §6 (Production + Preview).
4. Deploy, then open `https://<app>/api/health` → `{"status":"ok","database":"up"}`.
   With `AUTO_SETUP=true` the schema and webhook were already handled on that
   request (see §9).
5. The daily report is scheduled by `vercel.json`:
   `{"path": "/api/cron/daily-report", "schedule": "0 8 * * *"}` — Vercel Cron
   runs in **UTC**, and Mauritania is UTC+0 all year, so 08:00 UTC = 08:00
   Nouakchott. Vercel sends `Authorization: Bearer $CRON_SECRET` automatically.
   If you change the report time in the bot settings, change the cron schedule too.
6. `maxDuration` is set to 60 s in `vercel.json`; lower it if your plan requires.

### Any Docker host (Render, Fly.io, VPS)

```bash
docker build -t rental-bot .
docker run -p 8000:8000 --env-file .env rental-bot
```

Then register the webhook pointing at the public URL. For the daily report, either
call the cron endpoint with `CRON_SECRET` from the platform's scheduler, or run
`python scripts/send_daily_report.py` from a cron/systemd timer (idempotent).

**Vercel caveats worth knowing:** Hobby cron jobs run at most once per day (fine
for a daily 08:00 report) and may start a few minutes late; the report endpoint is
idempotent, so a late or repeated invocation never produces two reports.

---

## 11. Excel export

`📊 التقارير → 📗 تصدير كل البيانات Excel` sends a real `.xlsx` workbook named
`rentals-YYYY-MM-DD.xlsx`. Sheets: **ملخص / المباني / الشقق / المستأجرون /
الدفعات / المتأخرات**. Money cells are numbers (so `SUM`/filters work in Excel or
Sheets), sheets are `rightToLeft="1"` with a frozen header row, and the rows come
from the account's own schema, so a customer's file never contains another
account's data.

The writer lives in `app/core/xlsx.py` and has **no third-party dependency** (a
zip plus a handful of OOXML parts), keeping the Vercel bundle small; `openpyxl`
is only a dev dependency, used by the tests to read the generated file back.

### Archiving vs permanent delete

Cards carry two destructive actions:

* `🗄 أرشفة` — soft delete (`is_active = false`, `archived_at`): hidden from
  lists, everything kept, payments and history intact. This is the normal path.
* `🗑 حذف نهائياً` — row delete with a confirmation step and dependency guards:
  a building with apartments, an apartment with any tenant (even archived), or a
  tenant with payments cannot be deleted. Every delete is written to `audit_log`.

Both actions are **per-account settings**: `⚙️ الإعدادات → 🗑 الحذف والأرشفة`
toggles `allow_delete` and `allow_archive` (default `true`). When a flag is off
the matching button disappears from every card and a hand-made callback is
refused (the guard against deleting records with history is not configurable).
`ContextMiddleware` resolves the flags once per update into
`app/bot/policy.py`, so keyboard builders never touch the database.

---

## 12. Daily report

* Time: **08:00 Africa/Nouakchott** (UTC+0), configurable in ⚙️ الإعدادات.
* Content: active tenants, covered / due-today / overdue / upcoming counts,
  expected monthly rent, **collected today** and **collected this month**
  (kept separate), outstanding balance, then four tenant sections with inline
  buttons (view tenant, register payment).
* Long reports are split below Telegram's 4096-character limit.
* Exactly one report per report date: `daily_reports.report_date` is UNIQUE and a
  claim token protects concurrent invocations. Failed runs are recorded with the
  error and retried (max 3 attempts, stale claims reclaimed after 15 minutes).
* Sending a report never modifies payment data or rental dates.

---

## 13. Tests

```bash
pip install -r requirements-dev.txt
python -m pytest            # 130 tests, real PostgreSQL via embedded pgserver
```

Coverage (all executed, not asserted on paper):

| Suite | What it proves |
| --- | --- |
| `test_dates.py` | month-end arithmetic, leap years, Arabic digit/amount parsing |
| `test_rent.py` | statuses, full/partial/multiple/overpayments, arrears, rollover |
| `test_database.py` | schema constraints, one active tenant per apartment, archiving, edits don't reset cycles |
| `test_payments.py` | atomic registration, duplicate confirmation, history after archive, month-end rollover |
| `test_daily_report.py` | counts, today vs month totals, timezone, one-per-date, failure + retry, no data mutation |
| `test_web.py` | webhook secret rejection, unauthorized user rejection, duplicate update, cron secret, setup-webhook, health/misconfiguration, schema-missing detection + `/api/admin/migrate` + `AUTO_SETUP` bootstrap |
| `test_bot_flow.py` | full owner journey through real updates: building → apartment → tenant → payment → duplicate tap → reports → search → archive |
| `test_config.py` | missing/invalid env vars reported together, DSN redaction |
| `test_vercel_routing.py` | rewritten requests (`/api/index?__path=/api/health`) reach the real FastAPI route, direct function paths fall back to `/`, broken boots return a JSON diagnostic |

Last run: **130 passed** (`python -m pytest`).

---

## 14. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `/api/health` → 503 with `problems` | env vars missing/invalid; the list names each one |
| Bot silent, webhook info shows 401 | `TELEGRAM_WEBHOOK_SECRET` differs from the registered `secret_token`; re-run setup-webhook |
| Bot silent, `last_error_message` mentions 404 | webhook URL must end with `/api/telegram/webhook` |
| `permission denied` / `relation does not exist` | migrations not applied → `python -m app.db.migrate` |
| `too many connections` | use Supabase's pooler DSN, or lower pool `max_size` in `app/db/connection.py` |
| Daily report never arrives | cron schedule (UTC), `report_enabled` setting, or `CRON_SECRET` mismatch; check `daily_reports` rows and logs |
| `can't parse entities` | shouldn't happen: all dynamic text is HTML-escaped before sending |
| Bot replies “انتهت صلاحية هذه العملية” | the confirmation screen is older than the pending payment or already consumed — start again |

---

## 15. Security notes

* Access is enforced in an aiogram **outer** middleware, before any handler or
  query runs. Unknown users get a request screen only; pending, rejected and
  frozen accounts see a status screen and nothing else.
* Every account reads and writes its **own PostgreSQL schema** (`acct_<id>`).
  The middleware sets the active account for the update and `Database`
  switches `search_path` on each pooled connection, so a customer query cannot
  return the owner's rows even if a filter is forgotten.
* Owner-only screens (`📥 طلبات الاشتراك`, `📞 بيانات التواصل`, account list) are
  both hidden from other accounts' keyboards **and** re-checked inside every
  handler with the injected `is_owner` flag.
* Webhook authenticity via Telegram's secret-token header, compared with
  `hmac.compare_digest`. Cron/admin endpoints require `Authorization: Bearer
  $CRON_SECRET` (or `X-Cron-Secret`), also constant-time.
* All SQL is parameterised; every mutating operation validates entity ownership
  and existence (`⚠️ لم يتم العثور…`).
* Payments are append-only; duplicates are blocked by a UNIQUE idempotency key
  plus a consumed `pending_payments` row inside the same transaction.
* `audit_log` records building/apartment/tenant creation, edits, archiving,
  payments and settings changes.
* Secrets never reach logs, `.env.example`, or the repository.

---

## 16. Accounts & subscriptions (multi-tenant)

The bot is a product, not a personal tool: one deployment serves many customers.

| Step | What happens |
| --- | --- |
| Someone presses `/start` for the first time | a **free trial** starts automatically (7 days by default): the account becomes `active`, its own empty `acct_<id>` schema is provisioned, and the owner is notified with **upgrade 30 / 90 days / pause** buttons |
| Trial screen | the customer sees the trial length, its end date and the owner's phone, and starts working immediately |
| Trial switched off (`trial_days = 0`) | a new account instead becomes a `pending` request; the owner gets the applicant's name, username and Telegram id with **✅ 30 / ✅ 90 days / ❌ reject** buttons, and the applicant only sees the contact screen |
| Owner approves | the customer's own `acct_<id>` schema is provisioned **empty**, `paid_until` is set, and the customer receives an activation message |
| Owner rejects | the applicant sees a rejection screen; nothing is provisioned |
| Subscription ends | status becomes `frozen` automatically (`effective_status`): no edits, a renewal screen with the contact number, **all data kept** |
| Renewal | the owner extends from `📥 طلبات الاشتراك` / `👤 الحسابات` — the account is active again with the same data |

* Daily reports run per account: `/api/cron/daily-report` loops over the active
  accounts (trials included) and sends each owner/client their own report for
  their own dataset.
* The owner edits the contact phone, the request note **and the trial length**
  from inside the bot (`📞 بيانات التواصل` → `🎁 مدة التجربة المجانية`, or the
  `🎁 التجربة المجانية` button), stored in `public.platform_settings`
  (`trial_days`, 0–365, `0` = trial off → request flow).
* Changing the trial length only affects accounts created afterwards; existing
  trials keep their dates, and `➕ تمديد` extends from the current end date.
* Account 1 is always the platform owner (`TELEGRAM_OWNER_ID`); it never
  expires and never sees a subscription screen.
