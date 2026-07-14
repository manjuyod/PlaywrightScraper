from __future__ import annotations

from pathlib import Path


MIGRATIONS = Path(__file__).resolve().parents[1] / "api" / "migrations"


def test_dashboard_replay_nonce_migration_is_identity_scoped_and_expiry_indexed():
    sql = (MIGRATIONS / "004_dashboard_replay_nonces.sql").read_text(
        encoding="utf-8"
    ).lower()

    assert "identity_hash bytea not null" in sql
    assert "nonce uuid not null" in sql
    assert "expires_at timestamptz not null" in sql
    assert "primary key (identity_hash, nonce)" in sql
    assert "octet_length(identity_hash) = 32" in sql
    assert "expires_at" in sql and "create index" in sql
    assert "scheduler_identity text" in sql
    assert "scheduler_idempotency_key uuid" in sql
    assert "scheduler_request_hash bytea" in sql
    assert "uq_grade_scrape_jobs_scheduler_idempotency" in sql


def test_alternate_credentials_migration_adds_a_complete_versioned_envelope():
    sql = (MIGRATIONS / "005_alternate_credentials_encryption.sql").read_text(
        encoding="utf-8"
    ).lower()

    assert "alternate_credentials_version smallint" in sql
    assert "alternate_credentials_key_id text" in sql
    assert "alternate_credentials_nonce bytea" in sql
    assert "alternate_credentials_ciphertext bytea" in sql
    assert "octet_length(alternate_credentials_nonce) = 12" in sql
    assert "num_nonnulls" in sql


def test_plaintext_enforcement_migration_keeps_legacy_columns_constrained_null():
    sql = (MIGRATIONS / "006_enforce_no_plaintext_alternate_credentials.sql").read_text(
        encoding="utf-8"
    ).lower()

    assert "set p2username = null" in sql
    assert "p2password = null" in sql
    assert "check (p2username is null and p2password is null)" in sql


def test_007_requires_target_for_active_jobs():
    sql = (MIGRATIONS / "007_target_worker_jobs.sql").read_text(encoding="utf-8")
    lower = sql.lower()
    assert "target_worker_id text" in lower
    assert "status in ('queued', 'running')" in lower
    assert "raise exception" in lower
    assert "check (" in lower
    assert "status not in ('queued', 'running')" in lower
    assert "target_worker_id is not null" in lower
    assert "btrim(target_worker_id) <> ''" in lower


def test_007_refuses_legacy_active_jobs_before_schema_change():
    sql = (MIGRATIONS / "007_target_worker_jobs.sql").read_text(encoding="utf-8").lower()
    assert sql.index("raise exception") < sql.index("add column target_worker_id text")


def test_007_preserves_global_active_unique_index():
    sql = (MIGRATIONS / "007_target_worker_jobs.sql").read_text(encoding="utf-8")
    assert "drop index" not in sql.lower()
    assert "uq_grade_scrape_jobs_active" not in sql.lower()
    assert "alter table grade_scrape_jobs drop constraint" not in sql.lower()


def test_007_rollback_requires_quiescence():
    rollback = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "api"
        / "rollback"
        / "007_target_worker_jobs.sql"
    ).read_text(encoding="utf-8")
    lower = rollback.lower()
    assert "where status in ('queued', 'running')" in lower
    assert "raise exception" in lower
    assert "drop constraint if exists ck_grade_scrape_jobs_active_target_worker_id" in lower
    assert "drop column if exists target_worker_id" in lower
