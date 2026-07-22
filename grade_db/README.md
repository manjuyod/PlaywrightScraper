# grade-db

`grade-db.exe` is the local Windows database boundary for the Playwright runners. It reads runnable students whose CRM `IsTrail` value is `Active` and whose primary portal credentials are complete through a read-only SQL Server account, and it owns leased job/result/state transactions in Neon. JSON is accepted on stdin and emitted on stdout; sanitized diagnostic codes go to stderr.

## Build

```powershell
cargo fmt --manifest-path grade_db/Cargo.toml -- --check
cargo clippy --manifest-path grade_db/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path grade_db/Cargo.toml
cargo build --manifest-path grade_db/Cargo.toml --target x86_64-pc-windows-msvc --release
```

Do not commit `target/` or compiled executables.

## Commands

```text
grade-db.exe job start
grade-db.exe job heartbeat
grade-db.exe result post
grade-db.exe job complete
grade-db.exe job fail
grade-db.exe doctor
```

All job/result request bodies are JSON on stdin. `doctor` performs read-only CRM, Neon, configuration, and schema checks. There is deliberately no SQL, server, scheduler, auth-key, or migration command.

## Environment

- Neon: `GRADES_NEON_URL`, or `GRADES_NEON_HOST`, `GRADES_NEON_DB`/`GRADES_NEON_DATABASE`, `GRADES_NEON_USER`, `GRADES_NEON_PASSWORD`, and optional `GRADES_NEON_PORT`.
- CRM: `CRMSrvAddress`, `CRMSrvDb`/`CRMSrvDbQA`, `CRMSrvUs`, `CRMSrvPs`, and `CRM_TRUST_SERVER_CERTIFICATE`.
- Runner: optional `GRADE_RUNNER_ID` and `GRADE_JOB_LEASE_SECONDS` (default 600).

Python additionally supports `GRADE_DB_CLI_PATH`. Otherwise it checks target-specific and normal Cargo release/debug locations.

## SQL rollout

Agents do not execute these files:

- `sql/000_inspect_boundary.sql`: read-only schema/constraint/count inspection for human review.
- `sql/001_runner_boundary.sql`: idempotent forward migration for fresh or partially applied defunct schemas; no legacy student backfill and no data/table drops.
- `sql/operations/`: human-run updates for portal2, agenda, and GPS configuration on rows that already exist.

After human review and migration, run `doctor`, then pilot a single student, a single franchise, and an agenda job.
