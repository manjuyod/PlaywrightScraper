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
