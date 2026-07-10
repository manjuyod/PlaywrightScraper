# Private mTLS PKI runbook

## Boundary and custody

The private root and intermediate CA are offline operator assets. Codex has not
created keys, issued certificates, published a CRL, or copied material to any
host. Run `deploy/pki` tools only on the offline CA workstation. Keep the CA
directory on encrypted removable storage with an encrypted, separately held
backup. Root and intermediate keys use masked OpenSSL passphrase prompts and
AES-256-CBC encryption; never pass a passphrase as an argument or environment
variable.

The approved leaf roles are `frontend`, `worker`, `scheduler`, and `operator`.
Each identity gets a distinct 30-day certificate and private key. Do not reuse
a key, certificate, bearer token, or common name across roles or machines. The
API server receives a separate 30-day certificate for the private DNS name
`grades-api.tutoringclub.com`.

## Manual lifecycle

1. Copy `deploy/pki` to an offline workstation and verify the release checksum.
2. Run `init-offline-ca NEW_DIRECTORY` once. Record the CA certificate
   fingerprints through a second channel and move the new directory offline.
3. Run `issue-client CA_DIRECTORY ROLE COMMON_NAME OUTPUT_DIRECTORY` once per
   frontend, worker, scheduler, or operator identity. Use inventory common
   names such as `frontend-prod-01` and `worker-prod-01`; Nginx authorization is
   based on the CA-verified OU role and the inventory records the CN/serial.
4. Run `issue-server CA_DIRECTORY grades-api.tutoringclub.com OUTPUT_DIRECTORY`
   for the API Nginx server leaf.
5. Transfer each leaf key only through the approved secret-delivery channel.
   Do not bundle it with an application archive or send it through chat/email.
6. Install CA chains, leaves, and keys using the paths in the Nginx/environment
   templates. Verify certificate subject, issuer, serial, SAN/EKU, dates, and
   SHA-256 fingerprint on the destination before Nginx/service reload.

Maintain an offline inventory containing environment, role, common name,
serial, fingerprint, issue time, expiry time, destination, and revocation
status. It must not contain private-key bytes or passphrases.

## Rotation and expiry

Issue a replacement at least seven days before expiry. Install it beside the
active leaf, verify a synthetic connection, atomically change the configured
paths, run `nginx -t` where applicable, and reload only the affected role. Keep
the old leaf for the shortest validation overlap, then revoke it and remove its
key from the destination. The role and common-name inventory must remain
stable enough for Nginx route-family authorization during overlap.

Run `check-expiry CERTIFICATE 7` daily on both Ubuntu instances and from the
Windows scheduled host. A nonzero result must create a host alarm. Also alert
when the published CRL is older than its seven-day validity or cannot be
parsed. Test rotations with both the old and new leaves before revocation and
again after CRL publication.

## Revocation and CRL publication

On the offline workstation, run `revoke-client CA_DIRECTORY CERTIFICATE`. The
command revokes the exact serial and generates a new CRL. Verify the CRL text
and next-update time, then transfer only the CRL through the approved channel.

On the API host, install the CRL atomically at
`/etc/grade-api/pki/client-ca.crl.pem`, run `nginx -t`, and reload Nginx. Do not
restart Axum. Confirm the revoked certificate fails while a valid certificate
for every route family still succeeds. If a key may have been copied or logged,
revoke immediately and rotate the corresponding bearer/HMAC secret as a
separate credential incident.

The API Nginx safe access log records certificate subject and serial, method,
path without query, status, byte count, and timing. It never records request
headers, bearer/HMAC values, bodies, or result payloads.
