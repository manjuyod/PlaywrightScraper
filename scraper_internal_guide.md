# Student Grade Scraper Internal Developer Guide

This project combines Playwright-based portal scraping, Postgres persistence, and a Flask dashboard for franchise operators.

## Repository Layout

```text
PlaywrightScraper/
├── db.py                         # Postgres access, Student model, encryption helpers
├── scraper/
│   ├── runner.py                 # grade collection entrypoint
│   ├── agenda.py                 # agenda collection entrypoint
│   ├── portals/                  # portal-specific login and parsing engines
│   └── work_flows/               # sheet sync, grade insertion, verification workflows
├── ui/
│   ├── app.py                    # Flask app, sessions, auth helpers
│   ├── routes.py                 # dashboard routes
│   ├── controllers.py            # report/status computation helpers
│   ├── ext_jobs.py               # background job execution and progress state
│   ├── templates/                # health, franchise, student, heatmap views
│   └── static/                   # dashboard styles and assets
├── batches/                      # Windows batch pipeline wrappers
├── tests/                        # unit and integration tests
└── pyproject.toml
```

## High-Level Flow

1. `scraper.runner` loads active students from Postgres.
2. Portal URLs are mapped to registered portal engines.
3. Each portal engine logs in, handles portal-specific page structure, and extracts grades.
4. Grade results are inserted by `scraper.work_flows.insert_grades`.
5. The dashboard reads students from Postgres, computes report fields, and shows franchise/student views.
6. Dashboard refresh buttons start background jobs in `ui.ext_jobs`; templates poll `/status/<job_id>` until completion.

Agenda collection follows the same dashboard job pattern through `scraper.agenda`.

## Dashboard Architecture

`ui.wsgi` imports the Flask app and registers `ui.routes`.

Key routes:

- `/` is the protected handoff route. Normal access requires `X-Franchise` and `X-Internal-Key`; dev access can use `DEV_BYPASS=1`.
- `/health` shows active franchises, scraper health, grouped errors, bad logins, malformed inputs, nonconfigured portals, and active jobs.
- `/franchise/<franchise_id>` shows the franchise student list, grade snapshots, low/high grades, standing, status, search/sort, edit/delete actions, and franchise-wide grade/agenda refresh buttons.
- `/franchise/<franchise_id>/student/<student_id>` shows one student's current snapshot, agenda, grade history table, and heatmap tab.
- `/status/<job_id>` returns progress JSON for grade and agenda progress bars.

Job IDs are derived from dashboard scope:

- Franchise grades: `<franchise_id>`
- Franchise agenda: `<franchise_id>_agenda`
- Student grades: `<franchise_id>_<student_id>`
- Student agenda: `<franchise_id>_<student_id>_agenda`

## Configuration

The app uses `.env` through `python-dotenv`.

Database:

- `PGHOST`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`
- `PGPORT`

Dashboard:

- `INTERNAL_KEY` gates production-style dashboard entry.
- `SESSION_SECRET` signs Flask sessions.
- `DEV_BYPASS=1` opens local dashboard access without the internal handoff headers.

Notifications and utilities:

- `PYTHON_ENV`
- `SLACK_WEBHOOK_URL`
- `SLACK_NOTIFY_IN_DEV`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

## Run

Install dependencies:

```bash
uv sync
uv run playwright install
```

Run the local dashboard:

```bash
DEV_BYPASS=1 SESSION_SECRET=dev-session-secret \
uv run flask --app ui.wsgi:app run --host 127.0.0.1 --port 8080
```

Run grade collection:

```bash
uv run python -m scraper.runner
uv run python -m scraper.runner --franchise-id 57
uv run python -m scraper.runner --franchise-id 57 --student-id 123
```

Run agenda collection:

```bash
uv run python -m scraper.agenda --franchise-id 57
```

## Add a Portal

1. Create or update `scraper/portals/<name>.py`.
2. Register the portal key in `scraper/portals/__init__.py`.
3. Confirm `scraper/portals/utils.py` maps the stored portal URL to that key.
4. Add fixture-backed tests when parsing logic changes.
5. Run focused tests and at least one integration path for the affected franchise when credentials are available.

## Troubleshooting

| Symptom | Likely fix |
| --- | --- |
| `ModuleNotFoundError: utils` | Run commands from the repository root so top-level packages resolve. |
| Dashboard returns 403 | In normal mode, include `X-Franchise` and `X-Internal-Key`; for local development, set `DEV_BYPASS=1`. |
| Dashboard has no students | Check Postgres env vars and confirm the `student` table has active rows for the franchise. |
| Progress bar disappears | `/status/<job_id>` returned 404, usually because the job finished, was never started, or the process restarted. |
| CAPTCHA screenshot appears | Inspect `captcha.png`; re-run the student or update the portal flow if the site changed. |
| Portal iframe or selector never loads | The portal layout likely changed; update the portal engine wait condition or selector. |

## Tests

```bash
uv run pytest -q
uv run pytest -q --run-integration
TEST_FRANCHISE_ID=19 uv run pytest -q --run-integration
```

## Pending Enhancements

- More consistent logging across CLI runs, dashboard jobs, and portal engines.
- Cleaner error propagation from retries into dashboard-visible status fields.
- Additional portal coverage and fixture tests.
- More robust dashboard job persistence if the web process restarts during a refresh.
