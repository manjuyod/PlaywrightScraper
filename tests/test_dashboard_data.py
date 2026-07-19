from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from ui import dashboard_data


def _crm_student(student_id: int, *, franchise_id: int = 57) -> dict:
    return {
        "crmstudentid": student_id,
        "franchiseid": franchise_id,
        "firstname": "Ada",
        "lastname": f"Student {student_id}",
        "grade": 10,
        "portal_url": "https://grades.example.test/login",
    }


def test_crm_query_checks_credentials_without_selecting_them() -> None:
    sql = dashboard_data.CRM_STUDENTS_SQL.lower()
    projection = sql.split("from dbo.tblstudents", 1)[0]

    assert "gradeportalurl" in projection
    assert "gradeportaluser" not in projection
    assert "gradeportalpwd" not in projection
    assert "nullif(ltrim(rtrim(s.gradeportalurl)), '') is not null" in sql
    assert "nullif(ltrim(rtrim(s.gradeportaluser)), '') is not null" in sql
    assert "nullif(ltrim(rtrim(s.gradeportalpwd)), '') is not null" in sql


def test_neon_student_query_never_selects_runner_secrets() -> None:
    sql = dashboard_data.NEON_STATES_SQL.lower()

    assert "weeklydata" in sql
    assert "weekly_agenda" in sql
    for forbidden in (
        "p1username",
        "p1password",
        "p2username",
        "p2password",
        "auth_answers",
        "portal2",
    ):
        assert forbidden not in sql


def test_merge_uses_crmstudentid_and_keeps_missing_state_displayable() -> None:
    crm_rows = [_crm_student(101), _crm_student(102)]
    state_rows = [
        {
            "crmstudentid": 101,
            "weeklydata": {"2026-07-13": {"English": 91.5}},
            "weekly_agenda": {"2026-07-15": [["English", "Essay"]]},
            "status": "synced",
            "passwordgood": True,
            "error_msg": None,
            "updated_at": datetime(2026, 7, 14, 12, 30, tzinfo=UTC),
        },
        {
            "crmstudentid": 1,
            "weeklydata": {"2026-07-13": {"Wrong legacy match": 0}},
            "status": "error",
        },
    ]

    students = dashboard_data.merge_student_rows(crm_rows, state_rows)

    assert [student.crmstudentid for student in students] == [101, 102]
    assert students[0].status == "synced"
    assert students[0].grades == {"2026-07-13": {"English": 91.5}}
    assert students[1].status == "never"
    assert students[1].grades == {}
    assert not hasattr(students[0], "p1password")
    assert not hasattr(students[0], "portal2")


def test_student_report_compares_courses_by_name_in_date_order() -> None:
    student = dashboard_data.merge_student_rows(
        [_crm_student(101)],
        [
            {
                "crmstudentid": 101,
                "weeklydata": {
                    "2026-07-13": {"Math": 88.0, "English": 92.0},
                    "2026-07-06": {"English": 90.0, "Math": 90.0},
                    "2026-06-29": {},
                },
            }
        ],
    )[0]

    report = dashboard_data.build_student_report(student)
    by_course = {grade.course: grade for grade in report.grades_snapshot}

    assert by_course["English"].change == "+"
    assert by_course["Math"].change == "-"
    assert report.standing == "Good"


def test_job_shape_exposes_only_public_counts_and_sanitized_error() -> None:
    job = dashboard_data.shape_job_row(
        {
            "id": "7a74c220-ae45-4db7-9d5d-328db45530c9",
            "kind": "grade",
            "status": "failed",
            "franchise_id": 57,
            "student_id": None,
            "runner_id": "private-runner-name",
            "lease_token": "private-lease",
            "progress": {
                "total": 40,
                "attempted": 4,
                "success": 1,
                "errors": 3,
                "username": "must-not-leak",
            },
            "error_msg": "unsafe error with details",
            "started_at": datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            "updated_at": datetime(2026, 7, 14, 12, 5, tzinfo=UTC),
            "completed_at": datetime(2026, 7, 14, 12, 5, tzinfo=UTC),
        }
    )

    assert set(job) == {
        "id",
        "kind",
        "status",
        "franchiseId",
        "studentId",
        "total",
        "attempted",
        "success",
        "errors",
        "startedAt",
        "updatedAt",
        "completedAt",
        "errorCode",
    }
    assert job["errorCode"] == "runner_failed"
    assert "private-runner-name" not in str(job)
    assert "private-lease" not in str(job)


def test_franchise_summary_counts_only_merged_students() -> None:
    students = dashboard_data.merge_student_rows(
        [_crm_student(101), _crm_student(102), _crm_student(201, franchise_id=99)],
        [
            {"crmstudentid": 101, "status": "synced", "passwordgood": True},
            {
                "crmstudentid": 102,
                "status": "error",
                "passwordgood": False,
                "error_msg": "bad_login",
            },
        ],
    )

    summaries = dashboard_data.summarize_franchises(students)

    assert [summary["id"] for summary in summaries] == [57, 99]
    assert summaries[0]["total"] == 2
    assert summaries[0]["synced"] == 1
    assert summaries[0]["errorCount"] == 1
    assert summaries[0]["badLogins"] == 1


class _FakeCursor:
    description = [
        ("crmstudentid",),
        ("franchiseid",),
        ("firstname",),
        ("lastname",),
        ("grade",),
        ("portal_url",),
    ]

    def __init__(self) -> None:
        self.executed: tuple[str, tuple[Any, ...]] | None = None
        self.closed = False

    def execute(self, sql: str, *params: Any) -> None:
        self.executed = (sql, params)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [
            (101, 57, " Ada ", " Lovelace ", 10, "https://grades.example.test/login")
        ]

    def close(self) -> None:
        self.closed = True


class _FakeCrmConnection:
    def __init__(self) -> None:
        self.cursor_value = _FakeCursor()
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_value

    def close(self) -> None:
        self.closed = True


def test_crm_reader_uses_read_intent_and_parameterized_scope(monkeypatch) -> None:
    monkeypatch.setenv("CRMSrvAddress", "crm.example.test")
    monkeypatch.setenv("CRMSrvDb", "CRM")
    monkeypatch.setenv("CRMSrvUs", "reader")
    monkeypatch.setenv("CRMSrvPs", "secret-value")
    connection = _FakeCrmConnection()
    captured: dict[str, Any] = {}

    def connect(connection_string: str, *, timeout: int) -> _FakeCrmConnection:
        captured["connection_string"] = connection_string
        captured["timeout"] = timeout
        return connection

    rows = dashboard_data.read_crm_students(
        franchise_id=57,
        student_id=None,
        connect=connect,
    )

    assert len(rows) == 1
    assert rows[0]["crmstudentid"] == 101
    assert rows[0]["franchiseid"] == 57
    assert rows[0]["firstname"] == "Ada"
    assert rows[0]["lastname"] == "Lovelace"
    assert "ApplicationIntent=ReadOnly" in captured["connection_string"]
    assert captured["timeout"] == 10
    assert connection.cursor_value.executed is not None
    _, params = connection.cursor_value.executed
    assert params == (57, 57, None, None)
    assert connection.cursor_value.closed is True
    assert connection.closed is True


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self.rows)


class _FakeTransaction:
    def __enter__(self) -> _FakeTransaction:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class _FakeNeonConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, Any]] = []

    def __enter__(self) -> _FakeNeonConnection:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction()

    def exec_driver_sql(self, sql: str) -> None:
        self.calls.append((sql, None))

    def execute(self, statement: Any, params: dict[str, Any]) -> _FakeResult:
        self.calls.append((str(statement), params))
        return _FakeResult(self.rows)


class _FakeEngine:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.connection = _FakeNeonConnection(rows)

    def connect(self) -> _FakeNeonConnection:
        return self.connection


def test_neon_state_reader_sets_transaction_read_only() -> None:
    engine = _FakeEngine([{"crmstudentid": 101, "status": "synced"}])

    rows = dashboard_data.read_neon_states([101], engine=engine)

    assert rows == [{"crmstudentid": 101, "status": "synced"}]
    assert engine.connection.calls[0] == ("SET TRANSACTION READ ONLY", None)
    assert engine.connection.calls[1][1] == {"crmstudentids": [101]}


def test_job_reader_returns_active_plus_twenty_recent_without_private_columns() -> None:
    engine = _FakeEngine([{"id": "job-1", "progress": {"total": 1}}])

    rows = dashboard_data.read_jobs(limit=20, engine=engine)

    assert rows == [{"id": "job-1", "progress": {"total": 1}}]
    assert engine.connection.calls[0] == ("SET TRANSACTION READ ONLY", None)
    assert engine.connection.calls[1][1] == {"recent_limit": 20}
    job_sql = dashboard_data.NEON_JOBS_SQL.lower()
    assert "status = 'running'" in job_sql
    assert "status <> 'running'" in job_sql
    for forbidden in ("runner_id", "lease_token", "payload", "summary"):
        assert forbidden not in job_sql


def test_load_students_reads_neon_in_one_batch(monkeypatch) -> None:
    calls: list[list[int]] = []
    monkeypatch.setattr(
        dashboard_data,
        "read_crm_students",
        lambda **_kwargs: [_crm_student(101), _crm_student(102)],
    )

    def read_states(ids: list[int]) -> list[dict[str, Any]]:
        calls.append(ids)
        return [{"crmstudentid": 102, "status": "synced"}]

    monkeypatch.setattr(dashboard_data, "read_neon_states", read_states)

    students = dashboard_data.load_students(franchise_id=57)

    assert calls == [[101, 102]]
    assert [student.status for student in students] == ["never", "synced"]


def test_neon_engine_creation_failure_is_wrapped_without_details(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_data,
        "get_engine",
        lambda: (_ for _ in ()).throw(RuntimeError("postgres://owner:secret@private")),
    )

    with pytest.raises(dashboard_data.DashboardDataError) as error:
        dashboard_data.read_jobs()

    assert str(error.value) == "dashboard_data_unavailable"
