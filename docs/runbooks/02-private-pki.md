# Optional private mTLS compatibility runbook

## Rollout status

Private client PKI is optional/legacy compatibility. It is not a prerequisite
for the public-API/local-worker rollout. The target Nginx configuration performs
server TLS only; Axum's HMAC and role-specific API keyrings are the application
identity/authorization boundary. Do not enable `ssl_verify_client on` in the
target rollout and do not treat possession of a client certificate as authority
to call scheduler, worker, operator, readiness, or dashboard routes.

Keep `deploy/pki` tooling for an approved compatibility deployment. If mTLS is
enabled later, it is defense in depth: every request still needs the correct
application key/HMAC, worker identity, scope, and lease. Test the key-only
target architecture first and document any compatibility exception.

## Boundary and custody

The private root and intermediate CA are offline operator assets. Codex has not
created keys, issued certificates, published a CRL, or copied material to any
host. Run `deploy/pki` tools only on an offline CA workstation. Keep the CA
directory on encrypted removable storage with a separately held encrypted
backup. Never pass a CA passphrase as an argument or environment variable.

Approved optional leaf roles are `frontend`, `worker`, `scheduler`, and
`operator`. Each machine/role gets a distinct short-lived certificate and key.
Do not reuse a certificate, key, bearer API key, or common name across roles.
The normal API server certificate is independent server-TLS material valid for
both `grades-api.tutoringclub.com` and
`grades-api-dev.tutoringclub.com`; it need not be issued by this private CA.

## Optional manual lifecycle

1. Obtain an approved compatibility change and record why server TLS plus Axum
   keyrings is insufficient for that deployment.
2. Copy `deploy/pki` to the offline workstation and verify its checksum.
3. Run `init-offline-ca NEW_DIRECTORY` once. Verify CA fingerprints through a
   second channel and move the directory offline.
4. Run `issue-client CA_DIRECTORY ROLE COMMON_NAME OUTPUT_DIRECTORY` once per
   approved machine/role. Inventory the CN, role, serial, and fingerprint.
5. Transfer each private key only through the approved secret channel. Never
   place it in an application archive, chat, email, ticket, or source tree.
6. Install a complete client certificate/key pair on the matching client and
   CA/CRL verification material on Nginx. A half-configured client pair must
   fail closed.
7. Verify certificate subject, issuer, serial, EKU, dates, and fingerprint,
   then verify the same request still fails without its Axum key.

The offline inventory contains environment, role, common name, serial,
fingerprint, issue/expiry time, destination, and revocation status—never
private-key bytes, passphrases, or raw API keys.

## Rotation, revocation, and removal

Issue a replacement at least seven days before expiry. Install beside the old
leaf, test certificate plus application-key authorization, switch atomically,
then revoke/remove the old key after the shortest practical overlap. Alert on
certificate expiry and stale/unparseable CRLs.

For revocation, run
`revoke-client CA_DIRECTORY CLIENT_CERTIFICATE` offline, verify the exact
serial and CRL next-update, and transfer only the CRL. Install atomically, run
`nginx -t`, reload Nginx, and prove the revoked leaf fails while valid leaves
still require their own application keys. Suspected disclosure also triggers
immediate rotation of the corresponding raw API key.

To leave compatibility mode, first prove all target clients work with system
trust and application keys, remove Nginx client-verification directives, run
`nginx -t`, reload, repeat positive/negative API tests, then remove unused
client private keys through the approved destruction process. Do not delete the
offline CA inventory during the retention period.

API access logs may record source IP, method, path without query, status, byte
count, timing, and request ID. They never record request headers, HMAC/API keys,
bodies, credentials, leases, certificate private data, or result payloads.
