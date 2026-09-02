from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from ui import dashboard_data


def _crm_student(
    student_id: int,
    *,
    franchise_id: int = 57,
    franchise_name: object = "Gilbert",
    grade: object = 10,
) -> dict:
    return {
        "crmstudentid": student_id,
        "franchiseid": franchise_id,
        "franchise_name": franchise_name,
        "firstname": "Ada",
        "lastname": f"Student {student_id}",
        "grade": grade,
        "portal_url": "https://grades.example.test/login",
    }


@pytest.mark.parametrize(
    ("raw_grade", "expected"),
    [
        (7, 7),
        ("10", 10),
        ("6th", 6),
        ("7th", 7),
        ("10th", 10),
        ("12TH", 12),
        (True, None),
        (None, None),
        ("", None),
        ("college", None),
        ("10th Grade", None),
    ],
)
def test_merge_normalizes_supported_crm_grade_levels(
    raw_grade: object, expected: int | None
) -> None:
    student = dashboard_data.merge_student_rows(
        [_crm_student(101, grade=raw_grade)], []
    )[0]

    assert student.grade_level == expected


def test_crm_query_checks_credentials_without_selecting_them() -> None:
    sql = dashboard_data.CRM_STUDENTS_SQL.lower()
    projection = sql.split("from dbo.tblstudents", 1)[0]

    assert "gradeportalurl" in projection
    assert "gradeportaluser" not in projection
    assert "gradeportalpwd" not in projection
    assert "istrail" not in projection
    assert "s.istrail = 'active'" in sql
    assert "nullif(ltrim(rtrim(s.gradeportalurl)), '') is not null" in sql
    assert "nullif(ltrim(rtrim(s.gradeportaluser)), '') is not null" in sql
    assert "nullif(ltrim(rtrim(s.gradeportalpwd)), '') is not null" in sql


def test_neon_student_query_never_selects_runner_secrets() -> None:
    sql = dashboard_data.NEON_STATES_SQL.lower()

    assert "weeklydata" in sql
    assert "primary_agenda" in sql
    assert "secondary_agenda" in sql
    assert "grade_status" in sql
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
            "primary_agenda": {"portal": None, "weeks": {}},
            "secondary_agenda": {"portal": None, "weeks": {}},
            "grade_status": "synced",
            "passwordgood": True,
            "grade_updated_at": datetime(2026, 7, 14, 12, 30, tzinfo=UTC),
        },
        {
            "crmstudentid": 1,
            "weeklydata": {"2026-07-13": {"Wrong legacy match": 0}},
            "grade_status": "scrape_failed",
        },
    ]

    students = dashboard_data.merge_student_rows(crm_rows, state_rows)

    assert [student.crmstudentid for student in students] == [101, 102]
    assert students[0].grade_status == "synced"
    assert students[0].grades == {"2026-07-13": {"English": 91.5}}
    assert students[1].grade_status == "never"
    assert students[1].grades == {}
    assert not hasattr(students[0], "p1password")
    assert not hasattr(students[0], "portal2")


@pytest.mark.parametrize(
    ("raw_course", "expected"),
    [
        ("1: ENGLISH 8", "ENGLISH 8"),
        (" 3 : SPANISH IB ", "SPANISH IB"),
        ("12:  ADVANCED CHOIR", "ADVANCED CHOIR"),
        ("HISTORY 8-A", "HISTORY 8-A"),
        ("101 ALGEBRA", "101 ALGEBRA"),
        ("1:", "1:"),
    ],
)
def test_display_course_name_removes_only_a_leading_period_prefix(
    raw_course: str, expected: str
) -> None:
    assert dashboard_data._display_course_name(raw_course) == expected


def _course_label_report() -> dashboard_data.DashboardStudent:
    return dashboard_data.merge_student_rows(
        [_crm_student(101)],
        [
            {
                "crmstudentid": 101,
                "weeklydata": {
                    "2026-07-06": {
                        "7: ALGEBRA": 70.0,
                        "2: BIOLOGY": 90.0,
                        "4: CHEMISTRY": 98.0,
                        "9: ZOOLOGY": 91.0,
                    },
                    "2026-07-13": {
                        "9: ZOOLOGY": 91.0,
                        "1: ALGEBRA": 72.0,
                        "2: BIOLOGY": 88.0,
                        "4: CHEMISTRY": 99.0,
                    },
                },
            }
        ],
    )[0]


def test_student_report_alphabetizes_cleaned_recent_course_labels() -> None:
    report = _course_label_report()

    assert [grade.course for grade in report.grades_snapshot] == [
        "ALGEBRA",
        "BIOLOGY",
        "CHEMISTRY",
        "ZOOLOGY",
    ]


def test_student_report_keeps_low_and_high_grades_ordered_by_score() -> None:
    report = _course_label_report()

    assert [(grade.course, grade.grade) for grade in report.low_grades] == [
        ("ALGEBRA", 72.0),
        ("BIOLOGY", 88.0),
        ("ZOOLOGY", 91.0),
    ]
    assert [(grade.course, grade.grade) for grade in report.high_grades] == [
        ("ZOOLOGY", 91.0),
        ("CHEMISTRY", 99.0),
    ]


def test_student_report_matches_changes_with_raw_course_names() -> None:
    report = _course_label_report()
    by_course = {grade.course: grade for grade in report.grades_snapshot}

    assert by_course["ALGEBRA"].change is None
    assert by_course["BIOLOGY"].change == "-"
    assert by_course["CHEMISTRY"].change == "+"
    assert by_course["ZOOLOGY"].change is None


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
            "failure_code": "unsafe error with details",
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
            {"crmstudentid": 101, "grade_status": "synced", "passwordgood": True},
            {
                "crmstudentid": 102,
                "grade_status": "bad_login",
                "passwordgood": False,
            },
        ],
    )

    summaries = dashboard_data.summarize_franchises(students)

    assert [summary["id"] for summary in summaries] == [57, 99]
    assert summaries[0]["name"] == "Tutoring Club of Gilbert"
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
        ("franchise_name",),
    ]

    def __init__(self) -> None:
        self.executed: tuple[str, tuple[Any, ...]] | None = None
        self.closed = False

    def execute(self, sql: str, *params: Any) -> None:
        self.executed = (sql, params)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [
            (
                101,
                57,
                " Ada ",
                " Lovelace ",
                10,
                "https://grades.example.test/login",
                " Gilbert ",
            )
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
    assert rows[0]["franchise_name"] == "Gilbert"
    assert "ApplicationIntent=ReadOnly" in captured["connection_string"]
    assert captured["timeout"] == 10
    assert connection.cursor_value.executed is not None
    _, params = connection.cursor_value.executed
    assert params == (57, 57, None, None)
    assert connection.cursor_value.closed is True
    assert connection.closed is True


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("Tutoring Club of Gilbert", "Tutoring Club of Gilbert"),
        ("Tutoring Club OF Gilbert", "Tutoring Club OF Gilbert"),
        ("East - Tutoring Club of Gilbert", "East - Tutoring Club of Gilbert"),
        ("Gilbert", "Tutoring Club of Gilbert"),
        ("  Gilbert  ", "Tutoring Club of Gilbert"),
        ("", "Franchise 57"),
        ("   ", "Franchise 57"),
        (None, "Franchise 57"),
    ],
)
def test_load_franchise_name_normalizes_the_cover_label(
    monkeypatch, raw_name: object, expected: str
) -> None:
    def read_name(franchise_id: int) -> object:
        assert franchise_id == 57
        return raw_name

    monkeypatch.setattr(dashboard_data, "read_crm_franchise_name", read_name)

    assert dashboard_data.load_franchise_name(57) == expected


class _FakeFranchiseCursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row
        self.executed: tuple[str, tuple[Any, ...]] | None = None
        self.closed = False

    def execute(self, sql: str, *params: Any) -> None:
        self.executed = (sql, params)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row

    def close(self) -> None:
        self.closed = True


class _FakeFranchiseConnection:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.cursor_value = _FakeFranchiseCursor(row)
        self.closed = False

    def cursor(self) -> _FakeFranchiseCursor:
        return self.cursor_value

    def close(self) -> None:
        self.closed = True


def test_crm_franchise_name_reader_is_parameterized_and_read_only(monkeypatch) -> None:
    monkeypatch.setenv("CRMSrvAddress", "crm.example.test")
    monkeypatch.setenv("CRMSrvDb", "CRM")
    monkeypatch.setenv("CRMSrvUs", "reader")
    monkeypatch.setenv("CRMSrvPs", "secret-value")
    connection = _FakeFranchiseConnection((" Gilbert ",))
    captured: dict[str, Any] = {}

    def connect(
        connection_string: str, *, timeout: int
    ) -> _FakeFranchiseConnection:
        captured["connection_string"] = connection_string
        captured["timeout"] = timeout
        return connection

    name = dashboard_data.read_crm_franchise_name(57, connect=connect)

    assert name == "Gilbert"
    assert "ApplicationIntent=ReadOnly" in captured["connection_string"]
    assert captured["timeout"] == 10
    assert connection.cursor_value.executed is not None
    sql, params = connection.cursor_value.executed
    assert "from dbo.tblfranchies" in sql.lower()
    assert "franchiesname" in sql.lower()
    assert params == (57,)
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

    rows = dashboard_data.read_jobs(franchise_id=57, limit=20, engine=engine)

    assert rows == [{"id": "job-1", "progress": {"total": 1}}]
    assert engine.connection.calls[0] == ("SET TRANSACTION READ ONLY", None)
    assert engine.connection.calls[1][1] == {
        "recent_limit": 20,
        "franchise_id": 57,
    }
    job_sql = dashboard_data.NEON_JOBS_SQL.lower()
    assert "status = 'running'" in job_sql
    assert "status <> 'running'" in job_sql
    assert job_sql.count("grade_scrape_jobs.franchise_id = :franchise_id") == 2
    assert "franchise_id is null" not in job_sql
    for forbidden in ("runner_id", "lease_token", "payload", "summary"):
        assert forbidden not in job_sql


def test_job_reader_requires_franchise_scope() -> None:
    engine = _FakeEngine([])

    with pytest.raises(TypeError):
        dashboard_data.read_jobs(limit=20, engine=engine)

    assert engine.connection.calls == []


def test_job_loader_requires_franchise_scope(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_data, "read_jobs", lambda **_kwargs: [])

    with pytest.raises(TypeError):
        dashboard_data.load_jobs(limit=20)


def test_job_reader_rejects_explicit_none_before_query() -> None:
    engine = _FakeEngine([])

    with pytest.raises(ValueError):
        dashboard_data.read_jobs(None, limit=20, engine=engine)

    assert engine.connection.calls == []


def test_job_loader_rejects_explicit_none_before_reader(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def read_jobs(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(dashboard_data, "read_jobs", read_jobs)

    with pytest.raises(ValueError):
        dashboard_data.load_jobs(None, limit=20)

    assert calls == []


def test_load_students_reads_neon_in_one_batch(monkeypatch) -> None:
    calls: list[list[int]] = []
    monkeypatch.setattr(
        dashboard_data,
        "read_crm_students",
        lambda **_kwargs: [_crm_student(101), _crm_student(102)],
    )

    def read_states(ids: list[int]) -> list[dict[str, Any]]:
        calls.append(ids)
        return [{"crmstudentid": 102, "grade_status": "synced"}]

    monkeypatch.setattr(dashboard_data, "read_neon_states", read_states)

    students = dashboard_data.load_students(franchise_id=57)

    assert calls == [[101, 102]]
    assert [student.grade_status for student in students] == ["never", "synced"]


def test_neon_engine_creation_failure_is_wrapped_without_details(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_data,
        "get_engine",
        lambda: (_ for _ in ()).throw(RuntimeError("postgres://owner:secret@private")),
    )

    with pytest.raises(dashboard_data.DashboardDataError) as error:
        dashboard_data.read_jobs(57)

    assert str(error.value) == "dashboard_data_unavailable"
