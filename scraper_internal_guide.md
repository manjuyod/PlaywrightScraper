# Student Grade Scraper Internal Developer Guide

## Architecture invariants

CRM owns canonical students/franchises. Neon owns grade state, jobs, results,
replay claims, and encrypted alternate credentials. Only Rust talks to either
database. Flask, worker, scheduler, and operator tools call Rust over private
TLS; production clients also present role-specific mTLS certificates.

Flask and Axum run on separate Ubuntu instances. Flask has no database
credentials. The API has no Flask session/signing secrets and no public route.
The Windows EC2 instance runs scheduling and Playwright work, not the API.

## Repository layout

```text
api/                    Axum API, crypto, CRM/Neon queries, migrations, backfill
deploy/                 role artifacts, PKI tools, validation and release scripts
docs/runbooks/          manual AWS/Cloudflare/PKI/deployment/rotation procedures
frontend/               pinned local React/Tailwind build inputs and tests
scraper/                worker clients, lease loop, agenda collection, portals
scripts/                verification, boundary guard, Windows pipeline/operator CLI
ui/                     Flask BFF, safe report models, templates and built assets
tests/                  Python contracts, security, deployment and boundary tests
```

The deleted `db.py`, `db_core.py`, `ui.ext_jobs`, SQLAlchemy/ODBC helpers,
Sheets workflows, JSONL loaders, and student-management controls must not be
reintroduced. `scripts/check_python_boundaries.py` enforces this in CI.

## Request flows

Dashboard:

1. Flask sends credentials only to Rust `/api/auth/login`; Rust calls CRM.
2. Flask stores a signed secure cookie containing session type, role/franchise,
   HMAC username fingerprint, CSRF token, and active job UUIDs—never students.
3. Each BFF request is timestamp/user/franchise/role/nonce/body-bound HMAC.
4. Rust validates active/previous keys and atomically claims the nonce in
   PostgreSQL. Database failure returns 503; replay returns unauthorized.
5. Public student/job/health DTOs omit credentials, leases, worker identities,
   internal payloads, and raw errors.

Worker/scheduler:

1. The scheduler reconciles CRM eligibility, enqueues daily deterministic jobs,
   and then drains work.
2. A worker bearer token determines its identity. Claim returns a lease token;
   every later worker mutation requires the active matching lease.
3. The API rechecks canonical eligibility for context and result persistence.
4. Empty agenda is a successful synchronized result. Bad login is distinct
   from portal/network failure. Ambiguous result delivery abandons the lease.
5. Portal stdout/stderr is discarded at the worker boundary; result bodies,
   HTML, credentials, exception details, and tracebacks are not logged.

Alternate credentials:

1. Operator `PUT` requires a complete HTTPS URL/username/password set, re-reads
   CRM eligibility, AES-256-GCM encrypts username+password, and writes only the
   envelope. The URL remains non-secret.
2. A worker decrypts the envelope in memory. Plaintext fallback is an explicit,
   temporary flag and encrypted data always takes precedence.
3. Operator `DELETE` clears URL, legacy plaintext, envelope, and agenda tracking.
4. `backfill_alternate_credentials` is dry-run by default. `--apply` is the only
   write switch; `--resume-after`, `--limit`, `--verify`, and `--rotate-key`
   support staged manual execution. Agents never run apply against user data.

## Local verification

```powershell
uv sync --frozen
uv run ruff check .
uv run pytest -q
npm ci
npm run build
npm test

cd api
cargo fmt --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-targets
```

Run a local dashboard only with a separately running API and development
secrets. Browsers call Flask; never add browser-to-Axum CORS or tokens.

Run the Windows pipeline from the repository root:

```powershell
uv run python scripts\windows_pipeline.py
uv run python -m scraper.runner --once
```

`python -m scraper.agenda` is a compatibility entrypoint that drains API jobs;
it no longer loads students directly.

## Configuration

Do not improvise combined `.env` files for production. Use the role examples in
`deploy/` and `validate-role-env`. Important rotation-aware values include:

- Flask: `SESSION_SECRET`, optional `SESSION_SECRET_PREVIOUS`, and
  `DASHBOARD_HMAC_SIGNING_SECRET`.
- API: `DASHBOARD_HMAC_ACTIVE_SECRET`, optional previous secret,
  `WORKER_API_TOKENS_JSON`, `SCHEDULER_API_TOKENS_JSON`,
  `OPERATOR_API_TOKENS_JSON`, `ALTERNATE_CREDENTIAL_ACTIVE_KEY_ID`, and
  `ALTERNATE_CREDENTIAL_KEYS_JSON`.
- Temporary API overlap only: `ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS`.
- Windows: matching API keys, distinct client cert/key profiles, `WORKER_ID`,
  `SCHEDULER_ID`, and scheduled franchise/kind values.

## Database changes

Migrations `001`–`006` must be applied manually in order. CI exercises a fresh
schema and an `001`–`003` to `004`–`006` upgrade only against disposable
PostgreSQL. CI and developer tests never connect to CRM or production Neon.

Migration 006 deliberately fails when plaintext exists without a complete
encrypted envelope, then nulls the legacy credential columns and installs a
constrained-null check. Do not bypass that precondition.

## Portal development

1. Update/register the portal under `scraper/portals/`.
2. Add fixture-backed parsing/auth classification tests.
3. Never print names, usernames, URLs containing identity, grade/agenda payloads,
   raw HTML, screenshots, cookies, or exception details.
4. Return `LoginError` only for authentication failures. Let portal/network
   failures propagate for generic classification by the worker.
5. Run focused tests, the full Python suite, Ruff, and the boundary guard.

## Operations

Deployment, PKI, Cloudflare, AWS controls, HMAC/clock rotation, rollback, and
incident steps live under `docs/runbooks/`. Roll back frontend/API artifacts
independently, stop Windows tasks before API rollback, preserve additive schema,
and never perform destructive incident-time database rollback.
