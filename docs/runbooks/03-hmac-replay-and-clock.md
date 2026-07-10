# Dashboard HMAC, replay, and clock runbook

## Status and schema gate

Migration `004_dashboard_replay_nonces.sql` and the matching application code
are present in the repository. Codex has not applied the migration or connected
to a user database. An operator must apply `004` after `001`–`003` and before
deploying this API release. Do not deploy the shared-claim code against a schema
that lacks `dashboard_replay_nonces`.

The table stores only a SHA-256 identity hash, UUID nonce, claim time, and
expiry. The identity hash length-prefixes and covers the signed franchise ID,
role, and username fingerprint. An atomic insert accepts a new identity/nonce
or an expired prior row; an active conflict is unauthorized. Any Postgres error
fails the request with a generic 503. A background task deletes expired rows in
batches of at most 1,000; cleanup is not part of correctness.

## Secret inventory

- Frontend: `DASHBOARD_HMAC_SIGNING_SECRET`, the one key used to sign new
  requests. It must not contain the verifier keyring or any client-selected key
  identifier.
- API: `DASHBOARD_HMAC_ACTIVE_SECRET` and optional
  `DASHBOARD_HMAC_PREVIOUS_SECRET`. Rust computes both candidate HMACs before
  selecting an internal active/previous tag. It never reads a key ID from the
  request.

The secrets are root-staged from each instance's own Secrets Manager path and
never appear in an archive, browser response, command argument, or log. Active
and previous values must be distinct, nonempty, and unpadded.

## Zero-downtime overlap rotation

Let `A` be the current key and `B` the new key. Record version identifiers and
timestamps, but never record values.

1. Stage the new verifier on the API: keep API active=`A`, set API
   previous=`B`, restart only the API, and verify requests signed with `A`.
   Although the environment field is named previous, it is simply the second
   ordered verifier during this staging step.
2. Switch the frontend signer to `B`, restart only the frontend, and verify a
   signed read plus manual-refresh request. The API accepts both candidates.
3. Promote the API: set active=`B`, previous=`A`, restart only the API, and
   repeat the synthetic checks.
4. Wait longer than `DASHBOARD_HMAC_MAX_AGE_SECONDS` plus the five-second future
   skew and operational propagation margin. Confirm no instance still signs
   with `A`.
5. Remove `A` from the API previous field, restart only the API, confirm `B`
   succeeds, and confirm a controlled `A` vector is unauthorized.

On failure, restore the last signer/keyring pairing. Do not expand the request
age or accept a client key ID as a shortcut. Rotate the Flask session key by its
separate active/fallback procedure; do not assume HMAC and cookie secrets are
interchangeable.

## Time window and synchronization

Rust accepts signed seconds from `now - DASHBOARD_HMAC_MAX_AGE_SECONDS` through
`now + 5 seconds`, inclusive. The nonce expiry derives from signed timestamp
plus maximum age. Every host must therefore have monitored time sync.

On both Ubuntu instances, enable the approved chrony/systemd-timesyncd source,
verify synchronization before deployment, and publish clock offset plus sync
state to the role's CloudWatch alarms. Alert at one second of sustained offset
and page before five seconds. Treat an unsynchronized host as not ready for
deployment. Verify after reboot, instance replacement, NTP source changes, and
security-group changes affecting UDP 123 or the approved time source.

On Windows, keep Windows Time running and use a scheduled read-only check of
`w32tm /query /status` and `w32tm /stripchart` against the approved source.
Publish offset/sync failure without credentials. Stop scheduler/worker tasks
when offset approaches five seconds and resume only after stable sync.

## Acceptance

- The shared JSON vector in `api/testdata` produces the same HMAC in Python and
  Rust.
- Mutated method/path/query/franchise/role/user/nonce/body, malformed UUID,
  stale timestamp, and timestamp more than five seconds ahead are rejected.
- A valid active or previous signature is accepted during overlap; a retired
  signature is rejected afterward.
- Two API instances using the same disposable Postgres accept only one active
  claim. A database outage yields generic 503, and bounded cleanup never allows
  an active replay.
