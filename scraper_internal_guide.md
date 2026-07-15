# Student Grade Scraper Internal Developer Guide

This project combines Playwright-based portal scraping, a local Windows Rust CRM/Neon write boundary, and a public read-only Flask dashboard.

## Repository Layout

```text
PlaywrightScraper/
├── db.py                         # retained legacy helpers; not used by dashboard routes
├── scraper/
│   ├── runner.py                 # grade collection entrypoint
│   ├── agenda.py                 # agenda collection entrypoint
│   ├── portals/                  # portal-specific login and parsing engines
│   └── db_cli.py                 # JSON subprocess adapter for grade-db.exe
├── grade_db/                     # focused Rust CLI and human-run SQL artifacts
├── ui/
│   ├── app.py                    # Flask app and response/error policy
│   ├── dashboard_data.py         # fixed read-only CRM/Neon queries and display models
│   ├── routes.py                 # GET-only dashboard routes
│   ├── ext_jobs.py               # legacy runner callback compatibility; not imported by Flask
│   ├── templates/                # generic React shell and sanitized 503 page
│   └── static/                   # read-only React UMD dashboard, styles, and assets
├── batches/                      # Windows batch pipeline wrappers
├── tests/                        # unit and integration tests
└── pyproject.toml
```

## High-Level Flow

1. `scraper.runner` or `scraper.agenda` starts a leased job through `grade-db.exe`.
2. Rust selects CRM students whose `GradePortalURL`, `GradePortalUser`, and `GradePortalPwd` are all trimmed and nonblank, then merges Neon-owned runner configuration.
3. Python uses Playwright to collect one student at a time and posts each result immediately.
4. Rust rechecks CRM eligibility and atomically records the audit result and canonical `students_grades_20262027` update in Neon.
5. The dashboard independently selects the runnable CRM roster, batch-reads canonical Neon state, and merges strictly by `crmstudentid`.
6. The overview reads `grade_scrape_jobs` and polls `/api/jobs` every 15 seconds. It cannot start, heartbeat, complete, or fail jobs.

## Dashboard Architecture

`ui.wsgi` imports the Flask app and registers `ui.routes`. The web application is intentionally public and contains no application authentication, sessions, CSRF state, forms, or write routes.

Routes:

- `GET /` shows all runnable franchises plus active and 20 recent jobs.
- `GET /health` and `GET /login` redirect to `/` for old bookmarks.
- `GET /franchise/<franchise_id>` shows CRM-runnable students with grade filters.
- `GET /franchise/<franchise_id>/student/<crmstudentid>` shows grade, agenda, history, and heatmap data. Ineligible or missing students return 404.
- `GET /api/jobs` returns a fixed public job shape.

POST requests to dashboard pages return 405; retired logout and status paths return 404. Responses use `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, and `X-Content-Type-Options: nosniff`.

The CRM dashboard query selects only student ID, franchise, name, grade, and primary portal URL. Credential columns occur only in eligibility predicates. Neon queries never select alternate credentials, GPS answers, job leases, runner IDs, result payloads, or event payloads. Every Neon dashboard transaction begins with `SET TRANSACTION READ ONLY`.

## Configuration

The app reads `.env` through `python-dotenv` where loaded by the entrypoint/runtime.

Shared CRM/Neon settings:

- `CRMSrvAddress`, `CRMSrvDb` (or `CRMSrvDbQA`), `CRMSrvUs`, `CRMSrvPs`
- `CRM_TRUST_SERVER_CERTIFICATE`
- `GRADES_NEON_URL`, or `GRADES_NEON_HOST`, `GRADES_NEON_DB`, `GRADES_NEON_USER`, `GRADES_NEON_PASSWORD`, and `GRADES_NEON_PORT`
- Legacy `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, and `PGPORT` remain supported by `db_core.py`.

Runner-only settings:

- `GRADE_DB_CLI_PATH`
- `GRADE_RUNNER_ID`
- `GRADE_JOB_LEASE_SECONDS`
- `PYTHON_ENV`, `SLACK_WEBHOOK_URL`, and `SLACK_NOTIFY_IN_DEV`
- `OPENAI_API_KEY` and `OPENAI_MODEL` for GPT-assisted portal utilities

`SESSION_SECRET`, `INTERNAL_KEY`, `DEV_BYPASS`, login headers, and CRM user-login credentials are not dashboard settings.

## Run

Install dependencies and Playwright browsers:

```bash
uv sync
uv run playwright install
```

Run the local dashboard:

```bash
uv run flask --app ui.wsgi:app run --host 127.0.0.1 --port 8080
```

Run grade or agenda collection:

```bash
uv run python -m scraper.runner --franchise-id 57
uv run python -m scraper.runner --franchise-id 57 --student-id 123
uv run python -m scraper.agenda --franchise-id 57
```

Replit/nginx uses `ui/start.sh`: Gunicorn binds the private upstream at `127.0.0.1:3000`, and nginx exposes port `8080`.

## Testing

```bash
uv run pytest -q
uv run ruff check .
node --check ui/static/react-dashboard.js
uv run pytest -q --run-integration
```

Integration tests require explicit opt-in and may access live services. Agentic implementation and normal unit tests must use fakes and must not execute SQL against live CRM or Neon.

## Troubleshooting

| Symptom | Likely fix |
| --- | --- |
| Dashboard returns 503 | Confirm Replit has the CRM and Neon secrets and can reach both databases. Dependency details are intentionally hidden from HTTP responses. |
| Dashboard shows no students | Confirm CRM has nonblank `GradePortalURL`, `GradePortalUser`, and `GradePortalPwd`; only runnable students are visible. |
| Student history exists but is hidden | The CRM student is currently ineligible or outside the requested franchise. Neon history is retained, not deleted. |
| Jobs do not update | Confirm the runner is writing `grade_scrape_jobs` and that `/api/jobs` returns shaped progress. |
| `grade-db executable is unavailable` | Build the Rust crate for Windows or set `GRADE_DB_CLI_PATH`. |
| Portal iframe or selector never loads | The portal layout likely changed; update the portal engine wait condition or selector. |

## Pending Enhancements

- Provision a dedicated least-privilege Neon reader for the public Replit deployment.
- Continue adding portal engines and fixture coverage.
- Keep error codes sanitized and consistent between runners and dashboard presentation.
