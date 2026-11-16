# Deployment guide — what is done and what needs your account

The application is complete and tested **in this repository**. The remaining
steps need accounts only you can create (Supabase, Telegram, Vercel). Follow them
in order; each step tells you exactly what to copy and where it goes.

## What is already done

- [x] Full implementation (bot, database layer, reports, webhook, cron endpoint)
- [x] SQL migrations (`app/db/migrations/0001_init.sql`)
- [x] 116 automated tests against a real PostgreSQL instance — all passing
- [x] Vercel configuration (`vercel.json`, `api/index.py`, `requirements.txt`)
- [x] Docker alternative (`Dockerfile`)
- [x] Helper scripts: migrations, webhook setup, daily report, local polling
- [x] Security: owner-only access, webhook secret, cron secret, audit log
- [x] Documentation (README, this guide, database reference)

## What still needs you

| # | Step | Where | Needed for |
| --- | --- | --- | --- |
| 1 | Create a free Supabase project + copy the database URL | supabase.com | persistent database |
| 2 | Create the bot with @BotFather + get your Telegram user id | Telegram | bot token and owner id |
| 3 | Create a Vercel project, add env vars, deploy | vercel.com | hosting |
| 4 | Register the webhook (one command) | your machine / browser | bot starts answering |
| 5 | Verify: `/start`, add a building, register a test payment, trigger the cron | Telegram + browser | acceptance |

---

## Step 1 — Database (Supabase)

1. Open <https://supabase.com> → **Start your project** → sign in with GitHub.
2. **New project**: name `rental-bot`, choose the region closest to Mauritania
   (`eu-central-1` / `eu-west-*` work well), set a database password and **save
   that password**.
3. Wait until the project status is *Active* (1–2 minutes).
4. Go to **Project Settings → Database → Connection string → URI** and copy the
   **Session pooler** string. It looks like:

   ```
   postgresql://postgres.abcdefgh:YOUR-PASSWORD@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```

5. Replace `YOUR-PASSWORD` with the password from step 2 → this is your
   `DATABASE_URL`.
6. Apply the schema. Two options — pick one:

   *From your machine:*

   ```bash
   pip install -r requirements.txt
   DATABASE_URL="<the string from step 5>" python -m app.db.migrate
   ```

   *After deploying (step 3), from a browser/curl — no password sharing needed:*

   ```bash
   curl -X POST "https://<your-app>.vercel.app/api/admin/migrate" \
     -H "Authorization: Bearer <CRON_SECRET>"
   ```

   Expected result: `{"applied": ["0001_init"], "schema_ready": true}`.
   `GET /api/health` shows `"schema": "ready"` afterwards; before that it returns
   503 with `"schema": "missing"` and a hint.

> Prefer a different Postgres? Any provider works — only `DATABASE_URL` matters.

## Step 2 — Telegram bot and owner id

1. In Telegram open **@BotFather** → `/newbot`.
2. Choose a display name (e.g. `إدارة الإيجارات`) and a username ending in `bot`
   (e.g. `my_rentals_2026_bot`).
3. Copy the token (`123456789:AAE…`) → this is `TELEGRAM_BOT_TOKEN`.
4. Find your numeric Telegram id: message **@userinfobot** (or, after deployment,
   send `/id` to your bot) → this is `TELEGRAM_OWNER_ID`.
5. Generate the two random secrets (run twice, or use any password generator):

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   → first value = `TELEGRAM_WEBHOOK_SECRET`, second = `CRON_SECRET`.

## Step 3 — Deploy to Vercel

1. Open <https://vercel.com/new> and import this GitHub repository
   (`deepflow797-dotcom/telegram-bot`).
2. Framework preset: **Other**. Leave build/output settings empty — Vercel
   detects `api/index.py` and installs `requirements.txt`.
3. **Environment Variables** (Production and Preview) — add exactly:

   | Name | Value |
   | --- | --- |
   | `TELEGRAM_BOT_TOKEN` | step 2.3 |
   | `TELEGRAM_OWNER_ID` | step 2.4 |
   | `TELEGRAM_WEBHOOK_SECRET` | step 2.5 |
   | `DATABASE_URL` | step 1.5 |
   | `CRON_SECRET` | step 2.5 |
   | `APP_TIMEZONE` | `Africa/Nouakchott` |
   | `DEFAULT_CURRENCY` | `MRU` (or `USD`, `EUR`, …) |

4. Deploy. Then open `https://<your-app>.vercel.app/api/health`.
   Success looks like:
   `{"status":"ok","database":"up","timezone":"Africa/Nouakchott","today":"…"}`.
   If it says `misconfigured`, the JSON lists exactly which variable is missing.

The daily report schedule is already in `vercel.json`
(`0 8 * * *`, UTC = 08:00 Nouakchott). Vercel sends `Authorization: Bearer
$CRON_SECRET` automatically.

## Step 4 — Register the webhook

Either from a browser/curl:

```bash
curl -X POST "https://<your-app>.vercel.app/api/admin/setup-webhook" \
  -H "Authorization: Bearer <CRON_SECRET>"
```

or from your machine (after `pip install -r requirements.txt`):

```bash
export TELEGRAM_BOT_TOKEN=…  TELEGRAM_OWNER_ID=…  TELEGRAM_WEBHOOK_SECRET=…  CRON_SECRET=…
python scripts/set_webhook.py https://<your-app>.vercel.app/api/telegram/webhook
```

Expected: `webhook set to https://…/api/telegram/webhook`, `pending updates: 0`.

## Step 5 — Verify (acceptance)

1. Send `/start` to your bot in Telegram → the Arabic main menu appears.
2. Ask a family member/another account to send `/start` → they must get
   “⛔ هذا البوت خاص بالمالك فقط.”
3. 🏠 إدارة العقارات → ➕ إضافة مبنى → add one, then ➕ إضافة شقة.
4. 👥 المستأجرون → ➕ إضافة مستأجر → complete the form, confirm.
5. 💰 تسجيل الدفع → pick the tenant → amount → confirm → success message shows
   the new due date. Tap the same “confirm” twice in a test to see the duplicate
   protection message.
6. 📊 التقارير → 📅 تقرير اليوم → report with the four sections.
7. Browser: `https://<your-app>.vercel.app/api/cron/daily-report` with header
   `Authorization: Bearer <CRON_SECRET>` → `{"status":"sent", …}`. A second call
   returns `{"status":"skipped","reason":"already_sent"}`. The real report is
   sent automatically every day at 08:00 Nouakchott.
8. Optional: open the Vercel project → **Cron Jobs** tab to confirm the daily job
   is listed.

## Changing the daily report time

The bot has ⚙️ الإعدادات → ⏰ وقت التقرير (stored in the database, used for the
report date and shown in the UI). The *actual trigger* is the Vercel cron
schedule in `vercel.json`; update it (UTC) and redeploy when you change the time.

## Rolling back / maintenance

* Redeploy an older commit from the Vercel dashboard.
* Database migrations are forward-only and additive; there is no destructive
  migration in this release.
* Backups: Supabase → Database → Backups (enable daily backups).

## Troubleshooting quick table

| Symptom | Fix |
| --- | --- |
| `/api/health` 503 `misconfigured` | add the listed env var, redeploy |
| `/api/health` 503 `database: down` | wrong `DATABASE_URL` password, or use the pooler string |
| Bot silent | webhook not registered (step 4), or wrong `TELEGRAM_WEBHOOK_SECRET` |
| `404` in webhook info | the URL must end with `/api/telegram/webhook` |
| Cron returns 401 | `CRON_SECRET` in Vercel ≠ the one you sent |
| No daily report | check `report_enabled` in ⚙️ الإعدادات, the cron tab, and the `daily_reports` table |
