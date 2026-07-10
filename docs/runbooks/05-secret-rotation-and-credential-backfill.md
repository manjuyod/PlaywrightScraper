# Secret rotation and alternate-credential backfill runbook

## Authority and safety

This is a manual operator procedure. Agents do not connect to user databases,
apply migrations, run backfill `--apply`, distribute keys/tokens/certificates,
or change Secrets Manager. Capture approvals, backups, current versions, and a
rollback owner before starting. Never paste secret values into tickets, logs,
shell history, screenshots, or command arguments.

## Initial encrypted-credential cutover

1. Prove Neon recovery and record counts of rows with complete, partial, and
   empty alternate credential sets using an approved read-only query.
2. Generate a 32-byte random AES key on the approved secret workstation. Give
   it a non-secret identifier such as a date/version. Store the base64 key only
   in the API Secrets Manager keyring; do not place it in the release archive.
3. Apply migrations through `005_alternate_credentials_encryption.sql`.
4. Deploy the API with the new keyring and
   `ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS=true`. Encrypted data takes priority;
   operator writes are encrypted-only.
5. On the approved API operations host, run the Rust command without `--apply`.
   Review only its structured counts:

   ```text
   backfill_alternate_credentials --limit 500
   ```

6. Run an approved bounded apply, recording `last_crmstudentid` from the summary
   for resume. Do not add credentials to arguments:

   ```text
   backfill_alternate_credentials --apply --limit 500
   backfill_alternate_credentials --apply --resume-after LAST_ID --limit 500
   ```

7. Verify every batch and then the full population. A mismatch or decryption
   error stops the cutover:

   ```text
   backfill_alternate_credentials --verify --limit 500
   backfill_alternate_credentials --verify --resume-after LAST_ID --limit 500
   ```

8. Apply `006_enforce_no_plaintext_alternate_credentials.sql`. It deliberately
   fails if plaintext exists without a complete envelope, then nulls the legacy
   columns and installs the constrained-null check.
9. Set `ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS=false`, restart only the API role,
   and repeat worker/operator synthetic tests.

Rollback before migration 006 means restoring the previous API artifact while
keeping migration 005 and the old key available. After 006, preserve the
additive schema and restore a compatible API; do not repopulate plaintext.

## Encryption-key rotation

1. Add the new key to `ALTERNATE_CREDENTIAL_KEYS_JSON`, keep all referenced old
   keys, and change `ALTERNATE_CREDENTIAL_ACTIVE_KEY_ID` to the new identifier.
2. Deploy and verify that new operator writes use the new key while workers can
   still decrypt old envelopes.
3. Dry-run, apply, and verify rotation in bounded/resumable batches:

   ```text
   backfill_alternate_credentials --rotate-key --verify --limit 500
   backfill_alternate_credentials --rotate-key --verify --apply --limit 500
   ```

4. Use an approved read-only count to prove no envelope references the retired
   identifier. Remove the old key only after that proof and a worker synthetic.

## Other overlap rotations

- Dashboard HMAC: add Rust previous verifier -> deploy new verifier -> switch
  Flask signer -> promote Rust active -> wait request age plus five seconds ->
  remove old verifier.
- Flask session: deploy new active with old fallback -> wait longer than the
  eight-hour session lifetime -> remove fallback.
- Worker/scheduler/operator bearer tokens: add a new distinct token/identity,
  deploy the matching Windows secret, verify, disable old, and confirm rejection.
- Database credentials: rotate API roles one dependency at a time, validate
  readiness, then revoke old. Never stage these on the frontend.
- mTLS leaves: issue a new 30-day role leaf, overlap distribution, verify every
  permitted/forbidden route family, switch, revoke old, publish CRL, and verify
  rejection. Rotate server leaves independently.

Any suspected disclosure skips normal overlap: disable/revoke first, stop the
affected task/service, rotate, update CRL/alarms, and resume only after negative
and positive authentication tests pass.
