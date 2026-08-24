from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _docker(*args: str, input_text: str | None = None, check: bool = True):
    return subprocess.run(
        ["docker", *args],
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=check,
    )


def _psql(container: str, sql: str) -> subprocess.CompletedProcess[str]:
    return _docker(
        "exec",
        "-i",
        container,
        "psql",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        input_text=sql,
    )


@pytest.mark.integration
def test_split_state_migration_preserves_legacy_failures_and_timestamps() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the PostgreSQL migration contract")
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is not running")

    container = f"playwrightscraper-migration-{uuid.uuid4().hex[:12]}"
    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=migration_test",
        "postgres:17-alpine",
    )
    try:
        for _attempt in range(60):
            if (
                _docker(
                    "exec",
                    container,
                    "pg_isready",
                    "-U",
                    "postgres",
                    check=False,
                ).returncode
                == 0
            ):
                break
            time.sleep(0.25)
        else:
            pytest.fail("PostgreSQL migration container did not become ready")

        boundary_sql = (ROOT / "grade_db/sql/001_runner_boundary.sql").read_text(
            encoding="utf-8"
        )
        split_sql = (
            ROOT / "grade_db/sql/003_split_student_scrape_state.sql"
        ).read_text(encoding="utf-8")
        cleanup_sql = (
            ROOT / "grade_db/sql/004_drop_shared_scrape_state.sql"
        ).read_text(encoding="utf-8")

        _psql(container, boundary_sql)
        _psql(
            container,
            """
INSERT INTO students_grades_20262027
    (crmstudentid, weeklydata, weekly_agenda, status, error_msg, updated_at)
VALUES
    (
        101,
        '{"2026-08-17":{"Math":90}}'::jsonb,
        '{}'::jsonb,
        'error',
        'bad_login',
        '2026-08-20T12:00:00Z'
    ),
    (
        102,
        '{"2026-08-17":{"English":88}}'::jsonb,
        '{"agenda1":{"portal":"canvas","weeks":{}},"agenda2":{"portal":"parentvue","weeks":{"2026-08-17":{}}}}'::jsonb,
        'error',
        'agenda2_parentvue_failed',
        '2026-08-20T13:00:00Z'
    );
""",
        )
        _psql(container, split_sql)

        migrated = _psql(
            container,
            """
SELECT grade_status,
       grade_updated_at = '2026-08-20T12:00:00Z'::timestamptz
FROM students_grades_20262027
WHERE crmstudentid = 101;
SELECT secondary_agenda_status,
       secondary_agenda_updated_at = '2026-08-20T13:00:00Z'::timestamptz
FROM students_grades_20262027
WHERE crmstudentid = 102;
""",
        )
        assert migrated.stdout.splitlines() == ["bad_login|t", "scrape_failed|t"]

        _psql(container, cleanup_sql)
        _psql(container, split_sql)
        _psql(container, cleanup_sql)
    finally:
        _docker("rm", "-f", container, check=False)
