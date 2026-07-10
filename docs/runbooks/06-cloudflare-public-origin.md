# Cloudflare public-origin runbook

## Status and prerequisites

This is a manual console checklist. Codex has not changed Cloudflare, AWS,
public DNS, a certificate, or a security group. Complete the private API and
frontend-to-API acceptance checks first. The public frontend origin is the only
Cloudflare record in this architecture; never add
`grades-api.tutoringclub.com` to public DNS or Cloudflare.

The frontend Ubuntu instance must already have its role-only release, Nginx,
root-owned Cloudflare Origin Certificate/key, loopback Gunicorn service, and
the generated trusted-range include. Confirm the API instance remains private
and separate.

## Cloudflare DNS and TLS

In the zone for `tutoringclub.com`:

1. Create or update the frontend hostname (for example
   `grades.tutoringclub.com`) to the frontend origin address. Set Proxy status
   to **Proxied**. Do not reuse the private API hostname.
2. Under **SSL/TLS > Origin Server**, create a Cloudflare Origin Certificate
   whose hostname exactly includes the frontend hostname. Install its chain and
   private key at the paths in `deploy/frontend/nginx/grade-frontend.conf` with
   the key root-owned and mode `0600`. Do not put it in the release archive.
3. Under **SSL/TLS > Overview**, select **Full (strict)**. Do not use Flexible
   or non-strict Full mode.
4. Leave the API record absent. Verify public DNS answers for the API hostname
   are `NXDOMAIN` while the VPC private resolver returns the API private
   address.

Before changing AWS ingress, request the frontend through Cloudflare and verify
certificate hostname/chain, redirect behavior, CSP/security headers, login,
and the signed private API call.

## Cloudflare Free rate-limit rule

Create one rate-limiting rule for the login path with these exact settings:

- Expression: `(http.request.uri.path eq "/login")`
- Counting characteristic: **IP**
- Period: **1 minute**
- Requests: **30**
- Action: **Block**
- Mitigation timeout: **1 minute**

The application accepts only `POST /login` credentials; the edge path rule may
also count GETs because the approved Free-plan rule is path/IP scoped. Nginx
independently limits only `POST /login` at 30 requests/minute with burst 10.
Flask continues to block after five failed attempts for the real
IP+normalized-username pair over five minutes.

Record the deployed rule ID, expression, characteristic, threshold, period,
action, timeout, and operator/date. In a controlled test, the 31st request in a
minute must be blocked at the edge. Depending on the Cloudflare response path,
record the observed HTTP 429 and Cloudflare 1015 page/event. A normal login
from an office/NAT address must still succeed after the one-minute window.

## Real client IP and origin restriction

Install `deploy/frontend/bin/update-cloudflare-ranges` and its systemd service/
timer. The script downloads Cloudflare's authenticated IP API response,
validates every CIDR with Python's `ipaddress`, writes `set_real_ip_from`
directives plus `real_ip_header CF-Connecting-IP`, runs `nginx -t`, restores
the prior include on failure, and reloads Nginx only after success.

Run it once manually during staging, inspect the generated file, then enable
the daily timer. Alert on service failure. The script changes Nginx trust only;
an operator must separately reconcile the frontend AWS security-group ingress
with the same current Cloudflare IPv4/IPv6 ranges. Review additions and
removals, apply them as one controlled change, and rerun direct-origin tests.

Nginx proxies `$remote_addr` after trusted-source restoration and Flask trusts
exactly the one loopback Nginx hop. A direct client cannot supply a trusted
`CF-Connecting-IP` because the security group accepts public 443 only from
Cloudflare ranges and Nginx trusts the header only from those sources.

## Acceptance and rollback

- Through Cloudflare: login GET/POST, secure cookie, local JS/CSS/font assets,
  strict nonce CSP, manual refresh, and logout succeed.
- Direct to the origin IP with an arbitrary Host header: TCP is rejected by the
  security group. Test from an unrelated public host.
- Repeated POSTs: Nginx returns 429 at its threshold; edge events show the
  Cloudflare block; Flask's five-failure control still keys on the restored
  address plus normalized username.
- Browser developer tools show no CDN asset requests, credentials, HMACs,
  bearer tokens, private API calls, or mixed content.

If cutover fails, revert only the frontend DNS proxy/origin setting and
frontend release/Nginx artifact. Keep the API private. Restore the last known
Cloudflare range include if range validation failed. Rotate the Origin
Certificate or frontend client certificate only when its key may have been
exposed; do not copy API/database secrets to diagnose the frontend.
