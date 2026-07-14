# Public API and local worker deployment design

## Objective

Deploy the Rust boundary API in AWS first, then prove the production data path
by enqueueing a job for a specifically named developer worker and running that
worker locally with `uv`. The local scraper reads its authorized job context
from the API and returns fetched academic data to the API; only the API reads
CRM SQL Server or writes Neon Postgres.

This is an early-stage, cost-conscious deployment. It deliberately favors
direct, source-restricted HTTPS and role-specific application keys over a NAT
Gateway, interface VPC endpoints, or mandatory client-certificate operations.

## Goals

- Keep CRM and Neon credentials exclusively on the API host.
- Preserve the frontend, API, and worker as separate trust roles.
- Allow the existing Windows EC2 worker and local developer workers to use the
  same API contract.
- Permit remote API development only from four approved public IPv4 addresses:
  the primary developer's home and work addresses and one address for each of
  the other two developers.
- Give every scheduler, worker, and operator a distinct, revocable identity and
  key.
- Ensure a local worker can claim only a job explicitly targeted to it.
- Apply validated local-worker results to production Neon with complete job,
  worker, scheduler, lease, and timestamp attribution.
- Avoid a NAT Gateway and its hourly/data-processing charges.

## Non-goals

- A generally public API reachable from `0.0.0.0/0` or `::/0`.
- Browser-to-API calls or browser-held application keys.
- Direct CRM or Neon access from the frontend or any worker.
- A separate development database or record-only development result path.
- Automatic infrastructure provisioning; AWS changes remain an operator task.
- Worker-to-worker or frontend-to-worker connectivity.

## Topology

All EC2 instances run in `us-west-2` and the same VPC.

```text
Cloudflare -> frontend EC2 -> private HTTPS -> API EC2 -> public TLS -> CRM SQL
                                               |
                                               +-> public TLS -> Neon Postgres

Windows worker EC2 -------- private HTTPS -----^

Allowlisted developer IP -- public HTTPS ------^
                                  |
                                  +-- local uv scheduler/worker clients
```

The API EC2 instance uses a public-routed subnet and an Elastic IP. The Elastic
IP supplies stable outbound source identity for CRM and Neon allow rules. The
API is not broadly internet-reachable: its security group accepts TCP 443 only
from the frontend security group, Windows worker security group, and explicitly
approved developer `/32` addresses.

The architecture accepts the defense-in-depth tradeoff of a public IP on the
API instance to eliminate NAT infrastructure. A future accidental public
ingress rule would increase exposure, so public-ingress changes must be alerted
and reviewed.

## Naming and routing

- `grades-api.tutoringclub.com` is a Route 53 private record resolving to the
  API private IPv4 address. Frontend and EC2 workers use this hostname.
- `grades-api-dev.tutoringclub.com` is a public DNS-only record resolving to the
  API Elastic IP. It must not be Cloudflare-proxied because the API security
  group must see and enforce each developer's actual source IP.
- Both names use HTTPS with a publicly trusted server certificate whose SANs
  cover the names presented to their clients.
- HTTP, SSH, and RDP are not opened on the API. Administration uses SSM Session
  Manager.

Security-group references authorize the frontend and Windows instances by
their private network interfaces. Those clients must not connect to the API's
public address.

## Security groups

### API inbound

- TCP 443 from the frontend security group.
- TCP 443 from the Windows worker security group.
- TCP 443 from each approved developer IPv4 address as an individual `/32`
  rule with an owner and purpose in the description.
- No `0.0.0.0/0`, `::/0`, corporate-wide CIDR, SSH, or RDP rule.

### API outbound

- CRM SQL Server public address on its documented port.
- Neon Postgres on its documented port. Because Neon endpoint addresses can
  change and security groups cannot filter by hostname, the destination rule
  may need to be broader than a single `/32`; database TLS and Neon source-IP
  restrictions remain mandatory.
- HTTPS for SSM, Secrets Manager, KMS, logging, release retrieval, and approved
  operating-system update paths.
- Required DNS and time synchronization.

### Frontend and worker directionality

- Frontend egress permits private TCP 443 to the API security group and its
  operational dependencies. It receives no route or security-group permission
  to initiate traffic to workers.
- Windows worker egress permits private TCP 443 to the API security group plus
  required school portals and operational dependencies.
- Workers receive no application ingress from the frontend or API.

## Authentication model

Nginx provides server-authenticated TLS, source-IP request limits, header/body
limits, and proxying. Axum is the sole application authentication and
authorization boundary for API routes. Nginx forwards the `Authorization`
header without logging its value; it does not interpret application keys.
Mandatory Nginx client-certificate authentication is removed for this
key-based deployment.

- Frontend API calls retain the existing timestamped HMAC, nonce, user, role,
  and franchise claims.
- Each production or local worker has a unique worker identity and bearer key.
- Each developer who may enqueue jobs has a unique scheduler identity and
  bearer key.
- Every scheduler identity has a server-side policy listing the franchise IDs
  it may enqueue and the worker identities it may target. A request outside
  either allowlist is rejected even when the key is valid.
- Operator keys remain separate and are not implied by developer, scheduler,
  or worker access.
- Readiness retains its separate bearer token.
- Clients store raw keys only in their approved role-specific secret store.
  The API secret inventory stores only SHA-256 key digests plus identity,
  role, expiry, and scope metadata. Keys are generated with at least 256 bits
  of cryptographic randomness, verified in constant time, never logged, and
  redacted from diagnostics and request capture.
- Keys are individually expiring and revocable. Rotation adds a new digest,
  updates the client, verifies it, then removes the old digest. Emergency
  revocation removes the digest and performs a controlled API configuration
  reload or restart immediately.
- Nginx rate-limits by source IP; Axum additionally rate-limits authenticated
  scheduler, worker, operator, and readiness identities independently.

No shared developer super-key is introduced. A developer running both
scheduler and worker operations receives two distinct keys because those roles
have different route authority.

## Targeted jobs

The current global claim query allows any authenticated worker to claim the
oldest eligible job. Before production and local workers run concurrently, the
job contract gains `target_worker_id`.

- Every newly queued job has a nonempty target worker identity.
- The scheduler request accepts the target identity, verifies it is a
  configured worker permitted by that scheduler's policy, verifies the
  franchise is permitted by the same policy, and includes the target in its
  idempotency hash.
- Frontend/manual jobs use the configured default production worker identity.
- The worker claim query returns only jobs whose target matches the identity
  derived from the presented worker key.
- Post-claim context, heartbeat, event, result, completion, and failure calls
  continue to require both the worker identity and the active lease token.
- Existing active jobs are drained or explicitly backfilled before activating
  the new claim rule. Historical completed jobs may retain a null target.
- The existing active-job uniqueness rule remains global per franchise and job
  kind. A local and production job cannot concurrently process the same center
  and kind.

An unavailable target worker leaves its job queued. It does not fall through
to a different worker. Retargeting or cancellation is an explicit, audited
operator action.

## Production writes from local workers

Local workers are first-class authorized workers, not dry-run processors.
After a local worker obtains a targeted lease, the API returns the scoped portal
context required for that job. The local Playwright process fetches academic
data and submits results to the API.

Portal context is restricted to the leased job and the minimum fields needed
by its portal adapters: job/franchise/student identifiers, portal type and URL,
the scoped portal username/password, and only portal-specific authentication
artifacts that adapter requires. When alternate credentials are stored as an
encrypted envelope, the API decrypts them and transmits the minimum plaintext
credential fields to the leased worker over TLS; encryption keys and envelope
metadata never leave the API. The context never includes CRM or Neon connection
credentials, API key inventories, unrelated students, or operator secrets. The
worker keeps the context in memory, excludes it from logs/events/screenshots/
crash reports, does not persist it to disk or browser profiles, and releases
its references and closes the browser context when the lease terminates.

The API applies a result to canonical production state only after checks
confirm the worker owns the targeted running job, the lease token still matches
and has not expired, the result schema and size are valid, the returned student
matches the job scope, the result is fresh for that active lease attempt, and
CRM still recognizes the canonical eligible student. Result submission uses a
per-job/per-student idempotency key and a database transaction so a retry after
an ambiguous response cannot duplicate or partially apply a write. An expired,
superseded, completed, or failed lease can never write a late result. Failures
remain job results/events rather than direct database writes from the worker.

## Local `uv` workflow

The intended developer workflow is:

```powershell
$env:GRADE_API_BASE_URL = "https://grades-api-dev.tutoringclub.com"
$env:SCHEDULER_API_KEY = "<developer-scheduler-key>"
$env:WORKER_API_KEY = "<developer-worker-key>"

uv run python scripts\windows_pipeline.py `
  --franchise-id <approved-franchise-id> `
  --enqueue `
  --target-worker <developer-worker-id>

uv run python -m scraper.runner --once
```

The target-worker CLI option and API field are part of the required
implementation; they do not exist yet. Until they are deployed and verified,
local workers must not drain the hosted production queue while the production
worker is active.

## Failure behavior

- An unapproved source IP fails at the security-group layer before TLS.
- An approved IP with a missing, invalid, or wrong-role key receives a generic
  401 response.
- A valid worker with no job targeted to its identity receives 204 and changes
  no state.
- A worker presenting another worker's lease receives a generic 401 or 404 and
  cannot read job context or write results.
- If CRM or Neon is unavailable, readiness returns only `not_ready`, API
  operations fail safely, and workers do not fall back to direct database
  access.
- If a developer's public IP changes, its old `/32` is removed and its new
  `/32` is approved before access resumes; no temporary `0.0.0.0/0` rule is
  permitted.
- If a target worker remains offline, alert on queued-job age and require an
  explicit retarget or cancel decision.

## Verification

### Application tests

- Migration tests cover target-worker storage, result idempotency, and
  active-job constraints.
- Scheduler tests cover franchise/worker policy enforcement, target
  validation, expiry, revocation, and idempotency hashing.
- Claim tests prove that production and local identities cannot claim each
  other's jobs.
- Lease tests continue to cover context, heartbeat, event, result, complete,
  and fail routes.
- Result tests prove an authorized local worker updates canonical production
  state only for the exact scoped student and that duplicate, oversized,
  malformed, stale, and post-expiry submissions cannot write.
- Nginx tests prove application keys reach Axum without a client certificate
  while unknown paths and oversized/rate-limited requests fail.

### Network acceptance

- Frontend and Windows EC2 instances reach the API private hostname on 443.
- Every approved developer address reaches the public development hostname.
- An unrelated internet host and unrelated VPC security group cannot establish
  TCP 443.
- No source can reach API HTTP, SSH, RDP, or the Axum loopback port directly.

### End-to-end local proof

1. Stop or observe the production worker so the first test is controlled.
2. Enqueue one student-scoped job targeted to a named local worker.
3. Prove the production worker receives no claim for that job.
4. Run `uv run python -m scraper.runner --once` locally.
5. Observe claim, context, heartbeat, event, result, and completion attribution.
6. Verify the authorized fetched academic result appears in canonical Neon
   state and the API/frontend view.
7. Repeat negative tests with a wrong worker key, wrong lease, and unapproved
   source address.

## Rollout order

1. Implement and test target-worker support, Axum key authorization and
   identity policies, and Nginx key pass-through without client certificates.
2. Prepare API secrets, TLS certificate, Elastic IP, DNS, monitoring, and
   source-restricted security group.
3. Deploy the API and validate CRM/Neon readiness from the API host.
4. Verify private Windows connectivity and public developer connectivity.
5. Add one developer scheduler identity and one local worker identity.
6. Run the single targeted local `uv` proof described above.
7. Enable the production Windows schedule only after targeted claims and
   production writes are verified.
8. Deploy and connect the frontend after the API/worker data path is stable.

## Deferred improvements

- Cloudflare Tunnel or Access if developer IP maintenance becomes burdensome.
- Private API subnet with managed NAT if defense-in-depth requirements justify
  the recurring cost.
- Worker pools and automatic failover when more than one production worker is
  required.
- Separate development data branches if future testing must not affect
  production canonical state.
