# Ubuntu two-instance deployment runbook

## Status, prerequisites, and invariants

No AWS, DNS, Cloudflare, certificate, Secrets Manager, service, migration, or
database action here has been applied by Codex. An operator substitutes values
from the private inventory in runbook 01 and records approvals, release
checksum, secret versions, validation evidence, and rollback owner.

Production uses two different Ubuntu 24.04 EC2 instances in one `us-west-2`
VPC:

```text
Cloudflare -> frontend Nginx -> Gunicorn/Flask on 127.0.0.1:8080
                                  |
                                  +-> private DNS + HTTPS + HMAC
                                      -> API Nginx -> Axum 127.0.0.1:3000

Windows worker/scheduler ---------- private DNS + HTTPS + API keys ---^
Allowlisted local uv worker ------- API EIP + DNS-only + API keys -----^
```

The API private address serves EC2 callers; its EIP exists for four allowlisted
developer `/32`s. Nginx server TLS and Axum keyrings are required. Client
certificates are optional compatibility only. Never install Flask, frontend
session material, or Cloudflare origin keys on the API. Never install Axum,
CRM/Neon credentials, API keyrings, readiness keys, or encryption keys on the
frontend. `deploy/bin/validate-role-env` rejects crossed inventories.

## Build and release artifacts

Build on a clean trusted Linux host. Produce and review:

- `api-VERSION.tar.gz`: release Axum/backfill binaries and migrations. The
  backfill binary is never installed on frontend.
- `frontend-VERSION.tar.gz`: application Python, transport, static assets,
  templates, lockfile, and approved offline environment.
- One SHA-256 file per archive.

Neither archive may contain `.env`, private keys, database utilities, runtime
caches, test output, or `api/target`. Use
`deploy/bin/build-release-artifacts VERSION OUTPUT_DIRECTORY`; inspect archive
listings before transfer. `deploy/bin/install-release` verifies the checksum,
extracts an immutable version, preserves `previous`, switches `current`
atomically, and restarts only the selected role.

## One-time host preparation

On API, create non-login user `grade-api`; install only Nginx, CA certificates,
the API runtime artifact, SSM/CloudWatch agents, and approved OS tooling. On
frontend, create `grade-frontend`; install Nginx, pinned Python, and its virtual
environment. SSM Session Manager is the administrative path; do not open
standing SSH/RDP.

| Source | API destination | Frontend destination |
|---|---|---|
| `deploy/api/systemd/grade-api.service` | `/etc/systemd/system/grade-api.service` | — |
| `deploy/frontend/systemd/grade-frontend.service` | — | `/etc/systemd/system/grade-frontend.service` |
| `deploy/api/nginx/grades-api.conf` | `/etc/nginx/conf.d/grades-api.conf` | — |
| `deploy/frontend/nginx/grade-frontend.conf` | — | `/etc/nginx/conf.d/grade-frontend.conf` |
| role logrotate file | `/etc/logrotate.d/grade-api` | `/etc/logrotate.d/grade-frontend` |
| `deploy/bin/validate-role-env` | `/usr/local/lib/grade-boundary/validate-role-env` | same |

Service units and validators are root-owned and not group/world writable.
Stage `/etc/grade-api/api.env` and `/etc/grade-frontend/frontend.env` from the
matching examples as `root:root` mode `0600`. Retrieve only that role's current
Secrets Manager version immediately before staging. Never print values or put
them in user data, AMIs, shell history, release archives, or logs.

Install an API server certificate/key valid for both API hostnames at the Nginx
template paths. The private hostname and public developer hostname must verify
with normal system trust. Keys are root-owned mode `0600`; public chains may be
`0644`. Optional client certificate/key pairs are installed only under an
approved compatibility change and never replace Axum keys.

## API-first activation

Before each activation, run the role validator through a root-owned environment
staging mechanism, `nginx -t`, and
`systemd-analyze security grade-api.service` or
`systemd-analyze security grade-frontend.service`. Review every relaxation.

Deploy in this exact order:

1. **API.** Apply migrations only after legacy active jobs are drained. Start
   Axum and Nginx. Confirm `ss -lntp` shows Axum only on `127.0.0.1:3000` and
   Nginx on 443. Verify both hostnames, `/livez`, key-protected `/readyz`, 401
   for missing/wrong keys, 404 for unknown routes, 413 for bodies over 1 MiB,
   and 429 at the Nginx limit. Run the runbook-01 network matrix.
2. **Local `uv` proof.** From one allowlisted developer `/32`, run runbook 07
   with a scoped scheduler/worker key pair and one authorized franchise. Prove
   exact target claim, canonical CRM context, one production Neon result,
   idempotent retry, cleanup, and revocation/rollback behavior.
3. **Production Windows worker.** Stage its role-only raw keys and explicit
   `WINDOWS_TARGET_WORKER_ID`; run one targeted enqueue/claim/result proof via
   private DNS before enabling scheduled tasks.
4. **Frontend.** Start Gunicorn only on `127.0.0.1:8080`. Confirm the archive
   has no database/worker secrets and a signed request reaches private API DNS.
   Only then perform the Cloudflare frontend cutover in runbook 06.

At every stage, verify the frontend cannot invoke worker routes and workers do
not possess frontend session material. CRM/Neon must accept API-host traffic
and reject direct worker/developer paths.

## Quiesced API rollback

Rollback is not a live binary swap when target-worker contracts or migration
007 may differ.

1. Disable Windows/local schedulers and frontend manual enqueue.
2. Stop new claims. Drain safely finishable jobs; cancel remaining queued jobs
   through the operator API. Wait for or deliberately fail running leases.
3. Prove zero `queued`/`running` rows. Capture database backup/recovery point,
   release/checksum, Nginx config, API secret version IDs, and operator/time.
4. Restore previous API binary and Nginx configuration. Restore an isolated
   legacy secret version only if the previous binary requires it and the
   incident owner approves.
5. If the previous binary cannot read migration 007, run the reviewed
   `deploy/api/rollback/007_target_worker_jobs.sql` only after the zero-active
   proof. It refuses rollback while active rows exist.
6. Repeat positive/negative network, TLS, auth, unknown-route, and database-path
   matrices before resuming traffic.
7. Resume in API -> local proof -> Windows -> frontend order. Delete the legacy
   raw-token secret version after the approved rollback window.

Frontend-only rollback may switch its own `current`/`previous` link without an
API schema change, but still reruns signed-request and direct-origin checks.
Never perform unreviewed destructive incident-time SQL.

## Required alarms

Before enabling schedules, alarms must cover security-group ingress mutation,
old targeted queued jobs, expired/repeated leases, worker abandonment, service
restart loops, Nginx TLS/429 spikes, API 401/403/409/503 rates, readiness,
certificate/key expiry, clock drift, disk, CPU, and memory. Keep API/frontend
release and rollback alarms independent.
