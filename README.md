# Student Grade Checker

PlaywrightScraper is a security-separated grade and agenda collection system.
CRM SQL Server is authoritative for students, Neon stores scraper state/jobs,
Rust/Axum is the only database boundary, Flask is the browser-facing BFF, and
the existing Windows EC2 host schedules and executes Playwright jobs through
private APIs only.

## Production topology

```text
Cloudflare -> frontend Ubuntu EC2: Nginx -> Flask/Gunicorn on 127.0.0.1:8080
                                              |
                                              +-- private DNS + mTLS
                                                  -> API Ubuntu EC2: Nginx
                                                     -> Axum on 127.0.0.1:3000
                                                        |-> CRM
                                                        `-> Neon

Windows worker/scheduler/operator CLI -------- private mTLS + bearer --------^
```

The frontend and API are never co-located in production. The API has no public
IP or public DNS record. `grades-api.tutoringclub.com` is private VPC DNS only.
No AWS, Cloudflare, certificate, migration, backfill, or database command has
been run by Codex; those remain manual operator actions.

## Components

- `api/`: Rust API, migrations `001`–`006`, encrypted alternate credentials,
  scheduler/operator/worker contracts, and the dry-run-default backfill binary.
- `ui/`: Flask signed-session BFF and credential-free report DTOs. Dashboard
  student identity is read-only; grade and agenda refresh remain available.
- `scraper/`: API-only worker, agenda collector, portal engines, mTLS clients,
  accurate progress, and lease-abandonment behavior for ambiguous delivery.
- `scripts/windows_pipeline.py`: reconcile, deterministic UUIDv5 enqueue, and
  drain commands used by Windows Task Scheduler.
- `scripts/operator_credentials.py`: masked/stdin operator credential CLI;
  credentials are never command-line arguments, returned, or logged.
- `deploy/`: separate systemd/Nginx/environment/release/PKI artifacts and
  manual runbooks. Application IaC and application containers are out of scope.

## API surface

Public/private probes:

- `GET /livez`: dependency-free static liveness.
- `GET /readyz`: readiness bearer plus short concurrent CRM/Neon probes.

Dashboard-signed routes retain login, student/franchise reads,
`POST /api/jobs/manual-pull`, safe UUID job reads, and
`GET /api/dashboard/health`. Dashboard student `POST/PATCH/DELETE` routes and
browser controls are removed.

Service routes:

- Worker: claim, context, heartbeat, event, result, complete, and fail routes,
  all lease-protected after claim.
- Scheduler: `POST /api/scheduler/jobs` and
  `POST /api/scheduler/reconcile-students`.
- Operator: alternate-credential `PUT/DELETE` under
  `/api/operator/students/{id}/alternate-credentials`.

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

The script uses frozen/locked Python, frontend, and Rust dependencies and emits
independent API/frontend archives plus SHA-256 files.

## Configuration inventories

Use the complete examples in `deploy/api/api.env.example`,
`deploy/frontend/frontend.env.example`, and
`deploy/windows/windows.env.example`. Tokens must be globally unique across
worker, scheduler, and operator identities.

- Frontend only: Flask session active/previous secrets, dashboard signing key,
  and frontend mTLS client material.
- API only: CRM/Neon URLs, dashboard verification keyring, readiness token,
  worker/scheduler/operator token maps, AES keyring, and API TLS/CA/CRL files.
- Windows only: the matching worker/scheduler/operator tokens and separate
  role certificates, scheduled franchise list, and private API CA.

`ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS=true` is permitted only during the
explicit migration-005/backfill overlap. After verification and migration 006,
set it to `false` and restart only the API role.

## Windows API-only pipeline

```powershell
uv run python scripts\windows_pipeline.py
uv run python scripts\windows_pipeline.py --reconcile
uv run python scripts\windows_pipeline.py --enqueue
uv run python scripts\windows_pipeline.py --drain
```

The default performs one reconciliation, enqueues configured franchises with
daily deterministic UUIDv5 keys, and drains until no work remains. Existing
per-franchise batch filenames remain as Task Scheduler compatibility wrappers.
There are no Python SQL/ODBC/Sheets/JSONL write paths.

Operator credentials:

```powershell
uv run python scripts\operator_credentials.py set 123
Get-Content .\credential-input.json |
  uv run python scripts\operator_credentials.py set 123 --stdin
uv run python scripts\operator_credentials.py delete 123
```

Protect and remove any stdin file through the approved secret-handling process.
Do not place credential values in arguments, shell history, logs, or tickets.

## Migrations and rollout

Migrations are additive and ordered:

1. boundary tables;
2. worker result idempotency;
3. worker leases;
4. shared replay claims and scheduler idempotency;
5. encrypted alternate-credential envelope;
6. verified plaintext removal and constrained-null legacy columns.

Agents must not apply these migrations or run backfill write mode against user
databases. Operators should follow the numbered launch and deployment runbooks
in order, adjusting the sequence only through an approved rollout review:

1. [`01-aws-network-controls.md`](docs/runbooks/01-aws-network-controls.md)
2. [`02-private-pki.md`](docs/runbooks/02-private-pki.md)
3. [`03-hmac-replay-and-clock.md`](docs/runbooks/03-hmac-replay-and-clock.md)
4. [`04-ubuntu-two-instance-deployment.md`](docs/runbooks/04-ubuntu-two-instance-deployment.md)
5. [`05-secret-rotation-and-credential-backfill.md`](docs/runbooks/05-secret-rotation-and-credential-backfill.md)
6. [`06-cloudflare-public-origin.md`](docs/runbooks/06-cloudflare-public-origin.md)
