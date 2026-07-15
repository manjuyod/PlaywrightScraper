# Student Grade Checker

PlaywrightScraper collects student grades and agenda data from supported school portals. CRM owns student identity, franchise, grade level, and primary portal credentials. The local Windows `grade-db.exe` boundary reads CRM, applies job leases and idempotency, and writes the canonical `students_grades_20262027` state in Neon. Python contains the Playwright collection logic and no SQL.

The Flask dashboard is a public, read-only operations view. It reads the runnable student boundary from CRM, reads canonical grade/agenda state from Neon, and merges only on `crmstudentid`. It never reads the legacy Neon `student` table.

## Dashboard

The UI lives in `ui/` and is served by `ui.wsgi:app`.

Routes:

- `/` shows runnable-student summaries for every CRM franchise plus active and recent canonical jobs.
- `/health` and `/login` are compatibility redirects to `/`.
- `/franchise/<franchise_id>` shows runnable CRM students, grade-level filters, current grade snapshots, standing, status, and CRM primary-portal links.
- `/franchise/<franchise_id>/student/<crmstudentid>` shows current grades, agenda items, grade history, and heatmap views.
- `/api/jobs` returns shaped, read-only job progress and is polled by the overview every 15 seconds.

The web surface has no login, session, forms, scraper launch controls, or database mutations. Anyone who can reach the deployment URL can view student names and grades. `ui/ext_jobs.py` remains only for legacy runner callback compatibility and is not imported by Flask.

## Local Dashboard Run

Install dependencies:

```powershell
uv sync
```

Start Flask:

```powershell
uv run flask --app ui.wsgi:app run --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080/`. The dashboard immediately performs read-only CRM and Neon queries; no sign-in is required.

The Replit/nginx entrypoint is `ui/start.sh`. It runs Gunicorn on `127.0.0.1:3000` and proxies public traffic through nginx on port `8080`.

## Configuration

The scraper and dashboard read environment variables from `.env` via `python-dotenv`.

Required by `grade-db.exe`:

- `GRADES_NEON_URL`, or these component values:
- `GRADES_NEON_HOST`
- `GRADES_NEON_DB`
- `GRADES_NEON_USER`
- `GRADES_NEON_PASSWORD`
- `GRADES_NEON_PORT`

- `GRADE_DB_CLI_PATH` optionally selects an exact `grade-db.exe` build.
- `GRADE_RUNNER_ID` optionally supplies a stable runner identity; the machine hostname is the default.
- `GRADE_JOB_LEASE_SECONDS` optionally overrides the 600-second lease (120–86400 seconds).

Dashboard read settings:

- `CRMSrvAddress`, `CRMSrvDb`, `CRMSrvDbQA`, `CRMSrvUs`, `CRMSrvPs` provide CRM read connectivity. `CRMSrvDb` is preferred when set, with `CRMSrvDbQA` as fallback.
- `CRM_TRUST_SERVER_CERTIFICATE` controls SQL Server certificate trust (default `no`). `1`, `true`, and `yes` (case-insensitive) are accepted values for enabling `TrustServerCertificate=yes`.
- The CRM SQL Server connection uses encrypted ODBC transport and `ApplicationIntent=ReadOnly`. Its fixed query checks portal credential eligibility in SQL but selects only ID, franchise, name, grade, and the primary portal URL.
- Neon dashboard reads use the existing `GRADES_NEON_*`/`GRADES_NEON_URL` configuration and begin every transaction with `SET TRANSACTION READ ONLY`.

The bundled nginx config forwards `X-Forwarded-For` and `X-Forwarded-Proto`; Flask applies trusted proxy handling for Replit deployment URLs.

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

## Rust Database Boundary

Build the Windows MSVC release executable:

```powershell
cargo build --manifest-path grade_db/Cargo.toml --target x86_64-pc-windows-msvc --release
$env:GRADE_DB_CLI_PATH = (Resolve-Path .\grade_db\target\x86_64-pc-windows-msvc\release\grade-db.exe)
```

The Python adapter falls back to the documented target-specific and default `release`/`debug` build locations. Compiled executables and Cargo targets are ignored by Git.

Database rollout is intentionally human-operated:

1. Run and review [`grade_db/sql/000_inspect_boundary.sql`](grade_db/sql/000_inspect_boundary.sql). It selects only schema metadata and row counts.
2. After review, apply [`grade_db/sql/001_runner_boundary.sql`](grade_db/sql/001_runner_boundary.sql). It is forward-only, performs no legacy student backfill, and does not drop tables or data.
3. Use the templates in `grade_db/sql/operations/` to set or clear portal2, agenda, and GPS fields on existing CRM-created rows.
4. Run `grade-db.exe doctor`, then pilot one student, one franchise, and agenda collection before enabling scheduled batches.

`grade-db.exe` exposes only `job start`, `job heartbeat`, `result post`, `job complete`, `job fail`, and read-only `doctor`. It has no listener, arbitrary SQL command, scheduler, or migration command.

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
- Consider a dedicated least-privilege Neon role for the public dashboard deployment.
