# Secret rotation and alternate-credential backfill runbook

## Authority and safety

This is a manual operator procedure. Agents do not connect to user databases,
apply migrations, run backfill `--apply`, distribute keys, or change Secrets
Manager. Capture approvals, recovery points, current versions, and a rollback
owner. Never paste raw values into tickets, logs, shell transcripts,
screenshots, command arguments, or source control.

## Application-key generation

Generate on the approved secret workstation. This example assigns but does not
print a 32-byte raw key and its lowercase SHA-256 digest:

```powershell
$raw = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$digest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($raw))).ToLowerInvariant()
```

Securely capture `$raw` once into only the matching client secret. Put only the
digest, stable identity, unique `key_id`, expiry, and scheduler scope (when
applicable) into the matching API keyring secret. Do not display `$raw` after
capture. Validate the staged identity with a synthetic request, then clear both
variables and the PowerShell history according to workstation policy:

```powershell
$raw = $null
$digest = $null
[GC]::Collect()
```

Raw keys and digests must be unique across worker, scheduler, operator, and
readiness roles. The worker identity in `WORKER_API_KEYRING_JSON` is the exact
`target_worker_id`; scheduler scope lists explicit franchise and target-worker
sets.

## Application-key overlap rotation and revocation

Rotate one identity/client at a time:

1. Generate a new raw key and digest with a new `key_id`.
2. Add the new `key_id`/digest/expiry under the **existing identity**, retaining
   the old record for overlap. Do not create a second identity to rotate one
   machine.
3. deploy/restart the API with the overlapping keyring.
4. Update only the matching client's secret with the new raw key and restart or
   reload that client.
5. Verify successful attributed traffic for the same identity and new key;
   for workers, prove exact targeted claim and lease-bound call.
6. Remove the old key record from the API keyring and restart the API again.
7. Prove the old raw key returns 401 and the new key still works. Record secret
   versions, identity, key IDs, operator, and timestamps—never raw values.

Revocation skips overlap. Remove the compromised digest immediately, restart
the API, stop the affected client, prove the old raw key is rejected, generate
and distribute a replacement, and inspect attribution/security-group events.
For a lost developer device, also revoke its public `/32` rule and local secret
material before issuing replacements.

## Initial encrypted alternate-credential cutover

1. Prove Neon recovery and record approved counts of complete, partial, and
   empty alternate-credential sets.
2. Generate a 32-byte AES key on the secret workstation. Store its base64 value
   only in the API encryption keyring with a non-secret key ID.
3. Apply `005_alternate_credentials_encryption.sql`.
4. Deploy API with the new keyring and temporary
   `ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS=true`. Encrypted data wins and new
   operator writes are encrypted-only.
5. On the approved API operations host, dry-run and review structured counts:

   ```text
   backfill_alternate_credentials --limit 500
   ```

6. Apply only an approved bounded batch and record `last_crmstudentid` for
   resume. Do not put credentials in arguments:

   ```text
   backfill_alternate_credentials --apply --limit 500
   backfill_alternate_credentials --apply --resume-after LAST_ID --limit 500
   ```

7. Verify each batch and the whole population; any mismatch/decryption error
   stops cutover:

   ```text
   backfill_alternate_credentials --verify --limit 500
   backfill_alternate_credentials --verify --resume-after LAST_ID --limit 500
   ```

8. Apply `006_enforce_no_plaintext_alternate_credentials.sql`. It fails if
   plaintext lacks a complete envelope, then nulls legacy columns and adds the
   constrained-null check.
9. Set `ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS=false`, restart only API, and
   repeat worker/operator synthetic tests.

Before migration 006, rollback retains migration 005 and the old compatible
key. After 006, preserve additive schema and restore a compatible API; never
repopulate plaintext.

## Encryption-key rotation

1. Add the new key to `ALTERNATE_CREDENTIAL_KEYS_JSON`, retain referenced old
   keys, and switch `ALTERNATE_CREDENTIAL_ACTIVE_KEY_ID`.
2. Verify new writes use the new key and workers decrypt old envelopes.
3. Dry-run, apply, and verify bounded/resumable rotation:

   ```text
   backfill_alternate_credentials --rotate-key --verify --limit 500
   backfill_alternate_credentials --rotate-key --verify --apply --limit 500
   ```

4. Prove no envelope references the retired key, run a worker synthetic, then
   remove it.

## Other overlap rotations

- Dashboard HMAC: add Rust previous verifier -> deploy verifier -> switch Flask
  signer -> promote Rust active -> wait request age plus five seconds -> remove
  old verifier.
- Flask session: deploy new active with old fallback -> wait longer than the
  session lifetime -> remove fallback.
- Database credentials: rotate API roles one dependency at a time, validate
  readiness, then revoke old. Never stage these on frontend/workers.
- Optional mTLS leaves: follow runbook 02 independently of application-key
  rotation. A certificate never substitutes for a keyring identity.
- API server certificate: install a chain valid for both API hostnames, run
  `nginx -t`, reload, verify both names/system trust, then remove the old key.

Any suspected disclosure revokes first, stops the affected task/service,
rotates, reviews access/security-group events, and resumes only after positive
and negative tests.
