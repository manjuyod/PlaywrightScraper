# Ubuntu two-instance deployment runbook

## Status and invariants

The repository artifacts are code-complete for local review. No AWS, DNS,
Cloudflare, certificate, Secrets Manager, service, or database action described
here has been applied by Codex. An operator performs every command after
substituting locally approved values.

Production always uses two different Ubuntu 24.04 EC2 instances:

```text
Cloudflare -> frontend Nginx -> Gunicorn/Flask on 127.0.0.1:8080
                                  |
                                  +-> private DNS + mTLS
                                      -> API Nginx -> Axum on 127.0.0.1:3000

Windows worker/scheduler ----------- private DNS + role mTLS ----------^
```

Never install the Flask release or database credentials on the API instance.
Never install the Axum binary, CRM/Neon credentials, readiness token, worker
token maps, or encryption keys on the frontend instance. The validation script
in `deploy/bin/validate-role-env` fails startup when those inventories cross.

## Build and release artifacts

Build on a clean, trusted build host. Produce two archives and a SHA-256 file
for each archive:

- `api-VERSION.tar.gz`: release-mode Linux `api` and dry-run-default backfill
  binaries plus migrations. The backfill binary is never installed on frontend.
- `frontend-VERSION.tar.gz`: application Python, `api_transport.py`, static
  assets, templates, `uv.lock`, and an offline-created virtual environment.
  It must not contain `api/target`, `.env`, database utilities, test output, or
  runtime caches.

Review each archive listing before transfer. Generate checksums with
`sha256sum ARCHIVE > ARCHIVE.sha256` and transfer the archive/checksum through
the approved release channel. The root-run `deploy/bin/install-release` script
verifies the checksum, extracts into an immutable version directory, retains a
`previous` link, atomically changes `current`, and restarts only that role.

## One-time host preparation

On the API instance, create the `grade-api` system user with no login shell and
install only Nginx, CA certificates, the Axum runtime artifact, CloudWatch/SSM
agents, and operating-system tooling. On the frontend instance, create the
`grade-frontend` system user and install Nginx, the pinned Python runtime, and
the frontend virtual environment. Do not install Flask on the API host.

Stage the repository files as follows:

| Source | API host destination | Frontend host destination |
|---|---|---|
| `deploy/api/systemd/grade-api.service` | `/etc/systemd/system/grade-api.service` | — |
| `deploy/frontend/systemd/grade-frontend.service` | — | `/etc/systemd/system/grade-frontend.service` |
| `deploy/api/nginx/grades-api.conf` | `/etc/nginx/conf.d/grades-api.conf` | — |
| `deploy/frontend/nginx/grade-frontend.conf` | — | `/etc/nginx/conf.d/grade-frontend.conf` |
| role logrotate file | `/etc/logrotate.d/grade-api` | `/etc/logrotate.d/grade-frontend` |
| `deploy/bin/validate-role-env` | `/usr/local/lib/grade-boundary/validate-role-env` | same |

The service units and validation script must be root-owned and not group/world
writable. Stage `/etc/grade-api/api.env` and
`/etc/grade-frontend/frontend.env` from the matching examples with owner
`root:root` and mode `0600`. Retrieve only the role's values from its own
Secrets Manager path immediately before writing the file; do not print values,
put them in shell history, user data, AMIs, or release archives.

Stage private keys as root-owned mode `0600` and certificates/CA/CRL files as
root-owned mode `0644`. Grant the Nginx worker group only the minimum traversal
and read access needed for its own key. Frontend client mTLS files are readable
by `grade-frontend` but never by other unprivileged users.

## Validation and activation

Before activation, manually run the relevant validator through a root-owned
environment staging mechanism, run `nginx -t`, and run
`systemd-analyze security grade-api.service` or
`systemd-analyze security grade-frontend.service`. Review every relaxation.

Activate the API role first. Confirm locally that `ss -lntp` shows Axum only at
`127.0.0.1:3000`; Nginx may listen on private port 443. From an allowed client
host, verify:

1. no certificate, an unknown CA, a revoked leaf, an expired leaf, and a
   wrong-role leaf all fail;
2. `GET /livez` with the correct role certificate returns only `{"status":"ok"}`;
3. `GET /readyz` additionally requires the readiness bearer token and returns
   only `ready` or `not_ready`;
4. the private hostname verifies as `grades-api.tutoringclub.com`;
5. the API private address is unreachable from the internet and unrelated VPC
   security groups.

Then activate the frontend role. Confirm Gunicorn listens only on
`127.0.0.1:8080`, the frontend archive has no API/database secrets, and a
signed Flask request reaches the private API through its frontend-role client
certificate. Do not configure the public Cloudflare record until all private
checks pass.

## Release rollback

Stop Windows scheduled tasks before an API rollback and allow active leases to
expire. Run `rollback-release api` on the API host or
`rollback-release frontend` on the frontend host; each changes only that
role's `current`/`previous` links. Re-run the private synthetic checks after a
rollback. Keep additive database schema in place and never perform destructive
incident-time migration rollback.

If a release changes a cross-role contract, retain compatible API and frontend
artifacts until both roles are verified. Restore the API before resuming the
Windows tasks.
