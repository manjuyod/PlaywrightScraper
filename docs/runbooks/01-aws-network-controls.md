# Manual AWS network and role controls

## Status

This is an operator checklist, not infrastructure as code. No AWS, Route 53,
Secrets Manager, security-group, subnet, instance-profile, or monitoring change
has been made by Codex.

## Instances, subnets, and routes

- Create a dedicated Ubuntu 24.04 frontend EC2 instance in the public-origin
  subnet. It is the only internet-reachable application origin. Do not place
  the API process or API/database secrets on it.
- Create a different Ubuntu 24.04 API EC2 instance in a private subnet with no
  public IPv4 address, Elastic IP, public DNS name, internet gateway route, or
  public load balancer. Do not place Flask/session secrets on it.
- Keep the existing Windows EC2 worker/scheduler in its existing controlled
  subnet. It talks to the API only over private TCP 443.
- Create a private Route 53 hosted-zone record
  `grades-api.tutoringclub.com` resolving only to the API's private address.
  Do not create a public Route 53 or Cloudflare record for this name.

Use SSM Session Manager rather than public SSH/RDP ingress. If break-glass
administration is required, make it time-bound, source-restricted, approved,
and logged.

## Security groups

Create separate frontend, API, and Windows worker security groups.

- Frontend ingress: TCP 443 only from the current published Cloudflare IPv4 and
  IPv6 ranges. No direct public 80/443 source outside those ranges after
  cutover. Frontend egress to API TCP 443 targets the API security group, not a
  broad CIDR. Add only the explicit AWS endpoints/NTP/package-patching paths
  required by operations.
- API ingress: TCP 443 only when the source security group is the frontend or
  Windows worker group. Do not add `0.0.0.0/0`, `::/0`, a corporate CIDR, or an
  unrelated instance group. API egress is limited to the documented CRM SQL
  Server destination/port, Neon Postgres destination/port, DNS/NTP, AWS service
  endpoints, monitoring, and patching paths.
- Windows worker egress: TCP 443 to the API security group plus only the portal,
  AWS service, monitoring, DNS, NTP, and patching destinations it needs. It
  receives no API/database ingress authorization.

Before public cutover, run an acceptance matrix from the frontend, Windows
worker, an unrelated VPC instance, and an internet host. The first two must
reach API Nginx; the latter two must not establish TCP. Repeat after every
security-group or subnet change.

## Instance profiles and secrets

Use different instance profiles and Secrets Manager resource paths:

- Frontend may read only its Flask session active/previous keys, active HMAC
  signing key, and frontend client-certificate material.
- API may read only database credentials, HMAC verifier keyring, worker/
  scheduler/operator token maps, readiness token, encryption keyring, API
  server certificate, client CA, and CRL.
- Windows may read only its worker/scheduler/operator identities, matching
  tokens, and matching client-certificate material.

Explicitly deny cross-role secret paths. Scope KMS decrypt permissions to the
role's keys and encryption context. Disable secret values in user data and
console output. Record retrieval failures as availability alarms without
logging a secret value.

## Operations separation

Use distinct patch schedules, maintenance windows, instance recovery alarms,
disk/CPU/memory/clock alarms, log groups, and backup policies for frontend and
API. Alert on service restart loops, Nginx TLS failures, readiness failures,
certificate/CRL expiry, clock offset, API 401/403/409/503 rates, and Windows
lease abandonment. Keep API and frontend release/rollback alarms independent.

Back up only durable configuration and approved logs on the instances;
application data remains in CRM/Neon. Test instance replacement from the
role-specific release, secret inventory, and runbook. A frontend replacement
must not require database credentials, and an API replacement must not require
Flask session material.
