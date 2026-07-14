# Student Grade Scraper Internal Developer Guide

## Architecture invariants

CRM owns canonical students/franchises. Neon owns grade state, jobs, results,
replay claims, and encrypted alternate credentials. Only Rust talks to either
database. Flask, worker, scheduler, and operator tools call Rust over HTTPS;
Axum derives application identity from HMAC or scoped API keyrings.

Flask and Axum run on separate Ubuntu 24.04 instances in one `us-west-2` VPC.
Flask has no database credentials or worker key. The API has no Flask session
secret. Its private address is used by EC2 callers through private Route 53;
its EIP and public DNS-only developer hostname exist solely for four recorded,
allowlisted developer `/32` addresses. Nginx client certificates are optional
legacy compatibility, not the application authorization boundary.

The Windows EC2 instance runs production scheduling and Playwright work, not
the API. Allowlisted developers can run the same worker loop locally with a
separate, scoped scheduler identity and a worker key whose authenticated
identity exactly matches the job target. Neither worker path talks directly to
CRM or Neon after cutover.

## Repository layout

```text
api/                    Axum API, crypto, CRM/Neon queries, migrations, backfill
deploy/                 role artifacts, optional PKI, validation and releases
docs/runbooks/          manual AWS/Cloudflare/deployment/rotation procedures
frontend/               pinned local React/Tailwind build inputs and tests
scraper/                worker clients, lease loop, diagnostics, portals
scripts/                boundary guard, Windows pipeline, operator CLI
ui/                     Flask BFF, safe report models, templates and assets
tests/                  Python contracts, security, deployment and boundaries
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
5. Public DTOs omit credentials, leases, worker identities, internal payloads,
   and raw errors. The frontend has no route/key for worker endpoints.

Worker/scheduler:

1. A scoped scheduler key authorizes explicit franchise and target-worker sets.
   Each enqueue supplies `target_worker_id`; no target is inferred from a
   scheduler ID or machine name.
2. A worker key determines worker identity. Claim selects only jobs targeted to
   that identity and returns a lease token; all later worker calls require the
   same authenticated identity and active lease.
3. The API rechecks canonical CRM eligibility before returning context and
   again before applying result state in production Neon.
4. One result UUID is bound to one student and canonical payload. Identical
   retries are idempotent; changed payloads or students return 409.
5. Empty agenda is a successful result. Bad login is distinct from portal or
   network failure. Ambiguous result delivery abandons the lease.
6. Portal output is discarded. Raw/mapped credentials are cleared in an outer
   `finally`. Sensitive Playwright tracing is always off in production and is
   non-production opt-in through `WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS`.

Offline targets:

Queued work never falls through to another worker. An authorized operator may
retarget or cancel only a `queued` job, must submit a trimmed 1–256 character
incident reason, and receives an atomic event audit. Running jobs reject both
actions. Direct SQL is break-glass only.

Alternate credentials:

1. Operator `PUT` requires a complete HTTPS URL/username/password set, re-reads
   CRM eligibility, encrypts username+password with AES-256-GCM, and writes only
   the envelope. The URL remains non-secret.
2. A worker decrypts the envelope only in memory. Plaintext fallback is an
   explicit temporary flag and encrypted data always wins.
3. Operator `DELETE` clears URL, legacy plaintext, envelope, and agenda tracking.
4. `backfill_alternate_credentials` is dry-run by default. Agents never run
   `--apply` against user data.

## Local verification and worker proof

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

Run a dashboard only with a separate API and development secrets. Browsers call
Flask; never add browser-to-Axum CORS or API keys.

The intended allowlisted local worker flow is:

```powershell
$env:DEPLOYMENT_ENV = "production"
$env:GRADE_API_BASE_URL = "https://grades-api-dev.tutoringclub.com"
$env:SCHEDULER_API_KEY = "<raw scoped scheduler key>"
$env:WORKER_API_KEY = "<raw local worker key>"
$env:WORKER_ID = "dev-alice-laptop"
uv run python scripts\windows_pipeline.py --franchise-id 11 --enqueue --target-worker dev-alice-laptop
uv run python -m scraper.runner --once
```

The scheduler key must authorize franchise 11 and target
`dev-alice-laptop`; the worker key must authenticate as that exact identity.
Use only authorized fetched academic data and follow runbook 07 for production
verification, cleanup, and key revocation.

## Configuration

Do not combine production `.env` files. Use the role examples under `deploy/`
and `validate-role-env`.

- Flask: `SESSION_SECRET`, optional `SESSION_SECRET_PREVIOUS`, and
  `DASHBOARD_HMAC_SIGNING_SECRET`.
- API: `DASHBOARD_HMAC_ACTIVE_SECRET`, optional previous secret,
  `WORKER_API_KEYRING_JSON`, `SCHEDULER_API_KEYRING_JSON`,
  `OPERATOR_API_KEYRING_JSON`, `READINESS_API_KEYRING_JSON`,
  `ALTERNATE_CREDENTIAL_ACTIVE_KEY_ID`, and
  `ALTERNATE_CREDENTIAL_KEYS_JSON`.
- Temporary API overlap only: `ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS`.
- Windows/local: matching raw keys, `WORKER_ID`, explicit
  `WINDOWS_TARGET_WORKER_ID` or `--target-worker`, and approved franchises.

Production HTTPS uses the operating-system trust store by default. Custom CA
and complete client-certificate/key environment pairs are optional transition
compatibility only; a half-configured pair fails closed.

## Database changes

Migrations `001`–`007` are applied manually in order. Migration 006 fails if
plaintext exists without a complete encrypted envelope, then nulls the legacy
columns. Migration 007 refuses to add target enforcement while active jobs
exist. CI uses disposable PostgreSQL and never live CRM, Neon, or AWS secrets.

## Portal development

1. Update/register the portal under `scraper/portals/`.
2. Add fixture-backed parsing and authentication-classification tests.
3. Never print names, usernames, identity-bearing URLs, grade/agenda payloads,
   raw HTML, screenshots, cookies, credentials, or exception details.
4. Return `LoginError` only for authentication failures. Let portal/network
   failures propagate for generic worker classification.
5. Run focused tests, full Python tests, Ruff, and the boundary guard.

## Operations

Deployment, AWS controls, optional PKI, Cloudflare, key rotation, rollback,
and incident steps live under `docs/runbooks/`. Roll back frontend/API artifacts
independently. API rollback requires quiescing enqueue and claim traffic,
draining/cancelling active jobs, and re-running positive and negative network/
authentication matrices. Never perform ad hoc destructive production SQL.
