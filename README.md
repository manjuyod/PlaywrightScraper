# Student Grade Checker

## Grade Checker authentication flow

Grade Checker uses CRM-owned browser-device authorization and keeps all device
keys and proof operations on the CRM origin:

- `/auth/start` creates a short-lived PKCE transaction and redirects to the
  fixed CRM device authorization page.
- `/auth/callback` redeems the returned authorization code through the local
  Rust authentication service, introspects its grant, and creates an HTTP-only
  Grade session cookie. Center admins land on `/`; tutors land on their
  server-validated `/franchise/<franchise_id>` route.
- Every protected request validates the Grade session and introspects the live
  Rust grant before loading dashboard data.
- `/` and `/api/jobs` require `dashboard.read`; franchise and student routes
  require `students.read` and the exact franchise in the validated session.

PlaywrightScraper collects student grades and agenda data from supported school portals. CRM owns student identity, franchise, grade level, activity status, and primary and secondary portal credentials. The local Windows `grade-db.exe` boundary reads runnable CRM students whose `IsTrail` value is `Active` and whose primary portal credentials are complete, applies job leases and idempotency, and writes the canonical `students_grades_20262027` state in Neon. Python contains the Playwright collection logic and no SQL.

The Flask dashboard is an authenticated, read-only operations view. It uses the same runnable-student definition (`IsTrail = 'Active'` plus complete primary portal credentials), reads canonical grade/agenda state from Neon, and merges only on `crmstudentid`.

## Dashboard

The UI lives in `ui/` and is served by `ui.wsgi:app`.

Routes:

- `/` shows runnable-student summaries and canonical jobs for the franchise in
  a validated session with `dashboard.read`.
- `/health` and `/login` redirect to `/` and therefore reach the same
  authentication guard.
- `/franchise/<franchise_id>` shows runnable CRM students, grade-level filters, current grade snapshots, standing, status, and CRM primary-portal links.
- `/franchise/<franchise_id>/student/<crmstudentid>` shows current grades, agenda items, grade history, and heatmap views.
- `/api/jobs` returns shaped, read-only job progress for the validated session
  franchise and is polled by the overview every 15 seconds.

Franchise and student pages never provide navigation to the overview. A direct
URL still requires `students.read`, and its franchise ID must exactly match the
validated session before any private loader executes.

## Local Dashboard Run

Install dependencies:

```powershell
uv sync
```

Start Flask:

```powershell
uv run flask --app ui.wsgi:app run --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080/`. Grade Checker redirects an unauthenticated
browser to the CRM-owned device authorization flow before any dashboard query.

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

- `GRADE_CHECKER_ENV=production`
- `PGHOST`
- `PGDATABASE`
- `PGUSER`
- `PGPASSWORD`
- `PGPORT`
- `CRMSrvAddress`
- `CRMSrvDb` or `CRMSrvDbQA`
- `CRMSrvUs`
- `CRMSrvPs`
- `CRM_AUTH_BASE_URL=https://crm-auth.tutoringclub.com`
- `CRM_AUTH_CLIENT_ID=grade-checker`
- `CRM_AUTH_CLIENT_SECRET=<protected high-entropy value>`
- `CRM_AUTH_ISSUER=https://crm-auth.tutoringclub.com`
- `CRM_AUTH_AUDIENCE=grade-checker`
- `CRM_AUTH_JWKS_URL=https://crm-auth.tutoringclub.com/.well-known/jwks.json`
- `CRM_DEVICE_AUTHORIZE_URL=https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx`
- `GRADE_CHECKER_CALLBACK_URL=https://grades.tutoringclub.com/auth/callback`
- `GRADE_CHECKER_COOKIE_SECRET=<protected high-entropy value>`
- `AUTH_TRANSACTION_TTL_SECONDS=600`

### QA Replit deployment

Publish QA as a separate Autoscale deployment with the custom domain
`qa-grades.tutoringclub.com`. Generated `replit.dev` and `replit.app` URLs are
not valid callback or cookie origins for this integration. Configure the QA
published app with independent CRM/Neon credentials and these auth Secrets:

- `GRADE_CHECKER_ENV=qa`
- `CRM_AUTH_BASE_URL=https://qa-crm-auth.tutoringclub.com`
- `CRM_AUTH_CLIENT_ID=grade-checker`
- `CRM_AUTH_CLIENT_SECRET=<QA-only protected high-entropy value>`
- `CRM_AUTH_ISSUER=https://qa-crm-auth.tutoringclub.com`
- `CRM_AUTH_AUDIENCE=grade-checker`
- `CRM_AUTH_JWKS_URL=https://qa-crm-auth.tutoringclub.com/.well-known/jwks.json`
- `CRM_DEVICE_AUTHORIZE_URL=https://qa.tutoraid.net/GradeCheckerDeviceAuthorize.aspx`
- `GRADE_CHECKER_CALLBACK_URL=https://qa-grades.tutoringclub.com/auth/callback`
- `GRADE_CHECKER_COOKIE_SECRET=<QA-only protected high-entropy value>`
- `AUTH_TRANSACTION_TTL_SECONDS=600`

`GRADE_CHECKER_ENV` accepts only `production` and `qa`; missing or empty keeps
the production profile. A profile/URL mismatch fails configuration validation,
so QA cannot fall back to a production authorization origin.

Optional:

- `CRM_TRUST_SERVER_CERTIFICATE=1` allows trusting the CRM SQL Server
  certificate when required by that environment.

The Microsoft ODBC Driver 17 bundle lives in `odbc_driver/`, with its required
resource file in `share/resources/en_US/`. `setup_odbc.sh` registers the driver
as `ODBC Driver 17 for SQL Server` in `$HOME/.odbc/odbcinst.ini`, matching the
dashboard CRM login connection string.

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
- The CRM SQL Server connection uses encrypted ODBC transport and `ApplicationIntent=ReadOnly`. Its fixed query requires `IsTrail = 'Active'` and complete primary portal credentials but selects only ID, franchise, name, grade, and the primary portal URL.
- Neon dashboard reads use the existing `GRADES_NEON_*`/`GRADES_NEON_URL` configuration and begin every transaction with `SET TRANSACTION READ ONLY`.

Grade authentication uses the fixed server-to-server endpoints
`POST https://crm-auth.tutoringclub.com/v2/authorization/redeem` and
`POST https://crm-auth.tutoringclub.com/v2/grants/introspect`. Grade Checker
authenticates those requests with `CRM_AUTH_CLIENT_ID` and
`CRM_AUTH_CLIENT_SECRET`; no secret value belongs in this repository, browser
JavaScript, templates, or logs.

The bundled nginx config forwards `X-Forwarded-For` and `X-Forwarded-Proto`; Flask applies trusted proxy handling for Replit deployment URLs.

Optional:

- `PYTHON_ENV=dev` affects runner notification behavior. It does not bypass the
  Grade session or live-grant checks on private routes.
- `SLACK_WEBHOOK_URL` enables Slack notifications.
- `SLACK_NOTIFY_IN_DEV=1` allows Slack notifications in dev.
- `LOG_LEVEL` sets the runner and portal log level (default `INFO`).
- `LOG_FORMAT=text` controls readable terminal output (the default); set `LOG_FORMAT=json` when a console log collector expects JSON.
- `LOG_DIRECTORY` sets the rotating JSONL directory (default: `logs/`). `LOG_FILE` can override the complete file path.
- `LOG_MAX_BYTES` controls rotation size (default: 10 MiB), and `LOG_BACKUP_COUNT` controls retained rotated files (default: 10).
- `LOG_FILE_ENABLED=0` disables persistent JSONL logging when an external collector already provides durable storage.
- `LOG_INCLUDE_TRACEBACKS=1` includes tracebacks on fatal runner events. It is off by default so exception messages cannot accidentally expose portal data.

Portal logs use stable event names and include the portal key and CRM student record ID as context. They report counts and controlled outcome codes, not credentials, student names, grade values, authentication answers, page HTML, or full URLs. A terminal runner failure sends a critical Slack notification containing only its controlled failure code and exception type.

## Scraper Runs

Run grade collection from the CLI:

```powershell
uv run python -m scraper.runner
uv run python -m scraper.runner --franchise-id 19
uv run python -m scraper.runner --franchise-id 19 --student-id 123
```

Grade concurrency is controlled by the single
`MAX_CONCURRENT_GRADE_WORKERS` constant in `scraper/runner.py` (currently
`1`). It does not affect agenda concurrency.

Run agenda collection:

```powershell
uv run python -m scraper.agenda --franchise-id 19
```

### Agenda workflow

An agenda run always collects both missing and upcoming/due work for every
supported, configured credential slot. It does not accept a per-status target:
there is one complete collection for the selected students.

Agenda storage uses independent `primary_agenda` and `secondary_agenda` JSONB
columns. Each column stores one fixed credential slot:

```json
{"portal": "canvas", "weeks": {}}
```

Portal 1 always supplies `primary_agenda`; Portal 2 always supplies
`secondary_agenda`. Slot identity is never reordered by portal type. Each populated `weeks` object is
grouped by the ISO date of the Monday containing an assignment's due date, then
by the portal-provided class name, with `missing` and `due` arrays in each
class bucket. A row visible in both slots remains in both slots; there is no
cross-slot deduplication or reordering.

Storage is intentionally bounded by the unchanged Rust result validator. Each
slot's normalized `weeks` subtree is capped independently at 497 recursively
counted JSON values, so the complete two-slot bundle is at most 999 values
against the 1,000-value boundary. When a slot exceeds that capacity, it keeps
the deterministic canonical prefix: weeks in ascending order, classes in
case-insensitive order, missing rows before due rows, and rows ordered by due
date, time, and title. A week or class is included only when at least one of
its rows fits. Capacity is never borrowed across slots, and bounding does not
deduplicate or reorder work between `agenda1` and `agenda2`.

Canvas, ParentVUE, Google Classroom, and Infinite Campus are the currently
supported agenda collectors, and each returns missing plus upcoming/due work in
the same run. Infinite Campus opens each current course once from Grades and
bulk-parses its rendered assignment rows without opening assignment details.
An entirely unconfigured slot is cleared to the canonical blank snapshot and
records `not_configured`, so removed credentials cannot leave stale assignments
visible. Partial credentials record `configuration_missing`; an unsupported or
parserless configured portal produces a neutral empty slot. A capable collector
that successfully finds no dated work is a successful blank result. Each slot
posts as soon as it finishes. A successful slot remains committed if the other
slot later fails or is interrupted; the failed slot keeps its previous snapshot
and records its own failure status. Slot failures count as student errors but do
not fail the agenda process itself.

For a safe, fictional UI preview that does not need CRM, Neon, credentials, or
live portal data, run:

```powershell
uv run python tests/support/student_agenda_preview.py --port 8765
```

The preview serves localhost only. It neither imports dashboard routes nor
opens a database connection, and its data is synthetic.

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
2. After review, apply [`grade_db/sql/001_runner_boundary.sql`](grade_db/sql/001_runner_boundary.sql). It is forward-only and does not drop tables or data.
3. Require `crm_secondary_schema=true`, then apply [`grade_db/sql/002_drop_neon_secondary_portal.sql`](grade_db/sql/002_drop_neon_secondary_portal.sql).
4. Pause scheduled grade and agenda runners, wait for every running job to finish, and keep old runners stopped through the code deployment.
5. Apply the non-destructive [`grade_db/sql/003_split_student_scrape_state.sql`](grade_db/sql/003_split_student_scrape_state.sql) to add and backfill channel-specific data, status, and timestamp columns.
6. Deploy the matching Python, dashboard, and `grade-db.exe` build together, then require `grade-db.exe doctor` to pass before resuming runners.
7. Pilot one student, one franchise, and an agenda job. Verify channel statuses and result audit rows.
8. Apply [`grade_db/sql/004_drop_shared_scrape_state.sql`](grade_db/sql/004_drop_shared_scrape_state.sql) only after the new readers and writers are verified.
9. Use the templates in `grade_db/sql/operations/` to set or clear the Neon-owned portal override, agenda tracking, and GPS fields. The separate CRM frontend owns rows in `dbo.tblStudentGradePortalSecondary`.

`grade-db.exe` exposes only `job start`, `job heartbeat`, `result post`, `job complete`, `job fail`, and read-only `doctor`. It has no listener, arbitrary SQL command, scheduler, or migration command.

## Tests

```powershell
uv run pytest -q
uv run pytest -q --run-integration
$env:TEST_FRANCHISE_ID = "19"; uv run pytest -q --run-integration
```

## Portal Development

Portal engines live in `scraper/portals/`.

### To add a portal:

Portal modules are discovered automatically. A typical portal declares everything
needed for registration and the shared login flow beside its engine:

```python
from scraper.portals import (
    GradeTableConfig,
    PortalEngine,
    UniversalLoginConfig,
)


class ExamplePortal(PortalEngine):
    portal_key = "example"
    url_patterns = ("grades.example.org", "/example/login")
    login_config = UniversalLoginConfig(
        username_selector="#username",
        password_selector="#password",
        microsoft_sso=True,
    )
    grade_table_config = GradeTableConfig(
        table_selector=".course",
        title_selector=".course-title",
        grade_selector=".current-grade",
    )
```

Adding this module under `scraper/portals/` is sufficient; do not edit a central
portal list. URL detection is case-insensitive and selects the longest matching
declared pattern.

Override `validate_login()` to recognize portal-specific rejection states and
`after_login(first_name)` for student selection, secondary authentication, or
post-login navigation. Set `alternate_sso=True` and override
`alternate_sso_login()` for a nonstandard SSO fallback. Override `login()` only
when the universal flow cannot represent the portal.

`fetch_grades()` always returns a `dict[str, float]` mapping normalized course
names to percentages. Use `GradeTableConfig` when the shared table parser fits;
override `fetch_grades()` for custom layouts. The runner adds the outer
`parsed_grades` result field.

Each registered portal receives `self.logger`. Log stable events, counts, and
controlled states only---never credentials, student data, grade values, page HTML,
or full URLs. Add fixture-backed tests whenever login declarations or parsing
behavior changes.

### To update/fix a portal:

Make targeted changes.

Test that the changes worked by running the portal test:

```bash
uv run python -m scraper.workflows.test_portal --portal {portal_key}
```

Add the `--grades` flag to test grade parsing or `--agenda` to test the complete
missing-and-upcoming agenda flow as well. The flags can be combined.

### Live portal diagnostics

The explicit live diagnostic does not access CRM or Neon and does not persist grades
or login outcomes. It reads only an operator-provided, Git-ignored account manifest;
dedicated test accounts are strongly preferred. By default, success means `login()`
completed and grade fetching is not attempted. Without `--portal`, it tests one
random configured account for every registered portal. With `--portal`, it
concurrently tests up to five configured accounts for that portal (or every
available account when fewer exist). Pass `--grades` to continue through grade
fetching after a successful login. Pass `--agenda` to fetch missing and upcoming
work through both configured, supported agenda portals; it can be combined with
`--grades`.

Create `config/students.portal-test.json` with owner-only permissions. This path is
already covered by `.gitignore`'s `config/students.*` rule:

```json
[
  {
    "test_id": 1001,
    "portal": "powerschool",
    "login_url": "https://school.example/login",
    "id": "dedicated-test-user",
    "password": "dedicated-test-password"
  }
]
```

```bash
chmod 600 config/students.portal-test.json
export PORTAL_TEST_ACCOUNTS_FILE=config/students.portal-test.json
```

`test_id` is only a non-secret correlation value for diagnostic logs; it does not
need to be a CRM student ID. On Windows, restrict the file's NTFS ACL to the account
that runs the diagnostic.

```powershell
uv run python -m scraper.workflows.test_portal
uv run python -m scraper.workflows.test_portal --portal powerschool
uv run python -m scraper.workflows.test_portal --portal powerschool --seed 19 --headless
uv run python -m scraper.workflows.test_portal --portal powerschool --grades
```

For an interactive failure investigation, run one account in a headed browser:

```bash
uv run python -m scraper.workflows.test_portal --portal powerschool --sample-size 1 --debug
```

`--debug` opens Chromium DevTools from startup. On failure, it also opens Playwright
Inspector and pauses before the page and browser context are closed. Inspect the DOM,
console, network activity, action log, locators, and current page, then press Resume
to finish cleanup. It also prints the original exception traceback to the terminal.
This output is intentionally not added to the retained structured log fields because
exception messages can contain portal URLs, page text, selectors, or other private
data. Treat the terminal as sensitive while debugging.

Runner events are retained as JSON lines in `logs/scraper.jsonl`; explicit portal
diagnostics use `logs/portal-tests.jsonl`. Both rotate automatically. Terminal
output remains human-readable unless `LOG_FORMAT=json` is explicitly selected.
For example, query failed PowerSchool diagnostic events with:

```bash
jq 'select(.portal == "powerschool" and .level == "ERROR")' logs/portal-tests.jsonl
```
