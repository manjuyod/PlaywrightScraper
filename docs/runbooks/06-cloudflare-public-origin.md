# Cloudflare frontend and API DNS runbook

## Status and prerequisites

This is a manual console checklist. Codex has not changed Cloudflare, AWS, DNS,
certificates, or security groups. Complete API-first validation in runbooks 01,
04, and 07 before the frontend cutover.

Only the frontend is Cloudflare-proxied. The developer API name is public
DNS-only so AWS sees the developer's real source IP; the EC2 API name remains
private Route 53 only.

## DNS records and TLS

In the authoritative zones:

1. Create/update the frontend hostname, for example
   `grades.tutoringclub.com`, to the frontend origin and set Cloudflare Proxy
   status to **Proxied**.
2. Install a Cloudflare Origin Certificate valid for the frontend hostname at
   the paths in `deploy/frontend/nginx/grade-frontend.conf`; keep the key
   root-owned mode `0600` and out of release archives.
3. Select Cloudflare **Full (strict)**. Never use Flexible or non-strict Full.
4. Keep `grades-api.tutoringclub.com` absent from public DNS/Cloudflare. Its
   private Route 53 record resolves to the API private address only in the VPC.
5. Create `grades-api-dev.tutoringclub.com` as a public A record to the API EIP
   with Proxy status **DNS only** (gray cloud) if Cloudflare is authoritative.
   Never proxy it. The API server certificate must validate this hostname with
   normal system trust.

Confirm an EC2 caller resolves the private name/private address, an approved
developer resolves the developer name/EIP, and an unrelated public resolver
cannot resolve the private name. DNS resolution alone does not grant access;
the API security group still limits TCP 443 to the two trusted security groups
and four developer `/32`s.

## Frontend Cloudflare rate-limit rule

Create one login rule:

- Expression: `(http.request.uri.path eq "/login")`
- Counting characteristic: **IP**
- Period: **1 minute**
- Requests: **30**
- Action: **Block**
- Mitigation timeout: **1 minute**

Nginx independently limits `POST /login` at 30 requests/minute with burst 10.
Flask blocks five failed attempts for the restored IP plus normalized username
over five minutes. Record rule ID, expression, threshold, operator, and date.
In a controlled test, the 31st request must be blocked and normal use must
recover after the window.

## Real client IP and frontend origin restriction

Install `deploy/frontend/bin/update-cloudflare-ranges` plus its systemd service/
timer. It downloads Cloudflare's authenticated range response, validates each
CIDR, writes `set_real_ip_from` directives, runs `nginx -t`, restores on
failure, and reloads only after success.

Run once during staging, inspect output, then enable daily. Alert on failure.
Separately reconcile frontend AWS ingress with the same ranges as one reviewed
change. A direct client cannot forge trusted `CF-Connecting-IP`: the frontend
security group accepts origin 443 only from Cloudflare ranges and Nginx trusts
that header only from those sources.

Do not apply Cloudflare range rules or `CF-Connecting-IP` restoration to the
API. The developer API record is DNS-only, Nginx sees the direct source, and the
AWS `/32` rule remains authoritative at the network layer.

## Acceptance and rollback

- Frontend through Cloudflare: login GET/POST, secure cookie, local assets,
  strict nonce CSP, refresh, and logout succeed.
- Direct frontend origin: TCP is rejected from an unrelated host.
- Frontend repeated POSTs: Nginx/Cloudflare limits trigger as approved; Flask
  still keys failures on the restored client address.
- API private hostname: works from frontend/Windows private paths and is absent
  from public DNS.
- API developer hostname: works from each recorded developer `/32`, is not
  Cloudflare-proxied, and TCP is rejected from an unrelated public source.
- Browser developer tools expose no API keys/HMACs, private API calls, database
  credentials, leases, or mixed content.

If frontend cutover fails, revert only frontend DNS proxy/origin settings and
frontend release/Nginx. Do not broaden API ingress. If developer API DNS fails,
restore its last DNS-only EIP record; never switch it to Proxied as a shortcut.
Rotate a certificate only if its private key may have been exposed.
