# Public API and Local Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the existing Rust API on an inexpensive public-routed EC2 instance whose HTTPS ingress is restricted to trusted EC2 security groups and developer `/32` addresses, then let explicitly targeted local `uv` workers write authorized scraper results to production through that API.

**Architecture:** Keep the frontend, API, and Windows worker in one `us-west-2` VPC, using private addresses and security-group references for EC2-to-EC2 traffic. Give the API an Elastic IP only because local developers need a stable allowlisted endpoint. Axum remains the sole application authentication/authorization boundary, using SHA-256 key digests, expiry, per-identity scopes, and per-identity request limits. Every newly queued job names one worker identity; the existing lease, active-job uniqueness, result idempotency, canonical CRM revalidation, and production Neon write path remain authoritative.

**Tech Stack:** Rust/Axum, SQLx/Postgres, Python 3 with `uv`, Nginx, AWS EC2/VPC/Security Groups/Route 53/Secrets Manager/SSM, pytest, Cargo test/Clippy.

**Design source:** `docs/superpowers/specs/2026-07-14-public-api-local-worker-design.md`

## Global Constraints

- Apply the tasks in order. Tasks 1–10 change application behavior; Tasks 11–12 prepare and verify deployment; Task 13 is the manual AWS rollout.
- Route each bounded coding task through the `blaziken` GPT-5.3 Codex Spark agent. The primary Codex agent retains responsibility for GitNexus impact analysis, reviewing every resulting diff, making any required fixes itself, running verification, and deciding what is safe to commit.
- Before editing every existing Rust or Python function, class, or method, run GitNexus upstream impact analysis for that symbol and report direct callers, affected flows, and risk. Stop and warn the operator before changing a `HIGH` or `CRITICAL` risk symbol.
- Before every commit, stage only the task's intended files and run `gitnexus_detect_changes({scope: "staged"})`. Do not commit if unexpected symbols or flows appear.
- Preserve the current global active-job uniqueness rule `(franchise_id, kind)`. Targeting chooses who may claim a job; it must not create a second active job for the same franchise and kind.
- Never log raw API keys, `Authorization`, portal credentials, request bodies, or result payloads.
- Use UTC RFC 3339 timestamps for key expiry. Store only lowercase, 64-character SHA-256 digests in API configuration.
- Every task must leave the tree compiling and its focused tests passing. Do not defer a required caller update to a later commit.
- Keyring configuration must allow two or more keys for one identity during rotation. The API authorizes the identity and its policy, never a key ID supplied by a client.
- Scheduler idempotency lookup remains exactly `(scheduler_identity, scheduler_idempotency_key)`. The target worker is covered by the stored request hash so changing the target with the same idempotency key deterministically returns `409`.
- Database lifecycle claims require an ephemeral PostgreSQL integration test. SQL-string assertions supplement but never replace those tests.
- Worker context contains only adapter-essential fields. Portal credentials and browser artifacts are never persisted or logged and are released from live Python collections in a `finally` block after each job.
- Operational rollback is a quiesced procedure: stop enqueue/claim traffic, drain or cancel active jobs, restore the previous application/secret/Nginx versions, and deliberately remove the active-target constraint only if the old binary must run.

---

### Task 1: Add hashed, expiring, scoped API keyrings

**Files:**

- Create: `api/src/api_keys.rs`
- Modify: `api/src/lib.rs`
- Test: `api/src/api_keys.rs`

**Interfaces:**

- Consumes: `sha2`, `subtle`, `chrono`, `serde`, and the existing identity naming rules.
- Produces: `BasicKeyring`, `SchedulerKeyring`, keyring parsers, key identifiers, constant-time matchers, and cross-role digest validation used by Task 2.

```rust
#[derive(Clone)]
pub struct ApiKeyRecord {
    pub key_id: String,
    digest: [u8; 32],
    pub expires_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Clone)]
pub struct BasicKeyIdentity {
    pub keys: Vec<ApiKeyRecord>,
}

#[derive(Clone)]
pub struct SchedulerKeyIdentity {
    pub keys: Vec<ApiKeyRecord>,
    pub franchise_ids: std::collections::HashSet<i32>,
    pub target_worker_ids: std::collections::HashSet<String>,
    pub can_reconcile: bool,
}

pub type BasicKeyring = std::collections::HashMap<String, BasicKeyIdentity>;
pub type SchedulerKeyring = std::collections::HashMap<String, SchedulerKeyIdentity>;

pub struct AuthenticatedKey {
    pub identity: String,
    pub key_id: String,
}

pub struct AuthenticatedSchedulerKey {
    pub identity: String,
    pub key_id: String,
    pub franchise_ids: Arc<HashSet<i32>>,
    pub target_worker_ids: Arc<HashSet<String>>,
    pub can_reconcile: bool,
}

pub fn parse_basic_keyring_json(value: &str, role: &str) -> Result<BasicKeyring, String>;
pub fn parse_scheduler_keyring_json(value: &str) -> Result<SchedulerKeyring, String>;
pub fn identify_basic_key(
    keyring: &BasicKeyring,
    raw_key: &str,
    now: chrono::DateTime<chrono::Utc>,
) -> Option<AuthenticatedKey>;
pub fn identify_scheduler_key(
    keyring: &SchedulerKeyring,
    raw_key: &str,
    now: chrono::DateTime<chrono::Utc>,
) -> Option<AuthenticatedSchedulerKey>;
pub fn validate_cross_role_digest_uniqueness(
    worker: &BasicKeyring,
    scheduler: &SchedulerKeyring,
    operator: &BasicKeyring,
    readiness: &BasicKeyring,
) -> Result<(), String>;
```

The uniqueness helper stays inside `api_keys.rs`, where it can read the private digest field. Use this implementation; no digest accessor is exposed outside the module:

```rust
pub fn validate_cross_role_digest_uniqueness(
    worker: &BasicKeyring,
    scheduler: &SchedulerKeyring,
    operator: &BasicKeyring,
    readiness: &BasicKeyring,
) -> Result<(), String> {
    let mut seen = HashSet::<[u8; 32]>::new();
    for identity in worker.values() {
        for key in &identity.keys {
            if !seen.insert(key.digest) {
                return Err("API key digests must be unique across roles".into());
            }
        }
    }
    for identity in scheduler.values() {
        for key in &identity.keys {
            if !seen.insert(key.digest) {
                return Err("API key digests must be unique across roles".into());
            }
        }
    }
    for keyring in [operator, readiness] {
        for identity in keyring.values() {
            for key in &identity.keys {
                if !seen.insert(key.digest) {
                    return Err("API key digests must be unique across roles".into());
                }
            }
        }
    }
    Ok(())
}
```

- [ ] Run GitNexus context and upstream impact analysis for the `lib.rs` module exports before editing. Task 1 creates new symbols and deliberately does not replace `ApiConfig`, so this commit remains independently compilable.

- [ ] Add failing unit tests for parsing and matching keyring records:

  - `parses_two_rotation_keys_for_one_identity` accepts two distinct key IDs/digests for one identity;
  - `identifies_both_overlap_keys_as_the_same_identity` proves both raw keys authorize the same identity during overlap;
  - rejects raw plaintext values, malformed digests, duplicate digests, and malformed expiry;
  - refuses an expired key at request time;
  - matches a presented key by hashing it and comparing fixed-size digest bytes in constant time;
  - carries scheduler `franchise_ids`, `target_worker_ids`, and `can_reconcile` policy;
  - rejects duplicate key IDs within one identity and the same digest under two identities.
  - `rejects_digest_reuse_across_roles` calls `validate_cross_role_digest_uniqueness` and fails when any digest is present in more than one role; digest bytes remain private to `api_keys.rs`.

  Add this exact overlap test (with `use sha2::{Digest, Sha256};`):

  ```rust
  #[test]
  fn identifies_both_overlap_keys_as_the_same_identity() {
      let old_raw = "old-worker-key";
      let new_raw = "new-worker-key";
      let json = serde_json::json!({
          "dev-alice-laptop": {
              "keys": [
                  {"key_id": "old", "sha256": hex::encode(Sha256::digest(old_raw.as_bytes())), "expires_at": "2099-01-01T00:00:00Z"},
                  {"key_id": "new", "sha256": hex::encode(Sha256::digest(new_raw.as_bytes())), "expires_at": "2099-02-01T00:00:00Z"}
              ]
          }
      });
      let keyring = parse_basic_keyring_json(&json.to_string(), "worker").unwrap();
      let now = "2098-01-01T00:00:00Z".parse().unwrap();
      let old = identify_basic_key(&keyring, old_raw, now).unwrap();
      let new = identify_basic_key(&keyring, new_raw, now).unwrap();
      assert_eq!(old.identity, "dev-alice-laptop");
      assert_eq!(new.identity, "dev-alice-laptop");
      assert_eq!(old.key_id, "old");
      assert_eq!(new.key_id, "new");
  }
  ```

- [ ] Run the focused tests and confirm they fail because the keyring types do not exist:

  ```powershell
  Set-Location api
  cargo test api_keys --locked
  ```

  Expected: compilation/test failure naming the missing keyring records or parsers.

- [ ] Implement focused key types in `api/src/api_keys.rs`. Keep raw keys out of `Debug` output. Use a shared record plus scheduler policy:

  ```rust
  pub struct ApiKeyRecord {
      pub key_id: String,
      digest: [u8; 32],
      pub expires_at: chrono::DateTime<chrono::Utc>,
  }
  ```

  Hash the presented raw key once, compare all fixed-size configured digests with `subtle::ConstantTimeEq`, and select at most one non-expired record. Do not return early based on key order.

  Parse JSON configuration shaped like:

  ```json
  {
    "dev-alice": {
      "keys": [
        {
          "key_id": "2026-07",
          "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
          "expires_at": "2027-01-01T00:00:00Z"
        },
        {
          "key_id": "2026-08",
          "sha256": "1111111111111111111111111111111111111111111111111111111111111111",
          "expires_at": "2027-02-01T00:00:00Z"
        }
      ],
      "franchise_ids": [11, 12],
      "target_worker_ids": ["dev-alice-laptop"],
      "can_reconcile": false
    }
  }
  ```

- [ ] Run formatting and focused tests:

  ```powershell
  Set-Location api
  cargo fmt --all
  cargo test api_keys --locked
  ```

  Expected: all keyring/config tests pass; no raw key appears in assertion output.

- [ ] Stage only Task 1 files, run GitNexus change detection, review the expected `ApiConfig` blast radius, then commit:

  ```powershell
  git add api/src/api_keys.rs api/src/lib.rs
  git commit -m "feat(api): add scoped hashed API keyrings"
  ```

### Task 2: Authenticate identities and enforce per-identity request limits

**Files:**

- Create: `api/src/rate_limit.rs`
- Modify: `api/src/lib.rs`
- Modify: `api/src/config.rs`
- Modify: `api/src/auth.rs`
- Modify: `api/src/error.rs`
- Modify: `api/src/state.rs`
- Modify: `api/src/routes.rs`
- Test: `api/src/rate_limit.rs`
- Test: `api/src/auth.rs`
- Test: `api/src/routes.rs`

**Interfaces:**

- Consumes: Task 1 keyring types and match/validation functions.
- Produces: hashed-key `ApiConfig`, authenticated worker/scheduler/operator/readiness claims, shared `IdentityRateLimiter`, and `401`/`429` behavior used by all routes.

```rust
pub struct ApiConfig {
    pub worker_api_keyring: BasicKeyring,
    pub scheduler_api_keyring: SchedulerKeyring,
    pub operator_api_keyring: BasicKeyring,
    pub readiness_api_keyring: BasicKeyring,
    pub default_worker_id: String,
    // existing database, lease, HMAC, credential, bind, and logging fields remain
}

#[derive(Clone)]
pub struct SchedulerAuthClaims {
    pub scheduler_id: String,
    pub key_id: String,
    pub franchise_ids: Arc<HashSet<i32>>,
    pub target_worker_ids: Arc<HashSet<String>>,
    pub can_reconcile: bool,
}

pub struct WorkerAuthClaims {
    pub worker_id: String,
    pub key_id: String,
    pub lease_token: Option<Uuid>,
}

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
pub enum ApiRole { Worker, Scheduler, Operator, Readiness }

#[derive(Clone)]
pub struct IdentityRateLimiter {
    windows: Arc<std::sync::Mutex<HashMap<(ApiRole, String), RateWindow>>>,
    window: std::time::Duration,
}

struct RateWindow {
    started_at: std::time::Instant,
    count: u32,
}

impl IdentityRateLimiter {
    pub fn new(window: std::time::Duration) -> Self;
    pub fn check(&self, role: ApiRole, identity: &str, limit: u32) -> Result<(), ApiError>;
    fn check_at(&self, role: ApiRole, identity: &str, limit: u32, now: std::time::Instant) -> Result<(), ApiError>;
}
```

Environment interface:

```text
WORKER_API_KEYRING_JSON
SCHEDULER_API_KEYRING_JSON
OPERATOR_API_KEYRING_JSON
READINESS_API_KEYRING_JSON
DEFAULT_WORKER_ID
```

- [ ] Run GitNexus upstream impact analysis for `ApiConfig`, `ApiConfig::from_env`, `worker_auth`, `scheduler_auth`, `operator_auth`, `identity_for_token`, `ApiError`, `AppState`, and the readiness handler. Report risk before editing. Include the test-config constructors in `auth.rs`, `routes.rs`, and `state.rs` in the blast-radius report.

- [ ] Add failing tests proving:

  - valid raw keys map to the configured identity without retaining the raw value;
  - expired/revoked/unknown keys return `401`;
  - scheduler claims include their franchise, target-worker, and reconcile policy;
  - each `(role, identity)` has an independent fixed-window allowance;
  - the first excess request returns `429` with code `rate_limited`;
  - readiness keys use the same digest/expiry/identity rules;
  - the limiter evicts stale windows so arbitrary identities cannot grow memory forever.
  - `default_worker_must_exist` rejects an unknown `DEFAULT_WORKER_ID`;
  - `scheduler_targets_must_reference_workers` rejects every scheduler target not present in the worker keyring;
  - `digests_must_be_unique_across_roles` rejects a digest reused across any two roles while allowing multiple distinct keys for one identity.

  Name the remaining new tests `expired_key_is_unauthorized`, `scheduler_claims_carry_policy`, `rate_limits_are_independent_per_identity`, `rate_limit_returns_429`, `readiness_uses_keyring_identity`, and `stale_rate_windows_are_evicted`.

  Use this exact identity-isolation test:

  ```rust
  #[test]
  fn rate_limits_are_independent_per_identity() {
      let limiter = IdentityRateLimiter::new(std::time::Duration::from_secs(60));
      let now = std::time::Instant::now();
      assert!(limiter.check_at(ApiRole::Worker, "worker-a", 1, now).is_ok());
      assert!(matches!(
          limiter.check_at(ApiRole::Worker, "worker-a", 1, now),
          Err(ApiError::RateLimited)
      ));
      assert!(limiter.check_at(ApiRole::Worker, "worker-b", 1, now).is_ok());
      assert!(limiter.check_at(ApiRole::Scheduler, "worker-a", 1, now).is_ok());
  }
  ```

- [ ] Run focused tests and confirm failure:

  ```powershell
  Set-Location api
  cargo test auth --locked
  cargo test rate_limit --locked
  cargo test readiness --locked
  cargo test config --locked
  ```

- [ ] Implement a small in-process fixed-window limiter keyed by `(role, identity)`, with a testable clock entry point and stale-window cleanup. Use these initial ceilings and document them beside the constants:

  ```text
  worker:    600 requests/minute
  scheduler:  60 requests/minute
  operator:   30 requests/minute
  readiness:  60 requests/minute
  ```

  Return `ApiError::RateLimited` as HTTP `429`. Rate limiting occurs only after successful authentication so unknown keys do not create unbounded identity entries.

- [ ] Replace `ApiConfig` plaintext token fields and `READINESS_API_TOKEN` with the five environment interfaces above. Update every direct `ApiConfig` struct literal in `auth.rs`, `routes.rs`, and `state.rs` in this same task so the commit compiles. After parsing, call Task 1's `validate_cross_role_digest_uniqueness`; do not access private digest bytes from `config.rs`. `READINESS_API_KEYRING_JSON` uses named identities for attributable revocation/expiry/limits. Keep the dashboard HMAC verifier unchanged because HMAC verification requires verifier material.

- [ ] Replace plaintext token lookup in auth middleware with keyring matching and construct the exact worker/scheduler claim interfaces above. Operator and readiness authentication also retain the matched safe `key_id` for rotation telemetry; authorization and rate limits remain identity-based.

- [ ] Put one shared limiter in `AppState`; do not create a new limiter per request. Apply readiness authentication and rate limiting before database checks.

- [ ] Run focused tests and format:

  ```powershell
  Set-Location api
  cargo fmt --all
  cargo test auth --locked
  cargo test rate_limit --locked
  cargo test readiness --locked
  cargo test config --locked
  ```

  Expected: valid identities pass, expired keys fail closed, and only the over-limit identity receives `429`.

- [ ] Stage Task 2 files, run GitNexus change detection, and commit:

  ```powershell
  git add api/src/rate_limit.rs api/src/lib.rs api/src/config.rs api/src/auth.rs api/src/error.rs api/src/state.rs api/src/routes.rs
  git commit -m "feat(api): enforce key identity limits"
  ```

### Task 3: Add exact worker targeting to jobs

**Files:**

- Create: `api/migrations/007_target_worker_jobs.sql`
- Create: `deploy/api/rollback/007_target_worker_jobs.sql`
- Modify: `api/src/models.rs`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_ci_config.py`
- Test: `api/src/models.rs`

**Interfaces:**

- Consumes: existing job tables, scheduler request validation, and active-job uniqueness.
- Produces: migration/rollback 007 plus target-aware scheduler/internal job models used by Tasks 4–6.

```rust
pub struct SchedulerJobRequest {
    pub idempotency_key: uuid::Uuid,
    pub kind: SchedulerJobKind,
    pub franchise_id: i32,
    pub student_id: Option<i64>,
    pub target_worker_id: String,
}

pub struct WorkerJob {
    // existing internal fields
    pub target_worker_id: Option<String>, // null only for pre-007 terminal history
}
```

Database contract: `target_worker_id` may be null only for terminal history created before migration 007. Every `queued` or `running` row must contain a nonblank target. The existing partial unique active-job index remains unchanged.

- [ ] Run GitNexus upstream impact analysis for `SchedulerJobRequest`, `WorkerJob`, and their validation/hash methods. Report risk before editing.

- [ ] Add failing migration and model tests proving:

  - active jobs must have a nonblank `target_worker_id`;
  - historical terminal jobs may remain null during migration;
  - migration refuses to proceed while legacy queued/running jobs exist;
  - scheduler requests require a syntactically valid target worker;
  - scheduler idempotency includes `target_worker_id`;
  - internal worker-job rows deserialize the target identity.

  Name the Rust tests `scheduler_job_requires_target_worker`, `scheduler_hash_covers_target_worker`, and `worker_job_deserializes_target_worker`. Name Python structural tests `test_007_requires_target_for_active_jobs`, `test_007_preserves_global_active_unique_index`, and `test_007_rollback_requires_quiescence`.

  Use this exact hash test:

  ```rust
  #[test]
  fn scheduler_hash_covers_target_worker() {
      let first = SchedulerJobRequest {
          idempotency_key: Uuid::nil(),
          kind: SchedulerJobKind::Grade,
          franchise_id: 11,
          student_id: Some(42),
          target_worker_id: "dev-alice-laptop".into(),
      };
      let second = SchedulerJobRequest {
          idempotency_key: Uuid::nil(),
          kind: SchedulerJobKind::Grade,
          franchise_id: 11,
          student_id: Some(42),
          target_worker_id: "prod-windows-01".into(),
      };
      assert_ne!(first.request_hash(), second.request_hash());
  }
  ```

- [ ] Run the focused tests and confirm failure:

  ```powershell
  uv run pytest -q tests/test_migrations.py
  Set-Location api
  cargo test models --locked
  ```

- [ ] Add migration `007_target_worker_jobs.sql` with this behavior:

  ```sql
  DO $$
  BEGIN
      IF EXISTS (
          SELECT 1 FROM grade_scrape_jobs
          WHERE status IN ('queued', 'running')
      ) THEN
          RAISE EXCEPTION 'drain active jobs before adding target worker enforcement';
      END IF;
  END $$;

  ALTER TABLE grade_scrape_jobs
      ADD COLUMN target_worker_id TEXT;

  ALTER TABLE grade_scrape_jobs
      ADD CONSTRAINT ck_grade_scrape_jobs_active_target
      CHECK (
          status NOT IN ('queued', 'running')
          OR NULLIF(BTRIM(target_worker_id), '') IS NOT NULL
      );

  CREATE INDEX ix_grade_scrape_jobs_target_claim
      ON grade_scrape_jobs (target_worker_id, status, created_at);
  ```

  Preserve the existing active unique index on `(franchise_id, kind)`.

- [ ] Add a quiesced rollback SQL script that refuses to run while any job is `queued` or `running`, then drops only `ck_grade_scrape_jobs_active_target`. It must leave the nullable column and historical data intact. The old API may be restored only after this script and its legacy secret/Nginx configuration are deliberately restored.

- [ ] Extend the CI Postgres migration job to run fresh `001-007`, upgrade `001-006` to `007` with no active jobs, and prove applying `007` fails when a seeded legacy job is queued. Update `tests/test_ci_config.py` to require those three paths. This is behavioral Postgres coverage; `tests/test_migrations.py` remains a fast structural guard.

- [ ] Add `target_worker_id` to `SchedulerJobRequest` and internal `WorkerJob`. Validate it using the same conservative identifier rules used for configured identities. Do not expose target/lease details in `PublicJob`.

- [ ] Run focused tests:

  ```powershell
  uv run pytest -q tests/test_migrations.py
  uv run pytest -q tests/test_ci_config.py
  Set-Location api
  cargo fmt --all
  cargo test models --locked
  ```

- [ ] Stage Task 3 files, run GitNexus change detection, and commit:

  ```powershell
  git add api/migrations/007_target_worker_jobs.sql deploy/api/rollback/007_target_worker_jobs.sql api/src/models.rs .github/workflows/ci.yml tests/test_migrations.py tests/test_ci_config.py
  git commit -m "feat(api): target jobs to worker identities"
  ```

### Task 4: Enforce targeting in job creation and claims

**Files:**

- Modify: `api/src/queries.rs`
- Modify: `api/src/routes.rs`
- Modify: `api/Cargo.toml`
- Modify: `api/Cargo.lock`
- Create: `api/tests/targeted_jobs_postgres.rs`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_ci_config.py`
- Test: `api/src/queries.rs`

**Interfaces:**

- Consumes: Task 2 default worker identity and Task 3 target column/models.
- Produces: target-aware job create/claim queries, student-bound result idempotency, and the disposable Postgres harness used by Tasks 5 and 10.

```rust
pub async fn create_manual_pull_job(
    neon_db: &PgPool,
    payload: &ManualPullRequest,
    franchise_id: i32,
    role: Option<i32>,
    user: Option<&str>,
    target_worker_id: &str,
) -> Result<ManualPullResponse, ApiError>;

pub async fn create_scheduler_job(
    neon_db: &PgPool,
    scheduler_id: &str,
    payload: &SchedulerJobRequest,
) -> Result<WorkerJob, ApiError>;

pub async fn claim_next_job(
    neon_db: &PgPool,
    authenticated_worker_id: &str,
    lease_seconds: i64,
) -> Result<Option<WorkerClaimResponse>, ApiError>;

fn duplicate_result_identity_decision(
    existing_student_id: i64,
    existing_payload: &serde_json::Value,
    requested_student_id: i64,
    requested_payload: &serde_json::Value,
) -> Result<(), ApiError>;
```

`scheduler_job_by_key` remains keyed only by scheduler identity and idempotency UUID. `SchedulerJobRequest::request_hash()` covers `kind`, `franchise_id`, `student_id`, and `target_worker_id`.

- [ ] Run GitNexus context and upstream impact analysis for `create_manual_pull_job`, `create_scheduler_job`, `claim_next_job`, and their route callers. Include every execution flow reported for claiming and result submission.

- [ ] Add failing query tests proving:

  - scheduler and manual job inserts persist `target_worker_id`;
  - the scheduler idempotency lookup remains `(scheduler_identity, idempotency_key)`, while changing only `target_worker_id` changes the request hash and returns `409`;
  - claim SQL filters `target_worker_id = worker_id` before locking;
  - exhausted-lease cleanup in the claim CTE is also restricted to the authenticated target so one worker's claim request cannot mutate another target's job;
  - worker A cannot claim worker B's queued/expired job;
  - lease renewal, events, completion, failure, and result writes remain bound to both worker identity and lease token;
  - result insert still uses `(job_id, idempotency_key)` conflict handling.
  - duplicate result lookup compares both `crmstudentid` and canonical payload; reusing one UUID for a different student is always `409`, even when all other payload fields match.

  Name the new unit tests `scheduler_lookup_stays_identity_and_idempotency_only`, `scheduler_insert_persists_target_worker`, `manual_insert_persists_default_target`, `claim_filters_authenticated_target_before_locking`, and `claim_cleanup_cannot_mutate_another_target`.

- [ ] Create `api/tests/targeted_jobs_postgres.rs` using `#[sqlx::test(migrations = "./migrations")]`. Add real-Postgres tests named:

  - `targeted_worker_is_the_only_worker_that_can_claim`;
  - `active_job_uniqueness_survives_different_targets`;
  - `changed_target_with_reused_scheduler_key_is_conflict`;
  - `expired_lease_is_rejected_for_result_write`;
  - `duplicate_result_uuid_is_idempotent_but_changed_payload_conflicts`;
  - `duplicate_result_uuid_for_different_student_conflicts_even_with_same_content`.

  Seed only the minimal job/student state rows each test needs and call the public query functions. Do not mock SQLx for these lifecycle claims.

  The exact-target test body is:

  ```rust
  #[sqlx::test(migrations = "./migrations")]
  async fn targeted_worker_is_the_only_worker_that_can_claim(pool: PgPool) {
      let job_id: Uuid = sqlx::query_scalar(
          "INSERT INTO grade_scrape_jobs (franchise_id, kind, target_worker_id) VALUES (11, 'grade', 'dev-alice-laptop') RETURNING id"
      )
      .fetch_one(&pool)
      .await
      .unwrap();
      assert!(claim_next_job(&pool, "prod-windows-01", 300).await.unwrap().is_none());
      let claim = claim_next_job(&pool, "dev-alice-laptop", 300)
          .await
          .unwrap()
          .unwrap();
      assert_eq!(claim.job_id, job_id);
  }

  #[test]
  fn result_uuid_is_bound_to_student_and_payload() {
      let payload = serde_json::json!({"status": "synced"});
      assert!(matches!(
          duplicate_result_identity_decision(41, &payload, 42, &payload),
          Err(ApiError::Conflict(_))
      ));
  }
  ```

- [ ] Add SQLx's `migrate` feature to the existing dependency features so `#[sqlx::test(migrations = ...)]` can apply the real migration set. Run `cargo check --locked`; if Cargo reports the lock needs updating, run `cargo check` once to update only `api/Cargo.lock`, then rerun `cargo check --locked` and inspect the lock diff for expected SQLx migration support only.

- [ ] Run the focused tests and observe failure:

  ```powershell
  Set-Location api
  cargo test queries --locked
  ```

- [ ] Update all job inserts/selects to carry the target. The claim predicate must include:

  ```sql
  WHERE jobs.target_worker_id = $1
    AND (
      jobs.status = 'queued'
      OR (
        jobs.status = 'running'
        AND (jobs.lease_expires_at IS NULL OR jobs.lease_expires_at <= NOW())
        AND COALESCE(jobs.attempt_count, 0) < 3
      )
    )
  ```

  Apply the same `target_worker_id = $1` predicate to both the exhausted cleanup candidate and claim candidate CTEs. Keep row locking, lease-token rotation, `SKIP LOCKED`, and oldest-first ordering intact. Update the manual-pull route caller in this same task to pass `state.config.default_worker_id`; do not defer the caller update.

- [ ] Change the existing duplicate-result query to select `(crmstudentid, payload)`. Call `duplicate_result_identity_decision` before treating a retry as successful. Return `ApiError::Conflict` when either student ID or canonical payload differs; do not apply state for the second student.

- [ ] Add a Postgres 16 service and non-secret local `DATABASE_URL` to the CI Rust job so `#[sqlx::test]` runs on every change. Update `tests/test_ci_config.py` to require the service and targeted integration-test binary. For local execution, use an isolated disposable database:

  ```powershell
  docker run --detach --rm --name grades-api-test-postgres -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:16-alpine
  $env:DATABASE_URL = "postgres://postgres:postgres@127.0.0.1:55432/postgres"
  Set-Location api
  cargo test --test targeted_jobs_postgres --locked
  ```

  Expected: the five database tests pass. The credentials belong only to the disposable local container.

- [ ] Run formatting and focused tests:

  ```powershell
  Set-Location api
  cargo fmt --all
  cargo test queries --locked
  cargo test --test targeted_jobs_postgres --locked
  Set-Location ..
  docker stop grades-api-test-postgres
  ```

  Expected: query-contract tests pass and all existing lease/idempotency assertions remain green.

- [ ] Stage `api/src/queries.rs`, run GitNexus change detection, and commit:

  ```powershell
  git add api/src/queries.rs api/src/routes.rs api/Cargo.toml api/Cargo.lock api/tests/targeted_jobs_postgres.rs .github/workflows/ci.yml tests/test_ci_config.py
  git commit -m "feat(api): enforce targeted job claims"
  ```

### Task 5: Enforce scheduler scopes at the API boundary

**Files:**

- Modify: `api/src/error.rs`
- Modify: `api/src/models.rs`
- Modify: `api/src/queries.rs`
- Modify: `api/src/routes.rs`
- Modify: `api/tests/targeted_jobs_postgres.rs`
- Test: `api/src/routes.rs`

**Interfaces:**

- Consumes: Task 2 authenticated policies and Task 4 target-aware queries/Postgres harness.
- Produces: scheduler authorization helpers, `403`, and audited queued-only operator retarget/cancel APIs used by operations and Task 10.

```rust
fn authorize_scheduler_job(
    claims: &SchedulerAuthClaims,
    request: &SchedulerJobRequest,
) -> Result<(), ApiError>;

fn authorize_scheduler_reconcile(claims: &SchedulerAuthClaims) -> Result<(), ApiError>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperatorRetargetJobRequest {
    pub target_worker_id: String,
    pub reason: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperatorCancelJobRequest {
    pub reason: String,
}

pub async fn retarget_queued_job(
    neon_db: &PgPool,
    job_id: Uuid,
    target_worker_id: &str,
    operator_id: &str,
    reason: &str,
) -> Result<WorkerJob, ApiError>;

pub async fn cancel_queued_job(
    neon_db: &PgPool,
    job_id: Uuid,
    operator_id: &str,
    reason: &str,
) -> Result<WorkerJob, ApiError>;
```

`ApiError::Forbidden` maps to HTTP `403`, code `forbidden`, and a fixed safe message. These pure policy functions run before any SQL or CRM call and are directly unit-tested.

- [ ] Run GitNexus upstream impact analysis for the scheduler job, scheduler reconcile, manual pull, claim, context, and result handlers. Warn before proceeding if any route is `HIGH` or `CRITICAL` risk.

- [ ] Add failing route tests proving:

  - a scheduler may enqueue only an allowed franchise to an allowed target worker;
  - an unallowed franchise or target returns `403`, not `404` or `500`;
  - only `can_reconcile: true` scheduler identities may call global reconciliation;
  - manual pulls target `DEFAULT_WORKER_ID`;
  - worker claims use only the authenticated worker identity;
  - job context and result submission still route through the existing active worker/lease checks.

  Name the pure policy tests `scheduler_allows_scoped_franchise_and_target`, `scheduler_rejects_unscoped_franchise`, `scheduler_rejects_unscoped_target`, `scheduler_reconcile_requires_capability`, and `manual_pull_uses_default_worker`.

  Also add `operator_retarget_requires_configured_worker`, `operator_actions_require_nonblank_bounded_reason`, `operator_cannot_retarget_running_job`, and `operator_cancel_is_terminal_and_audited`.

  Use this exact scope test:

  ```rust
  #[test]
  fn scheduler_rejects_unscoped_target() {
      let claims = SchedulerAuthClaims {
          scheduler_id: "dev-alice".into(),
          key_id: "2026-07".into(),
          franchise_ids: Arc::new([11].into_iter().collect()),
          target_worker_ids: Arc::new(["dev-alice-laptop".into()].into_iter().collect()),
          can_reconcile: false,
      };
      let request: SchedulerJobRequest = serde_json::from_value(serde_json::json!({
          "idempotency_key": Uuid::nil(),
          "kind": "grade",
          "franchise_id": 11,
          "target_worker_id": "prod-windows-01"
      }))
      .unwrap();
      assert!(matches!(authorize_scheduler_job(&claims, &request), Err(ApiError::Forbidden)));
  }
  ```

- [ ] Add `ApiError::Forbidden` with the exact mapping above, then run tests to see the new failures:

  ```powershell
  docker run --detach --rm --name grades-api-test-postgres -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:16-alpine
  $env:DATABASE_URL = "postgres://postgres:postgres@127.0.0.1:55432/postgres"
  Set-Location api
  cargo test routes --locked
  cargo test --test targeted_jobs_postgres operator_ --locked
  ```

- [ ] Enforce scheduler policy before any database call. Do not accept a caller-supplied worker identity on worker routes; derive it exclusively from the authenticated key. Keep CRM canonical revalidation and result transaction code unchanged except for necessary target plumbing.

- [ ] Add operator-authenticated `POST /api/operator/jobs/:job_id/retarget` and `POST /api/operator/jobs/:job_id/cancel`. Accept only queued jobs, require a trimmed 1–256 character reason, validate retarget identity against the worker keyring, and update the job plus insert a `grade_scrape_job_events` audit row in one transaction. Retarget changes the existing row, so the global active uniqueness invariant remains in force. Cancellation sets `status='cancelled'`, `completed_at=NOW()`, and never deletes history.

- [ ] Run focused tests and formatting:

  ```powershell
  Set-Location api
  cargo fmt --all
  cargo test routes --locked
  cargo test --test targeted_jobs_postgres operator_ --locked
  Set-Location ..
  docker stop grades-api-test-postgres
  ```

  Expected: pure policy tests prove cross-franchise/cross-target/reconcile requests fail with `403`; permitted requests pass to the existing route code. Database-backed result behavior is proven in Tasks 4 and 10.

- [ ] Stage Task 5 files, run GitNexus change detection, and commit:

  ```powershell
  git add api/src/error.rs api/src/models.rs api/src/queries.rs api/src/routes.rs api/tests/targeted_jobs_postgres.rs
  git commit -m "feat(api): enforce scheduler job scopes"
  ```

### Task 6: Add target selection to Python scheduler and `uv` workflow

**Files:**

- Modify: `scraper/scheduler_client.py`
- Modify: `scripts/windows_pipeline.py`
- Modify: `tests/test_scheduler_client.py`
- Add or Modify: `tests/test_windows_pipeline.py`

**Interfaces:**

- Consumes: Task 3 scheduler JSON contract and Task 5 scheduler authorization.
- Produces: `enqueue_job(..., target_worker_id=...)`, `--target-worker`, and the local `uv` commands used by Task 13.

```python
def enqueue_job(
    *,
    franchise_id: int,
    kind: Literal["grade", "agenda"],
    idempotency_key: str,
    target_worker_id: str,
    student_id: int | None = None,
) -> dict[str, Any]:
    if type(franchise_id) is not int or franchise_id <= 0:
        raise ValueError("franchise_id must be positive")
    if kind not in {"grade", "agenda"}:
        raise ValueError("kind must be grade or agenda")
    if not target_worker_id or target_worker_id.strip() != target_worker_id:
        raise ValueError("target_worker_id must be nonblank and unpadded")
    payload: dict[str, Any] = {
        "franchise_id": franchise_id,
        "kind": kind,
        "idempotency_key": str(uuid.UUID(idempotency_key)),
        "target_worker_id": target_worker_id,
    }
    if student_id is not None:
        if type(student_id) is not int or student_id <= 0:
            raise ValueError("student_id must be positive")
        payload["student_id"] = student_id
    response = request_json("POST", "/api/scheduler/jobs", payload)
    if not isinstance(response, dict) or not response.get("id"):
        raise SchedulerApiError(502, "Scheduler API returned an invalid job")
    return response
```

CLI interface: `--target-worker WORKER_ID`, defaulting only from `WINDOWS_TARGET_WORKER_ID`. `SCHEDULER_ID` is removed from the Python client because the API derives scheduler identity solely from the bearer key.

- [ ] Run GitNexus upstream impact analysis for `enqueue_job`, `request_json`, `scheduler_id`, `run_pipeline`, and the CLI entry point before editing. Report callers and process risk.

- [ ] Add failing tests proving:

  - `enqueue_job` sends `target_worker_id`;
  - `--target-worker` overrides `WINDOWS_TARGET_WORKER_ID`;
  - enqueue fails locally with a clear message if neither is set;
  - target values are not inferred from a scheduler ID;
  - requests no longer require or send `SCHEDULER_ID`;
  - existing reconcile/drain modes retain their behavior.

  Name the tests `test_enqueue_sends_target_worker_id`, `test_cli_target_overrides_environment`, `test_enqueue_requires_explicit_target`, `test_scheduler_request_does_not_require_scheduler_id`, and `test_reconcile_and_drain_modes_are_unchanged`.

  Replace the existing scheduler bearer test with this exact contract:

  ```python
  def test_enqueue_sends_target_without_scheduler_id(monkeypatch):
      captured = {}

      def fake_request(method, path, payload=None):
          captured.update(method=method, path=path, payload=payload)
          return {"id": "00000000-0000-0000-0000-000000000042"}

      monkeypatch.delenv("SCHEDULER_ID", raising=False)
      monkeypatch.setattr(scheduler_client, "request_json", fake_request)
      scheduler_client.enqueue_job(
          franchise_id=19,
          kind="grade",
          idempotency_key="00000000-0000-0000-0000-000000000042",
          target_worker_id="dev-alice-laptop",
      )
      assert captured == {
          "method": "POST",
          "path": "/api/scheduler/jobs",
          "payload": {
              "franchise_id": 19,
              "kind": "grade",
              "idempotency_key": "00000000-0000-0000-0000-000000000042",
              "target_worker_id": "dev-alice-laptop",
          },
      }
  ```

- [ ] Run focused tests and confirm failure:

  ```powershell
  uv run pytest -q tests/test_scheduler_client.py tests/test_windows_pipeline.py
  ```

- [ ] Add the exact `target_worker_id` interface, remove the redundant `scheduler_id()` gate, and add `--target-worker` defaulting from `WINDOWS_TARGET_WORKER_ID`. The intended local command becomes:

  ```powershell
  $env:GRADE_API_BASE_URL = "https://grades-api-dev.tutoringclub.com"
  $env:SCHEDULER_API_KEY = "<raw scheduler key>"
  $env:WORKER_API_KEY = "<raw worker key>"
  $env:WORKER_ID = "dev-alice-laptop"
  uv run python scripts\windows_pipeline.py --franchise-id 11 --enqueue --target-worker dev-alice-laptop
  uv run python -m scraper.runner --once
  ```

- [ ] Run focused tests and lint:

  ```powershell
  uv run pytest -q tests/test_scheduler_client.py tests/test_windows_pipeline.py
  uv run ruff check scraper/scheduler_client.py scripts/windows_pipeline.py tests/test_scheduler_client.py tests/test_windows_pipeline.py
  ```

- [ ] Stage Task 6 files, run GitNexus change detection, and commit:

  ```powershell
  git add scraper/scheduler_client.py scripts/windows_pipeline.py tests/test_scheduler_client.py tests/test_windows_pipeline.py
  git commit -m "feat(worker): select target worker when enqueueing"
  ```

### Task 7: Make HTTPS clients work without mandatory mTLS

**Files:**

- Modify: `api_transport.py`
- Modify: `tests/test_api_transport.py`
- Modify: `tests/test_api_clients.py`

**Interfaces:**

- Consumes: existing Python API clients and TLS environment prefixes.
- Produces: HTTPS-required production transport with system/custom trust and optional client-certificate compatibility used by frontend, scheduler, and worker clients.

```python
@dataclass(frozen=True)
class HttpsTransportProfile:
    ca_file: str | None
    client_cert_file: str | None
    client_key_file: str | None
    timeout_seconds: float
    production: bool

    @classmethod
    def from_env(cls, prefix: str, *, default_timeout_seconds: float, fallback_prefix: str | None = None) -> "HttpsTransportProfile":
        def setting(suffix: str) -> str | None:
            value = os.getenv(f"{prefix}_{suffix}")
            if value is None and fallback_prefix:
                value = os.getenv(f"{fallback_prefix}_{suffix}")
            return value

        ca_file = setting("CA_FILE")
        client_cert_file = setting("CLIENT_CERT_FILE")
        client_key_file = setting("CLIENT_KEY_FILE")
        for value in (ca_file, client_cert_file, client_key_file):
            if value is not None and (not value or value.strip() != value):
                raise TransportConfigError(f"{prefix} TLS paths must be nonempty and unpadded")
        if bool(client_cert_file) != bool(client_key_file):
            raise TransportConfigError(f"{prefix} client certificate and key must be configured together")

        timeout_value = setting("TIMEOUT_SECONDS")
        try:
            timeout_seconds = default_timeout_seconds if timeout_value is None else float(timeout_value)
        except ValueError as exc:
            raise TransportConfigError(f"{prefix}_TIMEOUT_SECONDS must be a number") from exc
        if not math.isfinite(timeout_seconds) or not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise TransportConfigError(f"{prefix}_TIMEOUT_SECONDS is outside the allowed range")

        deployment_env = os.getenv("DEPLOYMENT_ENV", "development").strip().lower()
        if deployment_env not in {"development", "test", "production"}:
            raise TransportConfigError("DEPLOYMENT_ENV must be production, development, or test")
        return cls(ca_file, client_cert_file, client_key_file, timeout_seconds, deployment_env == "production")

    def ssl_context(self) -> ssl.SSLContext | None:
        if not any((self.ca_file, self.client_cert_file, self.client_key_file)):
            return None
        context = ssl.create_default_context(cafile=self.ca_file)
        if self.client_cert_file and self.client_key_file:
            context.load_cert_chain(self.client_cert_file, self.client_key_file)
        return context

    def open(self, request: urllib.request.Request) -> Any:
        scheme = urllib.parse.urlsplit(request.full_url).scheme.lower()
        if self.production and scheme != "https":
            raise TransportConfigError("API requests must use HTTPS in production")
        if any((self.ca_file, self.client_cert_file, self.client_key_file)) and scheme != "https":
            raise TransportConfigError("TLS settings cannot be used with a non-HTTPS URL")
        context = self.ssl_context()
        if context is None:
            return urllib.request.urlopen(request, timeout=self.timeout_seconds)
        return urllib.request.urlopen(request, timeout=self.timeout_seconds, context=context)
```

Production invariant: URL scheme is HTTPS. System trust is the default; a custom CA is optional; client certificate and key are optional but must appear together.

- [ ] Run GitNexus upstream impact analysis for `HttpsTransportProfile`, its environment loader, and its SSL-context builder. Report all API client callers.

- [ ] Change tests first so production mode requires `https://` but does not require a client certificate. Add coverage for:

  - system trust store with no TLS environment paths;
  - optional custom CA file;
  - optional client certificate/key accepted only as a complete pair for transition compatibility;
  - one-sided client certificate configuration rejected;
  - HTTP rejected in production;
  - bearer/HMAC headers remain application-level and are never placed in URLs.

  Name the new tests `test_production_https_uses_system_trust_without_client_cert`, `test_custom_ca_is_optional`, `test_optional_client_cert_requires_complete_pair`, `test_production_rejects_http`, and `test_api_keys_are_headers_not_query_parameters`.

  Use this exact production server-TLS test:

  ```python
  def test_production_https_uses_system_trust_without_client_cert(monkeypatch):
      captured = {}

      def fake_urlopen(request, *, timeout):
          captured.update(url=request.full_url, timeout=timeout)
          return "response"

      monkeypatch.setenv("DEPLOYMENT_ENV", "production")
      monkeypatch.setattr(api_transport.urllib.request, "urlopen", fake_urlopen)
      profile = api_transport.HttpsTransportProfile.from_env(
          "WORKER_API", default_timeout_seconds=30
      )
      response = profile.open(urllib.request.Request("https://grades-api-dev.tutoringclub.com/livez"))
      assert response == "response"
      assert captured == {
          "url": "https://grades-api-dev.tutoringclub.com/livez",
          "timeout": 30,
      }
      assert profile.ssl_context() is None
  ```

- [ ] Run focused tests and confirm the old mandatory-mTLS assertions fail:

  ```powershell
  uv run pytest -q tests/test_api_transport.py tests/test_api_clients.py
  ```

- [ ] Refactor transport profile validation to this matrix:

  | Production HTTPS | Custom CA | Client cert + key | Result |
  |---|---:|---:|---|
  | yes | no | no | allowed; system trust |
  | yes | yes | no | allowed; custom trust |
  | yes | no/yes | both | allowed; optional mTLS compatibility |
  | yes | no/yes | one only | reject |
  | no | any | any | reject in production |

- [ ] Run tests and lint:

  ```powershell
  uv run pytest -q tests/test_api_transport.py tests/test_api_clients.py
  uv run ruff check api_transport.py tests/test_api_transport.py tests/test_api_clients.py
  ```

- [ ] Stage Task 7 files, run GitNexus change detection, and commit:

  ```powershell
  git add api_transport.py tests/test_api_transport.py tests/test_api_clients.py
  git commit -m "feat(client): allow key auth over server TLS"
  ```

### Task 8: Replace Nginx mTLS authorization with safe TLS proxying

**Files:**

- Modify: `deploy/api/nginx/grades-api.conf`
- Modify: `deploy/api/api.env.example`
- Modify: `deploy/frontend/frontend.env.example`
- Modify: `deploy/windows/windows.env.example`
- Modify: `deploy/bin/validate-role-env`
- Modify: `tests/test_deploy_artifacts.py`

**Interfaces:**

- Consumes: Task 2 Axum application authorization and Task 7 server-TLS clients.
- Produces: key-pass-through Nginx configuration, role environment validation, one-megabyte body limit, source-IP rate limit, and safe logs used in deployment.

Nginx listens publicly only on `443`, proxies to Axum loopback, forwards the `Authorization` request header, and never interpolates it into logs. `deploy/bin/validate-role-env` is executed with a complete temporary role environment; it has no `--help` interface.

- [ ] Add failing deployment-artifact tests proving:

  - Nginx serves `grades-api.tutoringclub.com` and `grades-api-dev.tutoringclub.com` on TLS 1.2/1.3;
  - Nginx forwards `Authorization` but does not log it;
  - access logs contain no certificate identity, request body, portal credentials, or authorization value;
  - `ssl_verify_client on`, client-CA enforcement, and Nginx path/role maps are absent;
  - source-IP limiting returns `429` above `600 requests/minute` with a reasonable burst;
  - `client_max_body_size 1m` remains present so larger requests receive Nginx `413` before Axum;
  - unknown paths are proxied only to Axum and covered by the API `404` contract in Task 10; Nginx has no static-file fallback;
  - production role env validation requires keyring/digest settings and does not require client certificates.

  Name the tests `test_nginx_uses_server_tls_without_client_auth`, `test_nginx_forwards_but_never_logs_authorization`, `test_nginx_applies_source_ip_limit`, `test_nginx_rejects_bodies_over_one_megabyte`, `test_nginx_has_no_unknown_path_static_fallback`, and `test_role_env_requires_keyrings_not_client_certificates`.

  The body/unknown-path artifact guard is:

  ```python
  def test_nginx_body_limit_and_unknown_paths_are_fail_closed():
      nginx = _text("api/nginx/grades-api.conf")
      assert "client_max_body_size 1m;" in nginx
      assert "proxy_pass http://127.0.0.1:3000;" in nginx
      assert "try_files" not in nginx
      assert "root " not in nginx
      assert "alias " not in nginx
      assert "error_page 404" not in nginx
  ```

  This structural test is paired with the real Axum `404` integration test in Task 10 and the live Nginx `404`/`413` commands in Task 13.

- [ ] Run focused tests and confirm failure:

  ```powershell
  uv run pytest -q tests/test_deploy_artifacts.py
  ```

- [ ] Simplify Nginx to server TLS, safe headers, request-size/time limits, and proxying to Axum. Include:

  ```nginx
  limit_req_zone $binary_remote_addr zone=grade_api_per_ip:10m rate=600r/m;

  server {
      listen 443 ssl;
      server_name grades-api.tutoringclub.com grades-api-dev.tutoringclub.com;
      ssl_protocols TLSv1.2 TLSv1.3;
      client_max_body_size 1m;
      limit_req zone=grade_api_per_ip burst=100 nodelay;
      limit_req_status 429;
      proxy_set_header Authorization $http_authorization;
  }
  ```

  Keep the actual access log format free of `$http_authorization`, request bodies, and secrets. Axum—not Nginx—decides roles and scopes.

- [ ] Update examples to keyring JSON/digest configuration and remove mandatory client-certificate variables. Keep any optional compatibility variables clearly marked optional and unused in the target deployment.

- [ ] Validate artifacts:

  ```powershell
  uv run pytest -q tests/test_deploy_artifacts.py
  ```

  Expected: artifact tests pass. Run the validator with the complete valid-role fixture defined by the deployment tests in WSL or the Ubuntu deployment image during Task 12; the script intentionally has no `--help` mode.

- [ ] Stage Task 8 files, run GitNexus change detection, and commit:

  ```powershell
  git add deploy/api/nginx/grades-api.conf deploy/api/api.env.example deploy/frontend/frontend.env.example deploy/windows/windows.env.example deploy/bin/validate-role-env tests/test_deploy_artifacts.py
  git commit -m "feat(deploy): proxy API keys over server TLS"
  ```

### Task 9: Minimize and dispose of worker portal context

**Files:**

- Modify: `api/src/models.rs`
- Modify: `scraper/runner.py`
- Create: `scraper/diagnostics.py`
- Modify: `scraper/portals/blackbaud.py`
- Modify: `scraper/portals/aeries.py`
- Modify: `scraper/portals/classlink.py`
- Modify: `scraper/portals/google_classroom.py`
- Modify: `scraper/portals/gps.py`
- Modify: `scraper/portals/homeaccess.py`
- Modify: `scraper/portals/infinite_campus.py`
- Modify: `scraper/portals/microsoft_benjamin_franklin.py`
- Modify: `scraper/portals/parentvue.py`
- Modify: `scraper/portals/student_connection.py`
- Modify: `scraper/portals/utils.py`
- Modify: `scripts/check_python_boundaries.py`
- Modify: `tests/test_runner_worker_api.py`
- Create: `tests/test_worker_diagnostics.py`
- Test: `api/src/models.rs`

**Interfaces:**

- Consumes: existing CRM/state merge logic and Python portal adapters.
- Produces: the ten-field `WorkerStudent` DTO, best-effort credential disposal, and paired fail-closed browser tracing used by local and production workers.

```rust
#[derive(Clone, Serialize)]
pub struct WorkerStudent {
    pub crmstudentid: i64,
    pub firstname: String,
    pub portal1: Option<String>,
    pub p1username: Option<String>,
    pub p1password: Option<String>,
    pub portal2: Option<String>,
    pub p2username: Option<String>,
    pub p2password: Option<String>,
    pub portal: Option<String>,
    pub track_agenda: bool,
}
```

```python
def clear_worker_context_secrets(context: dict | None, students: list[dict]) -> None:
    sensitive = {"p1username", "p1password", "p2username", "p2password", "id", "password", "alt_id", "alt_password"}
    raw_rows = context.get("students", []) if isinstance(context, dict) else []
    for row in [*raw_rows, *students]:
        if isinstance(row, dict):
            for key in sensitive:
                row.pop(key, None)
            row.clear()
    if isinstance(raw_rows, list):
        raw_rows.clear()
    if isinstance(context, dict):
        context.clear()
    students.clear()

def sensitive_browser_artifacts_enabled() -> bool:
    production = os.getenv("DEPLOYMENT_ENV", "").strip().lower() == "production"
    opted_in = os.getenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", "").strip().lower() in {"1", "true", "yes"}
    return not production and opted_in

@asynccontextmanager
async def sensitive_tracing_context(page: Page):
    started = sensitive_browser_artifacts_enabled()
    if started:
        await page.context.tracing.start(screenshots=True, snapshots=True, sources=False)
    try:
        yield started
    finally:
        if started:
            await page.context.tracing.stop()
```

`students_from_worker_context` keeps its existing signature and maps only the ten Rust DTO fields into `db_id`, `student_name`, login/alternate-login credentials, portal, and `track_agenda`. Browser artifacts are disabled unconditionally when `DEPLOYMENT_ENV=production`. In non-production they require the explicit `WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS=1` opt-in. Production Python code may call Playwright `tracing.start` or `tracing.stop` only inside the owned context manager in `scraper/diagnostics.py`.

- [ ] Run GitNexus upstream impact analysis for `WorkerStudent`, `merge_worker_student`, `students_from_worker_context`, `run_worker_once`, and every portal method currently calling `tracing.start`. Report callers and risk before editing.

- [ ] Add failing Rust tests named `worker_student_serialization_contains_only_adapter_fields` and `merge_worker_student_drops_dashboard_and_historical_state`. Assert the serialized key set is exactly the ten fields in the interface; specifically exclude surname, grade, franchise, year bounds, `weeklydata`, status/error, password-good history, and `weekly_agenda`.

- [ ] Add failing Python tests named:

  - `test_context_mapping_ignores_noncontract_student_fields`;
  - `test_run_worker_once_clears_portal_secrets_in_finally` for success and exception paths;
  - `test_production_never_enables_sensitive_browser_artifacts`;
  - `test_nonproduction_artifacts_require_explicit_opt_in`;
  - `test_trace_context_never_starts_or_stops_in_production`;
  - `test_trace_context_pairs_start_and_stop_on_success_and_exception`;
  - `test_boundary_check_forbids_direct_tracing_outside_diagnostics`;
  - `test_portal_output_suppression_does_not_emit_context_credentials`.

  Use this exact pairing test with `pytest`, `SimpleNamespace`, and `sensitive_tracing_context` imported:

  ```python
  @pytest.mark.asyncio
  async def test_trace_context_pairs_start_and_stop_on_exception(monkeypatch):
      calls = []

      class Tracing:
          async def start(self, **options):
              calls.append(("start", options))

          async def stop(self):
              calls.append(("stop", None))

      page = SimpleNamespace(context=SimpleNamespace(tracing=Tracing()))
      monkeypatch.setenv("DEPLOYMENT_ENV", "development")
      monkeypatch.setenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", "1")
      with pytest.raises(RuntimeError):
          async with sensitive_tracing_context(page):
              raise RuntimeError("test")
      assert [name for name, _ in calls] == ["start", "stop"]
  ```

  Add the production variant with both environment values set to production/`1` and assert `calls == []`.

- [ ] Run focused tests and confirm failure:

  ```powershell
  Set-Location api
  cargo test worker_student --locked
  Set-Location ..
  uv run pytest -q tests/test_runner_worker_api.py tests/test_worker_diagnostics.py tests/test_safe_logging.py
  ```

- [ ] Reduce `WorkerStudent` and `merge_worker_student` to the exact adapter fields. Update `students_from_worker_context` to stop mapping `status` and `passwordgood`. Do not add names, grades, history, payload state, or result state back to worker context.

- [ ] Initialize `context` and `student_list` before the outer `try`, then call `clear_worker_context_secrets(context, student_list)` in its `finally`. It must remove credentials from both the raw API response rows and mapped worker rows, clear their collections, and do so even when login, scraping, result delivery, or lease renewal raises. This is best-effort reference disposal in Python, not a claim of guaranteed memory zeroization.

- [ ] Replace every direct trace start/stop and the existing `tracing_context` implementation with the owned `sensitive_tracing_context`. Remove orphaned stop calls in all files listed above. Extend the AST boundary checker to reject direct `.tracing.start(...)` or `.tracing.stop(...)` outside `scraper/diagnostics.py`. Do not add screenshot, video, trace-path, storage-state, or HTML-dump persistence.

- [ ] Run focused verification:

  ```powershell
  Set-Location api
  cargo fmt --all
  cargo test worker_student --locked
  Set-Location ..
  uv run pytest -q tests/test_runner_worker_api.py tests/test_worker_diagnostics.py tests/test_safe_logging.py
  uv run python scripts/check_python_boundaries.py
  ```

- [ ] Stage Task 9 files, run GitNexus change detection, and commit:

  ```powershell
  git add api/src/models.rs scraper/runner.py scraper/diagnostics.py scraper/portals/aeries.py scraper/portals/blackbaud.py scraper/portals/classlink.py scraper/portals/google_classroom.py scraper/portals/gps.py scraper/portals/homeaccess.py scraper/portals/infinite_campus.py scraper/portals/microsoft_benjamin_franklin.py scraper/portals/parentvue.py scraper/portals/student_connection.py scraper/portals/utils.py scripts/check_python_boundaries.py tests/test_runner_worker_api.py tests/test_worker_diagnostics.py
  git commit -m "fix(worker): minimize portal credential context"
  ```

### Task 10: Add database-backed end-to-end contract regressions

**Files:**

- Modify: `api/src/crm.rs`
- Modify: `api/src/state.rs`
- Modify: `api/src/routes.rs`
- Create: `api/tests/targeted_routes_postgres.rs`

**Interfaces:**

- Consumes: Tasks 1–9 authentication, targeting, operator actions, result validation, and the Postgres harness.
- Produces: injectable `CrmGateway` plus full Axum/Postgres lifecycle proofs without live CRM, Neon, or AWS dependencies.

```rust
#[async_trait::async_trait]
pub trait CrmGateway: Send + Sync {
    async fn ping(&self) -> Result<(), ApiError>;
    async fn login(&self, username: &str, password: &str) -> Result<CrmLogin, ApiError>;
    async fn list_students(
        &self,
        franchise_id: Option<i32>,
        student_id: Option<i64>,
    ) -> Result<Vec<CrmStudent>, ApiError>;
}

pub struct SqlServerCrmGateway {
    database_url: String,
}

impl SqlServerCrmGateway {
    pub fn new(database_url: String) -> Self {
        Self { database_url }
    }
}

impl AppState {
    pub fn with_dependencies(
        config: ApiConfig,
        neon_db: PgPool,
        crm: Arc<dyn CrmGateway>,
    ) -> Self {
        Self {
            config: Arc::new(config),
            neon_db,
            crm,
            rate_limiter: IdentityRateLimiter::new(std::time::Duration::from_secs(60)),
            #[cfg(test)]
            dashboard_replay_test_claims: None,
        }
    }
}
```

`AppState::new` constructs the existing production SQL Server gateway. Integration tests supply a deterministic in-memory `FakeCrmGateway` and the disposable SQLx Postgres pool.

The integration module defines these concrete helpers:

```rust
fn test_config() -> ApiConfig;
fn fake_crm(students: Vec<CrmStudent>) -> Arc<dyn CrmGateway>;
fn test_router(pool: PgPool, students: Vec<CrmStudent>) -> Router {
    create_router(AppState::with_dependencies(test_config(), pool, fake_crm(students)))
}
async fn row_count(pool: &PgPool, table: TestTable) -> i64;

enum TestTable {
    Results,
    StudentState,
}
```

`test_config` builds valid hashed keyrings for all four roles plus the default worker and existing non-network settings. `row_count` matches the enum to one of two static SQL strings; it never interpolates table text.

- [ ] Run GitNexus upstream impact analysis for `crm::ping`, `crm::login`, `crm::list_students`, `AppState::new`, and every route caller. Report the dependency-injection blast radius before editing.

- [ ] Add `api/tests/targeted_routes_postgres.rs` with `#[sqlx::test(migrations = "./migrations")]`, `tower::ServiceExt`, a fake CRM, and a real migrated Postgres database. Define tests with exact names:

  - `scoped_scheduler_to_targeted_worker_result_lifecycle`;
  - `production_worker_cannot_claim_local_target`;
  - `wrong_worker_and_wrong_lease_cannot_read_context_or_write_result`;
  - `expired_lease_cannot_write_result`;
  - `identical_result_retry_is_idempotent`;
  - `changed_result_retry_is_conflict`;
  - `reused_result_uuid_for_different_student_is_conflict`;
  - `malformed_result_body_writes_nothing`;
  - `oversized_result_field_writes_nothing`;
  - `unknown_route_returns_404`;
  - `operator_retarget_updates_one_queued_job_and_audits_reason`;
  - `operator_cancel_is_terminal_and_audited`;
  - `operator_cannot_retarget_or_cancel_running_job`;
  - `active_franchise_kind_uniqueness_is_global_across_targets`.

  Use this exact unknown-route no-write test with Axum `Body`, `Request`, `StatusCode`, and Tower `ServiceExt` imported:

  ```rust
  #[sqlx::test(migrations = "./migrations")]
  async fn unknown_route_returns_404(pool: PgPool) {
      let before_results = row_count(&pool, TestTable::Results).await;
      let before_state = row_count(&pool, TestTable::StudentState).await;
      let response = test_router(pool.clone(), Vec::new())
          .oneshot(
              Request::builder()
                  .uri("/not-a-real-route")
                  .body(Body::empty())
                  .unwrap(),
          )
          .await
          .unwrap();
      assert_eq!(response.status(), StatusCode::NOT_FOUND);
      assert_eq!(row_count(&pool, TestTable::Results).await, before_results);
      assert_eq!(row_count(&pool, TestTable::StudentState).await, before_state);
  }
  ```

- [ ] Run the new binary first and confirm compilation fails because `CrmGateway` and dependency construction do not exist:

  ```powershell
  docker run --detach --rm --name grades-api-test-postgres -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:16-alpine
  $env:DATABASE_URL = "postgres://postgres:postgres@127.0.0.1:55432/postgres"
  Set-Location api
  cargo test --test targeted_routes_postgres --locked
  ```

- [ ] Extract the exact CRM gateway interface without changing CRM queries or row mapping. Change routes to call `state.crm`. Keep the production constructor pointed at SQL Server and expose `with_dependencies` for deterministic composition.

- [ ] Add contract tests for the complete intended flow:

  1. scoped scheduler enqueues franchise 11 for `dev-alice-laptop`;
  2. `prod-windows-01` receives no job;
  3. `dev-alice-laptop` claims it and receives a lease-bound context;
  4. heartbeat/event/result calls use the same authenticated worker identity and lease token;
  5. duplicate identical result UUID is idempotent;
  6. duplicate changed result UUID is `409`;
  7. expired lease cannot write;
  8. a second active job for the same `(franchise_id, kind)` remains impossible;
  9. one result UUID reused for a different student returns `409`, even when other content is identical;
  10. malformed JSON and a result exceeding model string/node/depth limits return `400` and leave both result/state tables unchanged;
  11. an unknown route returns `404` without a database write;
  12. operator retarget/cancel affects only a queued job, emits an audit event containing operator ID/reason/old-new target, and rejects a running job;
  13. the canonical write uses `AppState.neon_db`; `scripts/check_python_boundaries.py` proves Python has no direct Neon/CRM bypass. The live production endpoint is verified only in Task 13.

- [ ] Run the focused API contract tests:

  ```powershell
  Set-Location api
  cargo fmt --all
  cargo test routes --locked
  cargo test queries --locked
  cargo test --test targeted_jobs_postgres --locked
  cargo test --test targeted_routes_postgres --locked
  Set-Location ..
  docker stop grades-api-test-postgres
  ```

  Expected: all thirteen contracts pass. The malformed/oversized tests query result and state row counts before and after and require equality. No test uses live CRM, Neon, or AWS credentials.

- [ ] Query the integration database after the happy path and assert `grade_scrape_jobs.worker_id`, `lease_expires_at`, result `worker_id`, and safe scheduler identity provide attribution. Never assert or log the raw lease token; use it only as a request secret.

- [ ] Stage the exact Task 10 files, run GitNexus change detection, and commit:

  ```powershell
  git add api/src/crm.rs api/src/state.rs api/src/routes.rs api/tests/targeted_routes_postgres.rs
  git commit -m "test: cover targeted local worker lifecycle"
  ```

### Task 11: Rewrite deployment and operator documentation

**Files:**

- Modify: `README.md`
- Modify: `scraper_internal_guide.md`
- Modify: `docs/runbooks/01-aws-network-controls.md`
- Modify: `docs/runbooks/02-private-pki.md`
- Modify: `docs/runbooks/04-ubuntu-two-instance-deployment.md`
- Modify: `docs/runbooks/05-secret-rotation-and-credential-backfill.md`
- Modify: `docs/runbooks/06-cloudflare-public-origin.md`
- Create: `docs/runbooks/07-api-first-local-worker-validation.md`

**Interfaces:**

- Consumes: the approved design and Tasks 1–10 runtime/configuration contracts.
- Produces: operator-ready AWS, deployment, key rotation, local validation, offline-target, and rollback runbooks used by Task 13.

The runbooks are the operator interface. They must contain copyable prerequisites, positive and negative acceptance matrices, quiesce/rollback steps, key rotation/revocation, and a private-inventory template whose account IDs, EIPs, and developer addresses are never committed.

- [ ] Update the architecture narrative to say exactly:

  - one `us-west-2` VPC;
  - frontend and API are separate Ubuntu instances;
  - API has a private address plus EIP because private-only developer access is unavailable;
  - port 443 is allowed only from frontend/Windows security groups and four recorded developer `/32` addresses;
  - `grades-api.tutoringclub.com` is private Route 53 for EC2 callers;
  - `grades-api-dev.tutoringclub.com` is public DNS-only to the EIP for allowlisted developers;
  - no public Cloudflare-proxied API record and no `0.0.0.0/0`/`::/0` API ingress;
  - SSM is the administrative path; no standing SSH/RDP rule;
  - Axum keyrings, not Nginx client certificates, enforce application identity.
  - CRM and Neon allow only the API EIP as the normal application source; workers and developers have no direct production database path after cutover.

- [ ] Mark the private-PKI runbook as optional/legacy compatibility rather than a rollout prerequisite. Do not delete its tooling in this change.

- [ ] Document key generation without printing raw keys after secure capture. Include a safe digest command such as:

  ```powershell
  $raw = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
  $digest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($raw))).ToLowerInvariant()
  ```

  The raw value goes only to the matching client secret; the digest, identity, expiry, and scope go to the API secret. Clear the shell variables after storing both sides.

- [ ] Document overlap rotation precisely: add a new `key_id`/digest under the existing identity, deploy/restart the API, update the one matching client with the new raw key, verify attributed traffic on the new key's identity, remove the old key record, and restart again. Revocation skips overlap and removes the compromised digest immediately.

- [ ] In the new validation runbook, include prerequisites, the exact `uv` commands from Task 6, expected API/worker responses, production Neon verification, cleanup, key revocation, and incident steps for a lost developer device or changed public IP.

- [ ] Add the quiesce/rollback sequence: disable schedulers and manual enqueue, stop new claims, drain/cancel all active jobs, capture database/secret/release state, restore the previous binary and Nginx configuration, restore the isolated legacy secret version only if absolutely necessary, run `deploy/api/rollback/007_target_worker_jobs.sql`, then repeat the network/auth matrix. Delete the legacy raw-token secret version after the rollback window.

- [ ] Document alarms and review controls for security-group ingress mutations, old targeted queued jobs, expired/repeated leases, service restarts, Nginx TLS/429 spikes, API 401/403/409/503 rates, certificate/key expiry, and worker abandonment. Security-group changes require an alert and human review.

- [ ] Document the offline-target procedure: queued-age alarm opens an incident; an authorized operator confirms the target is offline and the job is still `queued`; checks no other active `(franchise_id, kind)` row exists; chooses a configured replacement or cancellation; submits the bounded incident reason through the operator endpoint; verifies the job/event rows and new target claim (or terminal cancellation); and records operator ID, incident ID, old/new target, reason, and timestamps. Direct SQL retarget/cancel is break-glass only.

- [ ] State the deployment order explicitly: API first, local `uv` proof second, production Windows worker third, frontend last. The frontend receives no route or credential that can invoke worker endpoints, and the Windows/local workers receive no frontend session material.

- [ ] Check documentation for contradictions and forbidden old guidance:

  ```powershell
  rg -n "private subnet|no public IPv4|ssl_verify_client on|mandatory mTLS|0\.0\.0\.0/0|::/0" README.md scraper_internal_guide.md docs/runbooks
  ```

  Expected: any remaining matches are explicit prohibitions, historical notes, or optional compatibility notes—not deployment instructions.

- [ ] Stage Task 11 documentation, run GitNexus change detection, and commit:

  ```powershell
  git add README.md scraper_internal_guide.md docs/runbooks
  git commit -m "docs: document public API local worker rollout"
  ```

### Task 12: Run full local verification and build release artifacts

**Files:**

- Verify only; modify files only to fix a demonstrated failure, following impact analysis and TDD requirements.

**Interfaces:**

- Consumes: completed Tasks 1–11 and a disposable local Postgres 16 instance.
- Produces: passing Python/Rust/deployment checks and the release artifact consumed by Task 13; performs no AWS mutation.

Completion interface: every command below exits `0`, `git diff --check` is clean, GitNexus reports only planned flows, and any environment-dependent omission is recorded with the exact rerun command. This task produces artifacts but does not mutate AWS.

- [ ] Run Python formatting/lint and the complete test suite:

  ```powershell
  uv run ruff check .
  uv run pytest -q
  ```

  Expected: zero Ruff errors and all tests pass.

- [ ] Run Rust formatting, lint, tests, and release build:

  ```powershell
  docker run --detach --rm --name grades-api-test-postgres -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:16-alpine
  $env:DATABASE_URL = "postgres://postgres:postgres@127.0.0.1:55432/postgres"
  Set-Location api
  cargo fmt --all --check
  cargo clippy --locked --all-targets --all-features -- -D warnings
  cargo test --locked --all-targets
  cargo build --locked --release
  Set-Location ..
  docker stop grades-api-test-postgres
  ```

  Expected: all commands exit `0`; release binary is `api/target/release/api` (or `api.exe` on Windows). Stop the disposable container in a `finally` cleanup if any check fails.

- [ ] Validate deployment shell artifacts on Ubuntu/WSL:

  ```bash
  bash -n deploy/bin/validate-role-env
  bash -n deploy/bin/install-api
  bash -n deploy/bin/install-frontend
  ```

- [ ] Inspect the final code delta:

  ```powershell
  git status --short
  git diff --check
  git log --oneline --decorate -12
  ```

  Run `gitnexus_detect_changes({scope: "all"})` and confirm only the planned authentication, targeting, transport, deployment, and documentation flows changed.

- [ ] Do not claim completion if any check is skipped. Record the reason and an exact command for the operator to run in the correct environment.

### Task 13: Perform the manual AWS API-first rollout

**Files:**

- Follow: `docs/runbooks/01-aws-network-controls.md`
- Follow: `docs/runbooks/04-ubuntu-two-instance-deployment.md`
- Follow: `docs/runbooks/05-secret-rotation-and-credential-backfill.md`
- Follow: `docs/runbooks/07-api-first-local-worker-validation.md`

**Interfaces:**

- Consumes: Task 11 runbooks, Task 12 artifact/commit, and the private AWS/network/secret inventory.
- Produces: the live allowlisted API, local-worker proof, database-source enforcement, alarms, audit evidence, and exercised rollback.

Operator inputs: AWS account/region/VPC/subnets/routes, frontend/API/Windows security-group IDs, four named developer public `/32` values, API private IPv4/EIP, DNS zone IDs, certificate ARN/files, instance-profile ARNs, secret version IDs, release commit, and rollback owner. Store these in the private operations inventory, never in Git.

Acceptance outputs: a timestamped matrix for each allowed and denied network/auth case, one targeted production-data proof, safe database attribution, alarm state, rollback result, and final key versions.

- [ ] Record the AWS account ID, `us-west-2` region, VPC ID, frontend/API/Windows subnet IDs, route-table IDs, existing Windows private IP, and the four approved developer public `/32` addresses in the operator inventory. Do not commit account-specific IDs or IPs to the repository.

- [ ] Confirm the API subnet has an internet-gateway default route and that assigning an EIP is intentional. Allocate and record the API EIP.

- [ ] Create separate frontend, API, and Windows worker security groups. Configure API inbound TCP 443 only from:

  - frontend security group;
  - Windows worker security group;
  - developer home `/32`;
  - developer work `/32`;
  - developer 2 `/32`;
  - developer 3 `/32`.

  Add no API ingress from `0.0.0.0/0`, `::/0`, a broad corporate CIDR, or the frontend's public IP.

- [ ] Enable CloudTrail/EventBridge/CloudWatch notification and human review for any API security-group ingress authorization or revocation. Record the rule IDs and approved change ticket in the private inventory.

- [ ] Configure API egress only as operations require: CRM SQL Server endpoint/port, Neon Postgres endpoint/port, DNS, NTP, SSM/Secrets Manager/CloudWatch endpoints or HTTPS paths, and patch repositories. Because CRM and Neon are public services, verify DNS resolution and outbound TLS from the API before deployment.

- [ ] Add the API EIP as an approved source in both the CRM SQL Server firewall/allowlist and the Neon project IP Allow list. After the API proof succeeds, remove obsolete Windows-worker and developer public IPs from those database allowlists. Preserve only separately approved break-glass administration sources, each time-bound and audited.

- [ ] Verify source enforcement in both directions: the API instance connects successfully to CRM and Neon, while the Windows worker and all four developer networks fail direct TCP/database authentication to the CRM and Neon endpoints. Record provider allowlist screenshots/IDs and negative `Test-NetConnection`/database-client results without credentials in the private inventory.

- [ ] Create DNS records:

  - private Route 53 `grades-api.tutoringclub.com` → API private IPv4;
  - public DNS-only `grades-api-dev.tutoringclub.com` → API EIP.

  Do not enable the Cloudflare proxy for the developer API name; AWS security groups must see the developer's real source IP.

- [ ] Obtain/install a publicly trusted server certificate covering both names, or separate certificates as documented. Prove certificate renewal without opening broad inbound HTTP; prefer DNS-01 validation.

- [ ] Create role-separated Secrets Manager paths and instance profiles. Store API keyring digests/scopes/expiries on the API; store each raw key only with its matching frontend, Windows host, monitoring system, or developer secret manager. Verify cross-role reads fail.

- [ ] Launch the Ubuntu 24.04 API instance with no application database/session secrets outside its API role. Attach the EIP and API security group. Use SSM Session Manager, install the verified release, run migrations only after active legacy jobs are drained, and start Axum behind Nginx.

- [ ] Configure alarms before application cutover: oldest queued targeted-job age, running lease age/expiry, repeated lease abandonment, API/Nginx restart loops, Nginx TLS/429, API 401/403/409/503, certificate/key expiry, clock offset, disk/CPU/memory, and Secrets Manager retrieval failure without secret values.

- [ ] Exercise the audited offline-target procedure with a disposable queued job. Trigger/observe the queued-age alarm, verify active uniqueness, retarget it through the operator API to another configured test worker, verify the audit event and exact new claimant, then create a second disposable queued job and cancel it through the operator API. Confirm running jobs reject both actions and record the incident/operator evidence.

- [ ] Execute the network acceptance matrix:

  | Source | Name/address | Expected TCP/TLS result |
  |---|---|---|
  | Frontend EC2 | private API name | connect; unauthorized request gets `401` |
  | Windows EC2 | private API name | connect; valid worker key reaches worker route |
  | Developer home address | public dev API name | connect; invalid key gets `401` |
  | Developer work address | public dev API name | connect; invalid key gets `401` |
  | Developer 2 address | public dev API name | connect; invalid key gets `401` |
  | Developer 3 address | public dev API name | connect; invalid key gets `401` |
  | Unrelated VPC instance | API private address | TCP blocked |
  | Non-allowlisted internet host | API EIP | TCP blocked |

  From every applicable source, also verify public/private TCP 80, TCP 22, TCP 3389, and Axum loopback TCP 3000 are blocked. Verify the frontend cannot call worker routes with its HMAC/key material, workers have no frontend session material, and neither worker security group receives application ingress.

- [ ] From one allowlisted developer address, record the live Nginx edge contracts:

  ```powershell
  curl.exe --silent --output NUL --write-out "%{http_code}" https://grades-api-dev.tutoringclub.com/not-a-real-route
  $oversized = "x" * 1048577
  try { Invoke-WebRequest -Method Post -Uri "https://grades-api-dev.tutoringclub.com/api/worker/jobs/claim" -ContentType "application/json" -Body $oversized } catch { $_.Exception.Response.StatusCode.value__ }
  Remove-Variable oversized
  ```

  Expected: unknown route `404`; 1,048,577-byte body `413`. Confirm neither request creates a job/result/state row and no request body or authorization value appears in logs.

- [ ] Run one deliberately narrow local proof with an authorized test student/franchise. Enqueue it specifically to the developer laptop, run one `uv` worker cycle, and verify:

  - the production Windows worker did not claim it;
  - safe logs contain job and worker identity only, while lease expiry/token attribution is verified in database records;
  - the result appears once in production Neon;
  - the canonical CRM identifiers match;
  - retrying the same result UUID does not duplicate data;
  - no portal credential, bearer key, or payload was logged.

  Repeat the worker API negatives with the production worker key against the local-targeted job and with a random/wrong lease token. Both must fail. Verify worker/scheduler/job/lease-expiry attribution in database records; logs may contain safe job, worker, and scheduler identifiers but never the raw lease token.

- [ ] Revoke the temporary local test keys or reduce them to the intended steady-state scopes. Enable the production Windows schedule only after the local proof and network matrix pass. Deploy/connect the frontend last, then rerun its positive API check and its worker-route negative check.

- [ ] Exercise rollback while traffic is quiesced: stop enqueue/claim traffic, prove no active jobs remain, test the constraint rollback script against a disposable database, verify the retained previous release/secret/Nginx versions are usable, then return to the new release and rerun one health/auth check. Do not expose the legacy raw-token secret path during normal operation.

- [ ] Record the final validation timestamp, operator, release commit, secret versions, certificate expiry, EIP, security-group rule IDs, DNS records, and rollback result in the private operations inventory.

## Completion criteria

The rollout is complete only when all local checks pass, the API is reachable from the two trusted EC2 security groups and all four current developer `/32` addresses, all negative network tests fail closed, CRM and Neon accept the API EIP but reject workers/developers, local `uv` jobs can target only their authorized worker/franchises, UUID reuse cannot cross students, production Neon receives one canonical result, trace/context handling is fail-closed, audited retarget/cancel works for queued jobs only, and key revocation/rollback has been exercised.
