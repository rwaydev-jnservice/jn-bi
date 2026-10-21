# JN Service BI (V1)

Internal web app for JN Service HVAC & Plumbing that combines **Jobber** and
**Pipedrive** into one management view: estimates, service jobs, quotes,
conversions, technician schedules, and Pipedrive process gaps.

```
Jobber API ─────┐  (read only)
                ├── worker.py (sync every N min) ──► PostgreSQL ◄── Streamlit app (login) ◄── browser
Pipedrive API ──┘  (read only)
```

## Safety rules (built into the code, not just policy)

- **Jobber is read only.** `jobber/client.py` parses every GraphQL document
  before sending and refuses anything that is not a `query`. There is no
  mutation method. Tested in `tests/test_jobber_client.py`.
- **Pipedrive is read only.** `pipedrive/client.py` only issues `GET`; any
  other HTTP method raises before a request is made.
- The app **finds** gaps. People fix them in Jobber/Pipedrive.
- No credentials in code. Everything comes from environment variables
  (`.env.example`). Jobber tokens are stored **encrypted** in the database.

## Status

| Phase | What | Status |
|---|---|---|
| 1 | Project, config, DB models, migrations, login, deploy config | Done |
| 2 | Jobber read-only client, OAuth, schema checks, pagination, sync | Built and tested offline. **Needs Jobber app credentials for the live run** |
| 3 | Relationships (DIRECT / INFERRED / REVIEW_REQUIRED), Job #2718 regression | Built and tested with fixtures. Live check: `scripts/validate_jobber.py` |
| 4 | Pipedrive sync | Deals (all open + changed since history start), users, pipelines, stages, stage history. Activities detail still to do |
| 5 | Quote → Deal matching + segments | Contact matching (email/phone + timing, value tie-break) and segment from the deal's **Category**. An exact link comes from the Pipedrive deal field **"Quote #"** (created 2026-10-21; "Jobber Quote #/ID/Number" also recognized). It accepts `105733`, `#105733` or several numbers separated by commas, and wins over contact matching |
| 6 | Process audit + reconciliation | Expected vs actual stage for quotes, requests with an assessment, and direct service jobs; value/status reconciliation. **Pipeline Audit** and **Reconciliation** pages live. Pipeline 5 (Subcontractors) is excluded |
| 7 | Metrics + quote classification | **Executive**, **Estimates** and **Service** pages, with filters (segment, salesperson, technician, flow, department, category, system, Pipedrive owner). The rules classifier is tuned on JN line-item names (`services/classification.py`) |
| 7b | **Quote origin engine** (`services/quote_origin.py`) | Classifies every quote as Estimate DIRECT, Estimate INFERRED, Service INFERRED, Review required or Other/unlinked, in that precedence order. Results are shown on the **Quote Origin Review** page and in `scripts/quote_origin_report.py`. Dashboards switch to it only when `USE_QUOTE_ORIGIN_ENGINE=true` |
| 8–11 | Technician schedule, opportunity detail, validation, deploy | Not started |

**Metric definitions** (also in `services/metrics.py`):
- **Presented:** the quote was sent, or converted on the spot without being sent.
- **Salesperson conversion:** of the quotes presented in the period, the share converted so far. This is a cohort rate.
- **Value conversion:** the same calculation, using dollars instead of counts.
- **Converted (in period):** conversions that happened in the period.
- **Average ticket:** the converted value in the period divided by the converted count.
- **GC / Project Work** bids are excluded by default and can be added back through the Filters.

History is synced from `JOBBER_HISTORY_START` (currently **2026-09-01**, by request).

Segments follow the Pipedrive deal Category, using the same mapping as the existing Pipedrive reports:
- Reactivation, Small Jobs, Big Jobs - Homeowners → **Residential**
- Big Contracts - Maintenance → **Property Management**
- Big Jobs - GCs → **GC / Project Work**

A quote with no matched deal is "No deal match". A matched deal with an empty Category is "Uncategorized".

Contact matches at confidence **0.75** matched on only one channel (email or phone) and the value doesn't confirm them. Treat them as weaker. GC contacts often have several projects, so the specific deal can be wrong even when the segment is right.

## How the Jobber data is pulled (from the real 2026-05-12 schema)

The full schema is saved in `jobber/schema_snapshot/`. Every query in
`jobber/queries.py` is validated against it by the tests, so a wrong field
name fails the tests instead of failing in production.
(`python -m jobber.schema validate` runs the same check.)

| Object | How we know what changed |
|---|---|
| Users | Full pull each run (small) |
| Clients, Requests, Quotes, Invoices | `updatedAt` filter since the last sync |
| Jobs | No `updatedAt` filter exists, so: sort by `UPDATED_AT` and stop at the last sync |
| Visits | Rolling window by `startAt` (45 days back to 60 days ahead) plus `completedAt` since the last sync |
| Assessments | No top-level query exists, so: from each Request, plus `scheduledItems(ASSESSMENT)` over the same window |

Each run re-reads a 10-minute overlap. Watermarks only move forward when an
entity syncs successfully. Failures are recorded in `sync_runs` and shown as
warnings in the app.

## Relationship rules

- **DIRECT**: exposed by the API (Request→Quote, Request→Job, Request→Assessment,
  Quote→converted Job via `Job.quote`, Job→Visit, Job→Invoice).
- **INFERRED**: Service Job → Repair Quote when exactly one strong candidate exists.
  "Strong" means all of these: same `client_id` **and** same `property_id`,
  the quote has no request, the job was not itself created from a quote, and
  the quote was created during the job's lifecycle (up to `INFERENCE_WINDOW_DAYS`
  after its last visit or completion). Names, addresses or dates alone never
  create a link.
- **REVIEW_REQUIRED**: more than one strong candidate. All candidates are stored with their signals.
- A link that a person confirms is never overwritten by later syncs.

Regression case (in tests and in the live validation script):
Job #2718 → Quote #105733 = **INFERRED**; Quote #105733 → Job #2729 = **DIRECT**.

## Local setup (Windows)

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env
.venv\Scripts\python scripts\local_db.py
```

`local_db.py` prints a `DATABASE_URL`. Paste it into `.env`. Also fill in
`APP_SECRET_KEY` and the `BOOTSTRAP_ADMIN_*` values. Then run:

```bash
.venv\Scripts\alembic upgrade head
.venv\Scripts\streamlit run app.py
```

Tests (they start their own throwaway Postgres):

```bash
.venv\Scripts\python -m pytest -q
```

## Connecting Jobber

1. Go to **developer.getjobber.com**. Sign in with the Jobber account that owns
   JN Service, or ask the account owner to do it.
2. Click **Create App**. Fill in:
   - **App name:** `JN Service BI` (internal)
   - **OAuth Callback URL:** the app address, exactly as in `APP_BASE_URL`.
     Use `http://localhost:8501` for local testing; you can add the Render URL later.
   - **Scopes:** tick only the **Read** options for Clients, Requests, Quotes,
     Jobs, Scheduled items / Visits, Invoices and Users. Do **not** tick any Write option.
3. Save. Copy the **Client ID** into `JOBBER_CLIENT_ID` and the **Client Secret**
   into `JOBBER_CLIENT_SECRET`. Put them in `.env` locally, or in Render's
   Environment tab in production. Never paste them into chat.
4. In the app, sign in as an admin. Open **Connections** and click
   **Connect Jobber**. A Jobber admin approves, and Jobber sends you back to the app.
5. Run `python scripts/validate_jobber.py`. It pulls a small sample of every
   object and checks the Job #2718 chain. Results are written to
   `validation_output/jobber_validation.md`.

## Users and roles

- **ADMIN**: everything, including Connections, Sync Status and Users.
- **MANAGEMENT**: all dashboards.
- **VIEW_ONLY**: all dashboards. This is kept separate so it can be limited later.

The first admin is created automatically from `BOOTSTRAP_ADMIN_EMAIL` and
`BOOTSTRAP_ADMIN_PASSWORD`, only when there are no users yet. Remove the
password variable after the first login. Admins add other people on the
**Users** page. A terminal alternative is `scripts/manage_users.py`.
After 5 wrong passwords, the account is locked for 15 minutes.

## Deploying on Render

`render.yaml` creates three things: the web app, the sync worker, and a
Postgres database.

1. Push this folder to a **private** GitHub repo.
2. In Render: **New → Blueprint**, then pick the repo.
3. When Render asks, fill in:
   - `JOBBER_CLIENT_ID`, `JOBBER_CLIENT_SECRET`
   - `PIPEDRIVE_API_TOKEN`, `PIPEDRIVE_COMPANY_DOMAIN`
   - `APP_BASE_URL` (the `https://…onrender.com` address)
   - `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`

   `APP_SECRET_KEY` is generated automatically.
4. Add the Render URL as a callback URL in the Jobber app (step 2 above).
5. Database migrations run automatically on every deploy (`preDeployCommand`).
6. Optional custom domain such as `bi.jnservicehvac.com`: Render → web service →
   **Settings → Custom Domains**. Then add the CNAME record Render shows at your
   DNS provider, and update `APP_BASE_URL` and the Jobber callback URL.

The sync interval is set with `SYNC_INTERVAL_MINUTES` (default 15). The
worker uses a database lock, so two syncs never overlap.

## Project layout

```
app.py                 login + navigation (Streamlit)
worker.py              background sync loop
config.py              all settings (env vars)
database/              models.py, connection.py, migrations/ (Alembic)
jobber/                client.py (read-only), oauth.py, queries.py, schema.py, normalize.py, schema_snapshot/
pipedrive/             client.py (read-only, GET only)
services/              auth, crypto, sync_jobber, relationships, periods (Boston time), freshness, ui helpers
views/                 dashboard pages (named views/ so Streamlit never shows them before login)
scripts/               local_db.py, manage_users.py, validate_jobber.py
tests/                 64 tests incl. schema validation, read-only guards, regression case
```

## Known gaps and decisions to revisit

- **Login lasts one browser session.** Refreshing the page asks for the
  password again. If this gets annoying, switch to Google/Microsoft sign-in
  (Streamlit's built-in OIDC), which keeps people logged in.
- **Line items are capped at 50 per quote, and linked IDs at 10–20 per object**
  (to keep Jobber query cost low). This should be plenty for HVAC quotes.
  Confirm during the live validation.
- **Visits are only synced inside a rolling window.** Visits more than 45 days
  old are pulled once by the first full sync and then only when they complete.
- **Repair quotes after quote-converted jobs**: only jobs that were **not**
  created from a quote count as "original service jobs" in V1.
- **Jobber scopes and the token refresh flow** follow Jobber's standard OAuth
  flow. They will be confirmed on the first live connection.
