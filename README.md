# Student Grade Checker

PlaywrightScraper collects student grades and agenda data from supported school portals, stores the results in Postgres, and exposes a Flask dashboard for franchise-level monitoring.

## Dashboard

The UI lives in `ui/` and is served by `ui.wsgi:app`.

Routes:

- `/` is the protected entry point. In normal mode it requires `X-Franchise` and `X-Internal-Key` headers, stores the franchise in the session, and redirects to that franchise dashboard.
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

For local dashboard development, enable the dev bypass so `/` opens the health dashboard without the internal header handoff:

```powershell
$env:DEV_BYPASS = "1"
$env:SESSION_SECRET = "dev-session-secret"
uv run flask --app ui.wsgi:app run --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080/`.

For a production-style request, leave `DEV_BYPASS` unset or set it to `0`, set `INTERNAL_KEY`, and include the handoff headers:

```powershell
$env:INTERNAL_KEY = "replace-me"
$env:SESSION_SECRET = "replace-me"
uv run flask --app ui.wsgi:app run --host 127.0.0.1 --port 8080

curl -i `
  -H "X-Franchise: 19" `
  -H "X-Internal-Key: replace-me" `
  http://127.0.0.1:8080/
```

The Replit/nginx entrypoint is `ui/start.sh`. It runs Gunicorn on `127.0.0.1:3000` and proxies public traffic through nginx on port `8080`.

## Configuration

The scraper and dashboard read environment variables from `.env` via `python-dotenv`.

Required for database access:

- `PGHOST`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`
- `PGPORT`

Dashboard/session settings:

- `INTERNAL_KEY` gates production-style dashboard entry.
- `SESSION_SECRET` signs Flask sessions.
- `DEV_BYPASS=1` enables local dashboard access without the internal headers.

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
