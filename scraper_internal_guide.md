# Student Grade Scraper Internal Developer Guide

This project combines Playwright-based portal scraping, a local Windows Rust CRM/Neon write boundary, and a public read-only Flask dashboard.

## Repository Layout

```text
PlaywrightScraper/
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
│   ├── templates/                # generic React shell and sanitized 503 page
│   └── static/                   # read-only React UMD dashboard, styles, and assets
├── batches/                      # Windows batch pipeline wrappers
├── tests/                        # unit and integration tests
└── pyproject.toml
```

Portal engines are self-describing and automatically discovered. Their class
metadata owns the portal key, URL-detection patterns, universal-login selectors,
and optional shared grade-table selectors. Portal-specific behavior belongs in
login validation/post-login hooks or a focused method override. Grade engines
return a normalized `dict[str, float]`; the runner owns the surrounding result
payload and database boundary.

## High-Level Flow

1. `scraper.runner` or `scraper.agenda` starts a leased job through `grade-db.exe`.
2. Rust selects CRM students whose `GradePortalURL`, `GradePortalUser`, and `GradePortalPwd` are all trimmed and nonblank, left-joins optional secondary credentials from `dbo.tblStudentGradePortalSecondary`, then merges the remaining Neon-owned runner configuration.
3. Python uses Playwright to collect a bounded number of students concurrently, controlled by `scraper.runner.MAX_CONCURRENT_GRADE_WORKERS`, while posting completed results serially and immediately.
4. Rust rechecks CRM eligibility and atomically records the audit result and canonical `students_grades_20262027` update in Neon.
5. The dashboard independently selects the runnable CRM roster, batch-reads canonical Neon state, and merges strictly by `crmstudentid`.
6. In dev mode, the overview reads `grade_scrape_jobs` and polls `/api/jobs` every 15 seconds. It cannot start, heartbeat, complete, or fail jobs.

## Agenda Collection Contract

`scraper.agenda` preserves the two CRM credential positions as fixed agenda
slots: Portal 1 is always primary, and Portal 2 is always secondary. It never
sorts or otherwise reassigns slots based on portal type. Each slot is audited
and persisted independently in `primary_agenda` or `secondary_agenda`:

```json
{
  "portal": "canvas",
  "weeks": {
    "2026-08-10": {
      "Example class": {
        "missing": [],
        "due": [{"title": "Example work", "dueDate": "2026-08-14", "dueTime": null}]
      }
    }
  }
}
```

`portal` is a safe lowercase registry key or `null`; it is not a portal URL.
`weeks` is always an object. Week keys are canonical ISO Monday dates calculated from each usable
assignment due date. Class buckets always contain `missing` and `due` arrays.
Rows contain the title, normalized due date, and normalized local time (or
`null`); undated work is omitted because it cannot be placed in a week.

Canvas, ParentVUE, and Google Classroom are agenda-capable. Each collector
returns both missing and upcoming/due work in one invocation; the agenda CLI
does not select a single status. Missing credentials and unsupported or
parserless portals record `configuration_missing` or `unsupported_portal` for
their specific slot. A capable collector that completes with no dated
assignments is a successful blank slot.

Within a slot, normalized rows are grouped by Monday week and class, with
missing and due status buckets. Identical rows from the same portal may be
collapsed by stable source identity (with missing taking precedence over due),
but rows are never deduplicated, merged, or reordered across slots.

The Rust result boundary recursively counts every JSON object, array, and
primitive value and accepts at most 1,000 nodes. Normalization therefore caps
each slot's `weeks` subtree at 497 nodes.

Each audited result targets exactly one channel. Grade status may be `never`,
`synced`, `bad_login`, `no_grades`, or `scrape_failed`. Agenda status may be
`never`, `synced`, `bad_login`, `configuration_missing`, `scrape_failed`, or
`unsupported_portal`. Rust rejects failure codes that do not belong to the
result channel. Job-level failures use the separate process codes
`lease_renewal_failed`, `lease_expired`, `neon_unavailable`,
`result_post_failed`, `runner_failed`, and `agenda_runner_failed`.
The cap is applied separately to both slots, so a large `agenda1` cannot reduce
the capacity available to `agenda2`. Rows are retained from the already
canonical order (week, case-insensitive class, missing before due, then
date/time/title), and no empty class or week scaffolding is emitted when its
first row cannot fit. This is bounded current-state storage: a sufficiently
large portal response is deterministically truncated rather than rejected by
the database boundary.

The runner starts workers only for configured agenda-capable slots and uses
separate browser pages. It posts one `agenda_success` bundle only after every
started worker succeeds. If any such worker fails during login, request,
parsing, or normalization, it posts a controlled failure rather than a partial
bundle; the database boundary leaves the prior stored snapshot in place.

The snapshot must never include credentials, portal URLs, student identifiers,
cookies, tokens, session values, or raw portal responses. Logs and controlled
failure outcomes likewise contain only safe codes and aggregate context.

## Dashboard Architecture

`ui.wsgi` imports the Flask app and registers `ui.routes`. The web application is intentionally public and contains no application authentication, sessions, CSRF state, forms, or write routes.

Routes:

- `GET /` shows all runnable franchises plus active and 20 recent jobs only when `PYTHON_ENV=dev`; otherwise it returns a restricted page with HTTP 200 for deployment health checks without querying either database.
- `GET /health` and `GET /login` redirect to `/` for old bookmarks and inherit its environment gate.
- `GET /franchise/<franchise_id>` shows CRM-runnable students with grade filters.
- `GET /franchise/<franchise_id>/student/<crmstudentid>` shows grade, agenda, history, and heatmap data. Ineligible or missing students return 404.
- `GET /api/jobs` returns a fixed job shape only in dev mode; otherwise it returns 403 without querying Neon.

Outside dev mode, exact franchise and student URLs remain read-only and accessible. Their headers omit the overview link so the root page does not provide franchise discovery.

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
- `PYTHON_ENV=dev` enables the web overview and jobs endpoint and controls development notification behavior
- `SLACK_WEBHOOK_URL` and `SLACK_NOTIFY_IN_DEV`
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

For UI-only agenda verification, use the fixture-backed preview:

```bash
uv run python tests/support/student_agenda_preview.py --port 8765
```

It binds only to localhost and serves fictional data. It never imports
dashboard routes, opens a database, or uses live data; do not replace its
fixture with authorized student information.

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
