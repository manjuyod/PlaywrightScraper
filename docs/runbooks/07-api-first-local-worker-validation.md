# API-first local worker validation and rollback

## Scope and authority

Use this runbook after the API host/network is staged and before enabling the
production Windows worker or frontend. It proves that an allowlisted developer
can enqueue one authorized production job, claim it only as its explicit local
worker identity, and persist one canonical result through the API. It does not
grant direct CRM/Neon access.

Academic data fetched by the authorized scraper is production data. Use an
approved franchise/student, change ticket, operator, and test window. Never
copy response context, credentials, leases, grade payloads, raw HTML, traces,
or database rows into tickets/chat. Record only safe IDs, counts, statuses,
timestamps, key IDs, and evidence locations.

## Prerequisites

The private operations inventory must contain, without committing values:

- approved release commit/checksum and API/Nginx versions;
- `us-west-2` VPC, API private IPv4/EIP, security-group/rule IDs;
- private `grades-api.tutoringclub.com` and public DNS-only
  `grades-api-dev.tutoringclub.com` record IDs;
- all four named developer `/32`s; this workstation's current public IP must
  match its approved entry;
- API server-certificate expiry and both-hostname validation;
- scoped scheduler identity/key ID/expiry/franchise/target set;
- local worker identity/key ID/expiry matching the exact target string;
- production Windows worker identity/key for negative claim testing by its
  custodian, not copied to the developer;
- API secret version IDs, database recovery point, rollback owner, and incident
  contacts.

Before touching production:

```powershell
uv sync --frozen
uv run playwright install
uv run pytest -q
uv run ruff check .
uv run python scripts\check_python_boundaries.py
```

The API release must have passed Rust unit, binary, and migrated-Postgres tests.
Migration 007 is applied only after zero active legacy jobs. Axum must listen
only on `127.0.0.1:3000`; Nginx must pass `nginx -t` and server TLS.

## Network, DNS, and TLS matrix

Record the result from every source. Do not continue on an unexpected success.

| Source | URL/address | Expected |
|---|---|---|
| Frontend EC2 | private API DNS:443 | TLS connects |
| Windows EC2 | private API DNS:443 | TLS connects |
| Each developer `/32` | developer API DNS:443 | TLS connects |
| Unrelated VPC instance | API private address:443 | TCP rejected |
| Unlisted internet host | API EIP:443 | TCP rejected |
| Any remote source | API TCP 80/22/3389/3000 | TCP rejected |

From the selected developer workstation:

```powershell
Resolve-DnsName grades-api-dev.tutoringclub.com
$response = Invoke-WebRequest -Uri https://grades-api-dev.tutoringclub.com/livez -Method Get
if ($response.StatusCode -ne 200 -or $response.Content -ne '{"status":"ok"}') { throw 'API liveness contract failed' }
Remove-Variable response
```

Confirm the resolved value is the inventoried EIP, the certificate validates
`grades-api-dev.tutoringclub.com`, and there is no Cloudflare-proxy address.
From EC2, separately confirm the private hostname resolves the private address.

Test missing and deliberately invalid worker keys; both must return 401. Never
write the real key into the command line:

```powershell
function Get-ApiStatus([hashtable]$Headers) {
  try {
    return (Invoke-WebRequest -Uri https://grades-api-dev.tutoringclub.com/api/worker/jobs/claim -Method Post -Headers $Headers).StatusCode
  } catch {
    return [int]$_.Exception.Response.StatusCode
  }
}
if ((Get-ApiStatus @{}) -ne 401) { throw 'Missing-key request did not fail closed' }
if ((Get-ApiStatus @{Authorization='Bearer deliberately-invalid'}) -ne 401) { throw 'Invalid-key request did not fail closed' }
Remove-Item function:Get-ApiStatus
```

Also verify an unknown route returns 404, an over-1-MiB request returns 413,
and a controlled burst reaches Nginx 429 without logging Authorization headers.

## Stage local secrets

Retrieve raw values through the approved secret channel into the current
process only. Do not use `SCHEDULER_ID`; the API derives scheduler identity from
the key. System trust is the normal TLS configuration.

```powershell
$env:DEPLOYMENT_ENV = "production"
$env:GRADE_API_BASE_URL = "https://grades-api-dev.tutoringclub.com"
$env:SCHEDULER_API_KEY = "<securely captured raw scoped scheduler key>"
$env:WORKER_API_KEY = "<securely captured raw local worker key>"
$env:WORKER_ID = "dev-alice-laptop"
```

Replace the identity with the private-inventory value. Its worker keyring
identity and scheduler `target_worker_ids` entry must match byte-for-byte.
`WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS` must be absent or false; production
disables sensitive tracing even if accidentally set. Optional custom CA or
client-certificate variables remain unset unless an approved compatibility
deployment requires a complete pair.

## Targeted enqueue and one local run

Use one approved franchise and, when possible, one approved student-specific
test window. The current CLI enqueues a franchise job:

```powershell
uv run python scripts\windows_pipeline.py --franchise-id 11 --enqueue --target-worker dev-alice-laptop
uv run python -m scraper.runner --once
```

Expected behavior:

1. Scheduler request returns HTTP 200 with one safe public job ID and `queued`
   status. A scheduler key outside franchise 11 or the named target returns 403.
2. Before the local claim, the production Windows worker custodian performs a
   claim with its own key and receives HTTP 204/no job. Do not transfer that key
   to the developer.
3. The local runner claims the exact job, receives context bound to its lease,
   runs at most one job, and exits successfully. Another identity or lease must
   receive 404 for context/result calls.
4. The API re-reads CRM eligibility, accepts one canonical result in Neon, and
   completes/fails the job according to the worker summary. The runner never
   connects directly to either database.
5. Repeating an identical result delivery with the same UUID is accepted
   idempotently; a changed payload or different student with that UUID returns
   409. Exercise this only through an approved synthetic/API test, never by
   replaying sensitive production payloads from a shell.

If the runner returns before completion, do not immediately enqueue again.
Inspect safe API/job status. Ambiguous delivery abandons the lease by design;
wait for the lease/incident procedure rather than guessing whether a result was
written.

## Production Neon and attribution verification

An approved API/database operator—not the developer worker—performs read-only
verification. Bind the recorded job UUID as `$1` and approved CRM student ID as
`$2`; do not interpolate either value into SQL.

```sql
SELECT id, status, franchise_id, student_id, target_worker_id, worker_id,
       scheduler_identity, attempt_count, lease_expires_at, completed_at
FROM grade_scrape_jobs
WHERE id = $1;

SELECT COUNT(*) AS canonical_results
FROM grade_scrape_results
WHERE job_id = $1;

SELECT COUNT(*) AS matching_state_rows
FROM students_grades_20262027
WHERE crmstudentid = $2;
```

Expected: target and claimed `worker_id` are the approved local identity;
`scheduler_identity` is the scoped scheduler; `attempt_count` is bounded; one
canonical result exists for the test delivery; and the eligible student's state
reflects the approved result. Do not select/log result payloads, lease tokens,
credential envelopes, or portal credentials. Verify network evidence that the
connection originated from the API EIP and that equivalent worker/developer
database paths remain rejected.

## Cleanup and test-key revocation

1. Confirm the proof job is terminal. Cancel any unused queued proof job through
   the operator API with a bounded change-ticket reason; do not delete history.
2. Verify the job event/audit rows and zero unintended active
   `(franchise_id, kind)` rows.
3. Remove temporary local environment values without printing them:

   ```powershell
   Remove-Item Env:SCHEDULER_API_KEY -ErrorAction SilentlyContinue
   Remove-Item Env:WORKER_API_KEY -ErrorAction SilentlyContinue
   Remove-Item Env:WORKER_ID -ErrorAction SilentlyContinue
   Remove-Item Env:GRADE_API_BASE_URL -ErrorAction SilentlyContinue
   Remove-Item Env:DEPLOYMENT_ENV -ErrorAction SilentlyContinue
   ```

4. If proof-only keys were used, remove their digests from the API keyrings,
   restart API, and prove the old raw values return 401. If retained for ongoing
   development, record owner/expiry and exercise overlap rotation from runbook
   05 before production Windows/frontend cutover.
5. Delete temporary credential files, browser artifacts, and shell history under
   approved endpoint policy. Confirm production created no trace/screenshot.

## Offline target and incident actions

When a queued-age alarm fires:

1. Open/record the incident and confirm the exact target is offline, the job is
   still `queued`, and no other active `(franchise_id, kind)` row exists.
2. Choose a configured replacement or cancellation. Running jobs are not
   eligible.
3. With `OPERATOR_API_KEY` securely staged, submit a trimmed 1–256 character
   reason. Example retarget request (values remain variables, not history):

   ```powershell
   $headers = @{Authorization = "Bearer $env:OPERATOR_API_KEY"}
   $body = @{target_worker_id='prod-windows-01'; reason='INCIDENT-ID local target offline'} | ConvertTo-Json -Compress
   Invoke-RestMethod -Method Post -Uri "$env:GRADE_API_BASE_URL/api/operator/jobs/$jobId/retarget" -Headers $headers -ContentType application/json -Body $body
   $body = $null
   $headers = $null
   ```

   For cancellation, POST `{"reason":"INCIDENT-ID reason"}` to
   `/api/operator/jobs/$jobId/cancel`.
4. Verify one unchanged job ID, atomic audit event, exact new claimant or
   terminal cancellation. Retarget/cancel on a running job must return 409.
5. Record operator ID, incident ID, reason, old/new target, and timestamps.

Direct SQL retarget/cancel is break-glass only.

## Lost developer device or changed public IP

For a lost/suspected-compromised device, immediately remove its API security-
group `/32`, remove its scheduler/worker/operator digests, restart API, stop
targeted jobs or use the offline-target procedure, and review CloudTrail,
Nginx, API attribution, and Neon job/result activity. Issue replacements only
after incident approval; never reuse identity key IDs or raw values.

For a legitimate IP change, verify the developer through a second channel,
remove the old `/32`, add only the new `/32`, record rule IDs/change ticket,
review the security-group mutation alert, and repeat the full network/auth
matrix. DNS does not change.

## Quiesce and rollback exercise

Exercise before final cutover:

1. Disable schedulers/manual enqueue and stop new claims.
2. Drain safely finishable work; operator-cancel queued work and wait for or
   deliberately terminate running leases. Prove zero active jobs.
3. Capture recovery point, secret versions, release/checksums, Nginx config,
   rule IDs, and rollback owner/time.
4. Restore the previous API binary/Nginx config. Restore an isolated legacy
   secret version only if required and approved.
5. If required for the old binary, execute reviewed
   `deploy/api/rollback/007_target_worker_jobs.sql`; it must refuse any active
   jobs.
6. Repeat network, TLS, missing/wrong/revoked-key, unknown-route, database-path,
   and exact-worker matrices.
7. Resume API -> local proof -> Windows -> frontend. Delete the legacy raw-token
   secret version after the rollback window.

The validation is complete only when positive sources work, every negative
path fails closed, production attribution is exact, one canonical result is
verified, trace/context handling is fail-closed, operator actions are audited,
and key revocation plus rollback have both been exercised.
