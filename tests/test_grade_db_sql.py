from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL_ROOT = ROOT / "grade_db" / "sql"


def _sql(path: str) -> str:
    return (SQL_ROOT / path).read_text(encoding="utf-8").lower()


def test_schema_inspection_is_read_only_and_does_not_select_secrets() -> None:
    sql = _sql("000_inspect_boundary.sql")

    assert "information_schema.columns" in sql
    assert "pg_constraint" in sql
    assert "count(*)" in sql
    assert not re.search(
        r"\b(insert|update|delete|alter|drop|create|truncate|grant|revoke)\b",
        sql,
    )
    for secret_column in ("p1password", "p2password", "auth_answers", "weeklydata"):
        assert not re.search(rf"select\s+[^;]*\b{secret_column}\b", sql)


def test_boundary_migration_creates_only_runner_owned_tables_without_backfill() -> None:
    sql = _sql("001_runner_boundary.sql")

    for table in (
        "students_grades_20262027",
        "grade_scrape_jobs",
        "grade_scrape_job_events",
        "grade_scrape_results",
    ):
        assert f"create table if not exists {table}" in sql

    for api_only_table in ("dashboard_replay_nonces", "api_keys"):
        assert f"create table if not exists {api_only_table}" not in sql

    assert "from student" not in sql
    assert "drop table" not in sql
    assert "truncate" not in sql
    assert "crmstudentid bigint" in sql
    assert "auth_type text" in sql
    assert "auth_answers jsonb" in sql
    assert "lease_token uuid" in sql
    assert "lease_expires_at timestamptz" in sql
    assert "idempotency_key uuid" in sql
    assert "applied boolean" in sql
    for crm_owned_column in ("portal2", "p2username", "p2password"):
        assert crm_owned_column not in sql


def test_secondary_portal_drop_migration_removes_only_crm_owned_columns() -> None:
    sql = _sql("002_drop_neon_secondary_portal.sql")

    assert "begin;" in sql
    assert "commit;" in sql
    assert "alter table students_grades_20262027" in sql
    assert "drop constraint if exists ck_students_grades_no_plaintext_alternate_credentials" in sql
    for crm_owned_column in ("portal2", "p2username", "p2password"):
        assert f"drop column if exists {crm_owned_column}" in sql
    for retained_column in ("portal", "track_agenda", "auth_type", "auth_answers"):
        assert not re.search(
            rf"drop column if exists {retained_column}\s*[,;]",
            sql,
        )


def test_scrape_state_migration_splits_and_backfills_channels() -> None:
    sql = _sql("003_split_student_scrape_state.sql")

    for column in (
        "primary_agenda",
        "secondary_agenda",
        "grade_status",
        "primary_agenda_status",
        "secondary_agenda_status",
        "grade_updated_at",
        "primary_agenda_updated_at",
        "secondary_agenda_updated_at",
        "failure_code",
    ):
        assert f"add column if not exists {column}" in sql
    assert "weekly_agenda->'agenda1'" in sql
    assert "weekly_agenda->'agenda2'" in sql
    assert "drop column" not in sql


def test_shared_scrape_state_cleanup_removes_only_replaced_columns() -> None:
    sql = _sql("004_drop_shared_scrape_state.sql")

    for retired in (
        "weekly_agenda",
        "status",
        "error_msg",
        "updated_at",
    ):
        assert f"drop column if exists {retired}" in sql
    for retained in (
        "weeklydata",
        "primary_agenda",
        "secondary_agenda",
        "grade_status",
        "primary_agenda_status",
        "secondary_agenda_status",
        "failure_code",
    ):
        assert f"drop column if exists {retained}" not in sql


def test_boundary_migration_relaxes_only_known_defunct_constraints() -> None:
    sql = _sql("001_runner_boundary.sql")

    assert "drop constraint if exists ck_grade_scrape_jobs_active_target_worker_id" in sql
    assert "ck_students_grades_no_plaintext_alternate_credentials" not in sql
    assert "drop index if exists uq_grade_scrape_jobs_active" in sql
    assert "drop column" not in sql
    assert "retired_legacy_job" in sql
    assert "runner_id is null" in sql


def test_boundary_migration_keeps_defunct_event_message_compatible() -> None:
    sql = _sql("001_runner_boundary.sql")

    assert "message text" in sql
    assert "add column if not exists message text" in sql
    assert "set message = coalesce" in sql


def test_human_runner_configuration_sql_only_updates_existing_state() -> None:
    set_sql = _sql("operations/set_runner_config.sql")
    clear_sql = _sql("operations/clear_runner_config.sql")

    for sql in (set_sql, clear_sql):
        assert "update students_grades_20262027" in sql
        assert "where crmstudentid" in sql
        assert "insert" not in sql
        assert "delete" not in sql
        assert "begin;" in sql
        assert "commit;" in sql
        for crm_owned_column in ("portal2", "p2username", "p2password"):
            assert crm_owned_column not in sql


def test_rust_boundary_has_no_http_or_api_security_stack() -> None:
    cargo = (ROOT / "grade_db" / "Cargo.toml").read_text(encoding="utf-8").lower()
    main = (ROOT / "grade_db" / "src" / "main.rs").read_text(
        encoding="utf-8"
    ).lower()

    for dependency in ("axum", "tower", "reqwest", "jsonwebtoken", "hmac"):
        assert dependency not in cargo
    for listener in ("tcplistener", "axum::serve", "0.0.0.0"):
        assert listener not in main
