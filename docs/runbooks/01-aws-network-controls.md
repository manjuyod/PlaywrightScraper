# Manual AWS network and role controls

## Status and private inventory

This is an operator checklist, not infrastructure as code. Codex has not made
an AWS, Route 53, Secrets Manager, security-group, subnet, instance-profile,
monitoring, CRM, or Neon change.

Keep the following values in the approved private operations inventory, never
in Git, chat, screenshots, or this runbook:

```text
AWS account / region (must be us-west-2):
VPC ID / frontend subnet ID / API subnet ID / Windows subnet ID:
Route-table IDs and relevant routes:
Frontend / API / Windows security-group IDs and rule IDs:
Developer name -> current public IPv4 /32 (exactly four current entries):
API private IPv4 / Elastic IP:
Private Route 53 zone and record ID:
Public DNS zone and DNS-only record ID:
API server-certificate files/expiry:
Frontend / API / Windows instance-profile ARNs:
Role-specific Secrets Manager version IDs and KMS keys:
Release commit/checksum / Nginx version / rollback owner:
Approved change ticket / validation operator and timestamp:
```

## One-VPC topology

- Use one existing `us-west-2` VPC. Separate VPCs are not required.
- Keep the frontend and API on different Ubuntu 24.04 EC2 instances. The
  frontend is the Cloudflare-restricted public application origin and contains
  no API process or database credentials.
- Give the API instance a private IPv4 address plus an Elastic IP because
  private-only developer access is unavailable. Axum listens only on
  `127.0.0.1:3000`; Nginx is the sole TCP 443 listener. Do not install Flask or
  Flask session/signing secrets on this host.
- Keep the Windows worker/scheduler in its controlled subnet. EC2 callers use
  the API private address; they do not hairpin through the EIP.
- Create private Route 53 `grades-api.tutoringclub.com` pointing to the API
  private address for frontend/Windows callers.
- Create public `grades-api-dev.tutoringclub.com` as a DNS-only record pointing
  to the API EIP for allowlisted developers. Never proxy this record through
  Cloudflare and never publish the private API hostname.

Use SSM Session Manager as the administrative path. Do not create standing
SSH/RDP ingress. Any break-glass rule is approved, logged, source-restricted,
time-bound, removed immediately afterward, and included in the security-group
mutation alert.

## Security groups and routes

Create distinct frontend, API, and Windows worker security groups.

- Frontend ingress: TCP 443 only from current Cloudflare IPv4/IPv6 ranges.
  Frontend egress to API TCP 443 references the API security group. Add only
  documented AWS endpoints, DNS, NTP, monitoring, and patching paths.
- API ingress: TCP 443 only from the frontend security group, Windows worker
  security group, and the four named developer IPv4 `/32` rules. Never add
  `0.0.0.0/0`, `::/0`, a broad corporate CIDR, or an unrelated instance group.
  Record each rule ID and owner in the private inventory.
- API egress: only documented CRM SQL Server and Neon Postgres destinations/
  ports plus DNS, NTP, AWS endpoints, monitoring, and patching. CRM and Neon
  allowlists accept the API EIP as the normal application source.
- Windows egress: API TCP 443 through the API security-group reference plus
  only required portal/AWS/monitoring/DNS/NTP/patching destinations. It receives
  no application ingress authorization and no direct CRM/Neon production path.
- Developer machines receive API TCP 443 only. They do not receive database
  allowlist entries. A changed developer IP requires approved revocation of the
  old `/32`, addition of the new `/32`, alert review, and matrix re-validation.

Enable CloudTrail plus EventBridge/CloudWatch notification for every API
security-group ingress authorization/revocation. A human must review each
mutation against the approved ticket and four-person address inventory.

## Network acceptance matrix

Run before application cutover and after every security-group/subnet change.
Test the destination intended for each caller and record timestamp/operator.

| Source | Destination | Expected |
|---|---|---|
| Frontend EC2 | private DNS TCP 443 | TLS connects; application auth still required |
| Windows EC2 | private DNS TCP 443 | TLS connects; application auth still required |
| Each of four developer `/32`s | public developer DNS TCP 443 | TLS connects; application auth still required |
| Unrelated VPC instance/security group | private address TCP 443 | TCP rejected |
| Internet host not in developer inventory | EIP TCP 443 | TCP rejected |
| Any source | EIP/private TCP 80, 22, 3389 | TCP rejected |
| Any remote source | Axum TCP 3000 | TCP rejected |

Also prove CRM and Neon accept an API-host probe sourced through the EIP while
equivalent Windows/developer probes fail. Never put database credentials on a
worker merely to conduct that negative test; use network-level evidence or an
approved database administrator test identity.

## Instance profiles and secrets

Use different instance profiles and Secrets Manager paths:

- Frontend reads only Flask session active/previous values and the active
  dashboard HMAC signing key.
- API reads only CRM/Neon credentials, dashboard verifier keys, worker/
  scheduler/operator/readiness keyrings, alternate-encryption keyring, and API
  server-certificate material.
- Windows reads only its matching raw worker/scheduler/operator keys and
  approved client configuration.

Axum keyrings enforce application identity. Nginx client certificates are not
required for the target rollout. Optional legacy mTLS material, if retained,
must stay role-specific and never substitute for an application key.

Explicitly deny cross-role secret paths. Scope KMS decrypt to the role's keys
and encryption context. Never put secret values in user data, console output,
AMI metadata, logs, or alarms. Alert on retrieval failure without logging a
value.

## Alarms and operational separation

Use separate patch schedules, maintenance windows, recovery alarms, log groups,
and backups for frontend and API. Alert on:

- API security-group ingress mutations and developer `/32` inventory drift;
- queued jobs older than the approved target wait threshold;
- expired/repeated leases and Windows/local worker abandonment;
- API/frontend service restart loops and Nginx TLS or 429 spikes;
- readiness failures and API 401/403/409/503 rates;
- server-certificate/key expiry, clock offset, disk, CPU, and memory.

Back up only durable configuration and approved logs on the instances;
application data remains in CRM/Neon. A frontend replacement must not require
database credentials, and an API replacement must not require Flask session
material.

## Offline target procedure

1. The queued-age alarm opens an incident. Record the incident ID.
2. An authorized operator verifies the named target is offline, the job remains
   `queued`, and no other active `(franchise_id, kind)` row exists.
3. Choose a configured replacement worker or cancellation. Do not allow another
   worker to claim implicitly.
4. Call the operator retarget/cancel endpoint with a trimmed 1–256 character
   incident reason. Running jobs must return 409.
5. Verify the same job row and its audit event contain operator ID, reason, and
   old/new target as applicable. For retarget, verify only the new target can
   claim; for cancellation, verify terminal `cancelled` state.
6. Record operator ID, incident ID, old/new target, reason, and timestamps.

Direct SQL retarget/cancel is break-glass only and requires database-owner
approval, an immutable audit record, and immediate application-path repair.
