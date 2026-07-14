# Student Grade Checker

PlaywrightScraper is a security-separated grade and agenda collection system.
CRM SQL Server is authoritative for students, Neon stores scraper state/jobs,
Rust/Axum is the only database boundary, Flask is the browser-facing BFF, and
Windows or allowlisted local Playwright workers communicate through the API.

## Production topology

```text
Cloudflare -> frontend Ubuntu EC2: Nginx -> Flask/Gunicorn on 127.0.0.1:8080
                                              |
                                              +-- private DNS + HTTPS + HMAC
                                                  -> API Ubuntu EC2: Nginx
                                                     -> Axum on 127.0.0.1:3000
                                                        |-> CRM
                                                        `-> Neon

Windows worker/scheduler ---------------- private DNS + HTTPS + API keys ----^
Allowlisted local uv workers ------------ public DNS-only + HTTPS + API keys ^
```

The frontend and API are separate Ubuntu 24.04 instances in one `us-west-2`
VPC. The API has a private address for EC2 callers and an Elastic IP because
private-only developer access is unavailable. `grades-api.tutoringclub.com`
is private Route 53 DNS for EC2 callers; `grades-api-dev.tutoringclub.com` is a
public DNS-only record to the EIP for four named developer `/32` addresses.

The API is never Cloudflare-proxied. Its security group never permits
`0.0.0.0/0` or `::/0`; TCP 443 is limited to the frontend and Windows security
groups plus the four recorded developer addresses. Axum keyrings/HMAC, not
Nginx client certificates, enforce application identity. CRM and Neon normally
accept application traffic only from the API EIP, so workers and developer
machines have no direct production database path after cutover.

Deployment order is API first, local `uv` proof second, the production Windows
worker third, and the frontend last. The frontend receives no route/key that
can invoke worker endpoints, and workers receive no Flask session material.
No AWS, Cloudflare, certificate, migration, backfill, or database action has
been run by Codex; those remain manual operator actions.

## Components

- `api/`: Rust API, migrations `001`–`007`, encrypted alternate credentials,
  scoped keyrings, targeted jobs, and the dry-run-default backfill binary.
- `ui/`: Flask signed-session BFF and credential-free report DTOs. Dashboard
  student identity is read-only; grade and agenda refresh remain available.
- `scraper/`: API-only worker, agenda collector, portal engines, HTTPS clients,
  accurate progress, bounded diagnostics, and lease-abandonment behavior.
- `scripts/windows_pipeline.py`: reconciliation, deterministic UUIDv5 targeted
  enqueue, and drain commands used by Windows Task Scheduler or local proof.
- `scripts/operator_credentials.py`: masked/stdin alternate-credential CLI;
  credentials are never command-line arguments, returned, or logged.
- `deploy/`: separate systemd/Nginx/environment/release/optional-PKI artifacts
  and manual runbooks. Application IaC and containers are out of scope.

## API surface

Probes:

- `GET /livez`: dependency-free static liveness.
- `GET /readyz`: readiness key plus short concurrent CRM/Neon probes.

Dashboard-signed routes retain login, student/franchise reads,
`POST /api/jobs/manual-pull`, safe UUID job reads, and
`GET /api/dashboard/health`. Dashboard student `POST/PATCH/DELETE` routes and
browser worker controls are absent.

Service routes:

- Worker: claim, context, heartbeat, event, result, complete, and fail routes;
  every route after claim requires the authenticated identity's active lease.
- Scheduler: scoped `POST /api/scheduler/jobs` and capability-gated
  `POST /api/scheduler/reconcile-students`.
- Operator: alternate-credential `PUT/DELETE`, plus audited queued-only job
  retarget/cancel actions under `/api/operator/jobs/{id}`.

Dashboard HMAC replay claims are atomic PostgreSQL rows shared by API
instances. Rust accepts active/previous verification keys and Flask signs with
one active key. Alternate credentials use AES-256-GCM with environment, table,
schema version, field, and CRM student ID as AAD.

## Install, build, and test

```powershell
uv sync --frozen
uv run playwright install
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

Build role-separated Linux artifacts on a trusted Linux build host:

```bash
sh deploy/bin/build-release-artifacts VERSION /approved/output/directory
```

The script uses frozen/locked dependencies and emits independent API/frontend
archives plus SHA-256 files.

## Configuration inventories

Use `deploy/api/api.env.example`, `deploy/frontend/frontend.env.example`, and
`deploy/windows/windows.env.example`. Raw keys must be globally unique across
worker, scheduler, operator, and readiness roles. The API stores only named
SHA-256 digests, expiries, and scopes; each client stores only its raw key.

- Frontend only: Flask session active/previous secrets and the dashboard HMAC
  signing key.
- API only: CRM/Neon URLs, dashboard verification keys,
  `READINESS_API_KEYRING_JSON`, `WORKER_API_KEYRING_JSON`,
  `SCHEDULER_API_KEYRING_JSON`, `OPERATOR_API_KEYRING_JSON`, the AES keyring,
  and API server-TLS material.
- Windows/local worker only: matching raw worker/scheduler keys, worker
  identity, explicit target worker, and approved franchise/kind values.

System trust is the normal HTTPS client configuration. A custom CA and complete
client-certificate/key pair are optional compatibility settings only.
`ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS=true` is permitted only during the
explicit migration-005/backfill overlap. After migration 006, set it to
`false` and restart only the API role.

## Windows and local API-only pipeline

Production Windows examples:

```powershell
uv run python scripts\windows_pipeline.py --reconcile
uv run python scripts\windows_pipeline.py --franchise-id 11 --enqueue --target-worker prod-windows-01
uv run python scripts\windows_pipeline.py --drain
```

Local development uses `https://grades-api-dev.tutoringclub.com`, a scoped
developer scheduler key, and a worker key whose identity exactly matches
`--target-worker`. See runbook 07 for the copyable environment and validation
sequence. There are no Python SQL/ODBC/Sheets/JSONL write paths.

Operator alternate credentials:

```powershell
uv run python scripts\operator_credentials.py set 123
Get-Content .\credential-input.json |
  uv run python scripts\operator_credentials.py set 123 --stdin
uv run python scripts\operator_credentials.py delete 123
```

Protect and remove any stdin file through the approved secret process. Never
put credential values in arguments, shell history, logs, or tickets.

## Migrations and rollout

Migrations are additive and ordered:

1. boundary tables;
2. worker result idempotency;
3. worker leases;
4. shared replay claims and scheduler idempotency;
5. encrypted alternate-credential envelope;
6. verified plaintext removal and constrained-null legacy columns;
7. exact target-worker enforcement for every active job.

Agents must not apply migrations or backfill write mode against user databases.
Operators follow these runbooks:

1. [`01-aws-network-controls.md`](docs/runbooks/01-aws-network-controls.md)
2. [`03-hmac-replay-and-clock.md`](docs/runbooks/03-hmac-replay-and-clock.md)
3. [`04-ubuntu-two-instance-deployment.md`](docs/runbooks/04-ubuntu-two-instance-deployment.md)
4. [`05-secret-rotation-and-credential-backfill.md`](docs/runbooks/05-secret-rotation-and-credential-backfill.md)
5. [`07-api-first-local-worker-validation.md`](docs/runbooks/07-api-first-local-worker-validation.md)
6. [`06-cloudflare-public-origin.md`](docs/runbooks/06-cloudflare-public-origin.md)

[`02-private-pki.md`](docs/runbooks/02-private-pki.md) is optional legacy
compatibility guidance, not a prerequisite for this rollout.
