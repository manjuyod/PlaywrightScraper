# Student Grade Checker

PlaywrightScraper collects student grades and agenda data from supported school portals, stores the results in Postgres, and exposes a Flask dashboard for franchise-level monitoring.

## Dashboard

The UI lives in `ui/` and is served by `ui.wsgi:app`.

Routes:

- `/` starts the dashboard auth flow and always redirects to `/login`.
- `/login` allows CRM authentication via `dbo.usp_login` (through `ui/auth.py`). Franchise ID `1` routes to the health dashboard; other valid franchises route to their franchise dashboard.
- `/logout` clears the dashboard session.
- `/health` shows all active franchises, active/background jobs, synced student counts, bad login counts, malformed inputs, nonconfigured portals, and grouped scraper errors.
- `/franchise/<franchise_id>` shows a searchable, sortable student table for one franchise. It includes portal links, recent grade snapshots, low/high grades, standing, status, edit/delete controls, and buttons to refresh grades or agendas for the full franchise.
- `/franchise/<franchise_id>/student/<student_id>` shows one student's report, including current standing, recent grades, agenda items, grade history, and a heatmap view.
- `/status/<job_id>` returns JSON progress for the dashboard's grade and agenda refresh progress bars.

Background dashboard jobs are managed in `ui/ext_jobs.py` with a small thread pool. Franchise-level job IDs use the franchise ID, student-level jobs use `<franchise_id>_<student_id>`, and agenda jobs add the `_agenda` suffix.

## Local Dashboard Run

Install dependencies and Playwright browsers first:

```powershell
uv sync
uv run playwright install
```

For local dashboard development, set a strong session secret and start Flask:

```powershell
$env:SESSION_SECRET = "local-development-session-secret-2026"
uv run flask --app ui.wsgi:app run --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080/` and sign in with CRM credentials.

The Replit/nginx entrypoint is `ui/start.sh`. It runs Gunicorn on `127.0.0.1:3000` and proxies public traffic through nginx on port `8080`.

## Replit Deployment

For Replit Autoscale, keep `.replit` and `replit.nix` configured manually in
Replit; both files are intentionally gitignored for this project. The shared
deployment contract is that Replit builds Python dependencies with
`uv sync --frozen`, verifies or rebuilds the bundled SQL Server ODBC driver,
then starts `ui/start.sh`. Runtime traffic is served by nginx on local port
`8080`, mapped to external port `80`.

Build command:

```bash
uv sync --frozen && bash setup_odbc_build.sh
```

Run command:

```bash
bash ui/start.sh
```

Set these Replit published-app Secrets before deploying:

- `SESSION_SECRET`
- `PGHOST`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`
- `PGPORT`
- `CRMSrvAddress`
- `CRMSrvDb` or `CRMSrvDbQA`
- `CRMSrvUs`
- `CRMSrvPs`

Optional:

- `CRM_TRUST_SERVER_CERTIFICATE=1` allows trusting the CRM SQL Server
  certificate when required by that environment.

The Microsoft ODBC Driver 17 bundle lives in `odbc_driver/`, with its required
resource file in `share/resources/en_US/`. `setup_odbc.sh` registers the driver
as `ODBC Driver 17 for SQL Server` in `$HOME/.odbc/odbcinst.ini`, matching the
dashboard CRM login connection string.

## Configuration

The scraper and dashboard read environment variables from `.env` via `python-dotenv`.

Required for database access:

- `PGHOST`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`
- `PGPORT`

Dashboard/session settings:

- `CRMSrvAddress`, `CRMSrvDb`, `CRMSrvDbQA`, `CRMSrvUs`, `CRMSrvPs` provide CRM authentication connectivity. `CRMSrvDb` is preferred when set, with `CRMSrvDbQA` as fallback.
- `CRM_TRUST_SERVER_CERTIFICATE` controls SQL Server certificate trust (default `no`). `1`, `true`, and `yes` (case-insensitive) are accepted values for enabling `TrustServerCertificate=yes`.
- The CRM SQL Server connection uses encrypted ODBC transport with certificate validation controlled by `CRM_TRUST_SERVER_CERTIFICATE`.
- `SESSION_SECRET` signs Flask sessions. It must be a strong non-default value of at least 32 characters.

The bundled nginx config forwards `X-Forwarded-For` and `X-Forwarded-Proto`; Flask applies trusted proxy handling so login rate limiting can use the real client address behind nginx.

Optional:

- `PYTHON_ENV=dev` affects runner notification behavior.
- `SLACK_WEBHOOK_URL` enables Slack notifications.
- `SLACK_NOTIFY_IN_DEV=1` allows Slack notifications in dev.
- `OPENAI_API_KEY` and `OPENAI_MODEL` are used by GPT-assisted portal utilities.

## Scraper Runs

Run grade collection from the CLI:

```powershell
uv run python -m scraper.runner
uv run python -m scraper.runner --franchise-id 19
uv run python -m scraper.runner --franchise-id 19 --student-id 123
```

Run agenda collection:

```powershell
uv run python -m scraper.agenda --franchise-id 19
```

Batch helpers live in `batches/`, including per-franchise pipelines and `pipeline_all_franchises.bat`.

## Tests

```powershell
uv run pytest -q
uv run pytest -q --run-integration
$env:TEST_FRANCHISE_ID = "19"; uv run pytest -q --run-integration
```

## Portal Development

Portal engines live in `scraper/portals/`.

To add or update a portal:

1. Implement the portal module under `scraper/portals/`.
2. Register the portal key in `scraper/portals/__init__.py`.
3. Make sure `scraper/portals/utils.py` can infer the portal key from the stored portal URL.
4. Add or update fixtures/tests when the parsing behavior changes.

## Current TODOs

- Make logging consistent across dashboard jobs and scraper runs.
- Continue adding portal engines.
- Propagate scraper errors cleanly through retry wrappers and dashboard status.
