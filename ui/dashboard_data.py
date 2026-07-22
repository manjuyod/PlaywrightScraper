from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import pyodbc
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from db_core import get_engine


CRM_STUDENTS_SQL = """
SELECT
    s.Id AS crmstudentid,
    s.FranchiseID AS franchiseid,
    s.FirstName AS firstname,
    s.LastName AS lastname,
    s.Grade AS grade,
    s.GradePortalURL AS portal_url
FROM dbo.tblStudents AS s
WHERE (? IS NULL OR s.FranchiseID = ?)
  AND (? IS NULL OR s.Id = ?)
  AND s.IsTrail = 'Active'
  AND NULLIF(LTRIM(RTRIM(s.GradePortalURL)), '') IS NOT NULL
  AND NULLIF(LTRIM(RTRIM(s.GradePortalUser)), '') IS NOT NULL
  AND NULLIF(LTRIM(RTRIM(s.GradePortalPwd)), '') IS NOT NULL
ORDER BY s.FranchiseID, s.LastName, s.FirstName, s.Id
"""


NEON_STATES_SQL = """
SELECT
    crmstudentid,
    weeklydata,
    weekly_agenda,
    status,
    passwordgood,
    error_msg,
    updated_at
FROM students_grades_20262027
WHERE crmstudentid IN :crmstudentids
"""


NEON_JOBS_SQL = """
WITH active_jobs AS (
    SELECT
        id,
        kind,
        status,
        franchise_id,
        student_id,
        progress,
        error_msg,
        started_at,
        updated_at,
        completed_at
    FROM grade_scrape_jobs
    WHERE status = 'running'
),
recent_jobs AS (
    SELECT
        id,
        kind,
        status,
        franchise_id,
        student_id,
        progress,
        error_msg,
        started_at,
        updated_at,
        completed_at
    FROM grade_scrape_jobs
    WHERE status <> 'running'
    ORDER BY created_at DESC
    LIMIT :recent_limit
)
SELECT *
FROM (
    SELECT * FROM active_jobs
    UNION ALL
    SELECT * FROM recent_jobs
) AS visible_jobs
ORDER BY
    CASE WHEN status = 'running' THEN 0 ELSE 1 END,
    started_at DESC
"""


_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_:-]{0,63}$")
_KNOWN_STATUSES = {"never", "synced", "error"}


class DashboardDataError(RuntimeError):
    """Safe boundary error that never includes dependency details."""


def _crm_connection_string() -> str:
    database = os.getenv("CRMSrvDb", "").strip() or os.getenv("CRMSrvDbQA", "").strip()
    values = {
        "host": os.getenv("CRMSrvAddress", "").strip(),
        "database": database,
        "username": os.getenv("CRMSrvUs", "").strip(),
        "password": os.getenv("CRMSrvPs", ""),
    }
    if not all(values.values()):
        raise DashboardDataError("dashboard_data_unavailable")
    trust = os.getenv("CRM_TRUST_SERVER_CERTIFICATE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    return (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={values['host']};"
        f"DATABASE={values['database']};"
        f"UID={values['username']};"
        f"PWD={values['password']};"
        "Encrypt=yes;"
        f"TrustServerCertificate={'yes' if trust else 'no'};"
        "ApplicationIntent=ReadOnly;"
    )


def _cursor_rows(cursor: Any) -> list[dict[str, Any]]:
    columns = [str(column[0]).lower() for column in (cursor.description or [])]
    rows: list[dict[str, Any]] = []
    for raw_row in cursor.fetchall():
        row = {
            column: raw_row[index]
            for index, column in enumerate(columns)
            if index < len(raw_row)
        }
        for key, value in tuple(row.items()):
            if isinstance(value, str):
                row[key] = value.strip()
        rows.append(row)
    return rows


def read_crm_students(
    franchise_id: int | None = None,
    student_id: int | None = None,
    *,
    connect: Any = None,
) -> list[dict[str, Any]]:
    connector = connect or pyodbc.connect
    connection = None
    cursor = None
    try:
        connection = connector(_crm_connection_string(), timeout=10)
        cursor = connection.cursor()
        cursor.execute(
            CRM_STUDENTS_SQL,
            franchise_id,
            franchise_id,
            student_id,
            student_id,
        )
        return _cursor_rows(cursor)
    except DashboardDataError:
        raise
    except Exception:
        raise DashboardDataError("dashboard_data_unavailable") from None
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def _read_neon(
    statement: Any,
    params: dict[str, Any],
    *,
    engine: Engine | Any | None = None,
) -> list[dict[str, Any]]:
    try:
        selected_engine = engine or get_engine()
        with selected_engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                result = connection.execute(statement, params)
                return [dict(row) for row in result.mappings().all()]
    except Exception:
        raise DashboardDataError("dashboard_data_unavailable") from None


def read_neon_states(
    crmstudentids: Sequence[int],
    *,
    engine: Engine | Any | None = None,
) -> list[dict[str, Any]]:
    ids = [int(student_id) for student_id in crmstudentids]
    if not ids:
        return []
    statement = text(NEON_STATES_SQL).bindparams(
        bindparam("crmstudentids", expanding=True)
    )
    return _read_neon(statement, {"crmstudentids": ids}, engine=engine)


def read_jobs(
    limit: int = 20,
    *,
    engine: Engine | Any | None = None,
) -> list[dict[str, Any]]:
    recent_limit = min(max(int(limit), 1), 100)
    return _read_neon(
        text(NEON_JOBS_SQL),
        {"recent_limit": recent_limit},
        engine=engine,
    )


def load_students(
    franchise_id: int | None = None,
    student_id: int | None = None,
) -> list[DashboardStudent]:
    crm_rows = read_crm_students(franchise_id=franchise_id, student_id=student_id)
    crmstudentids = [
        student_id
        for student_id in (_optional_int(row.get("crmstudentid")) for row in crm_rows)
        if student_id is not None
    ]
    state_rows = read_neon_states(crmstudentids)
    return merge_student_rows(crm_rows, state_rows)


def load_student(franchise_id: int, crmstudentid: int) -> DashboardStudent | None:
    students = load_students(franchise_id=franchise_id, student_id=crmstudentid)
    return students[0] if students else None


def load_jobs(limit: int = 20) -> list[dict[str, Any]]:
    return [shape_job_row(row) for row in read_jobs(limit=limit)]


@dataclass(frozen=True)
class CourseGrade:
    course: str
    grade: float
    change: str | None = None


@dataclass(frozen=True)
class DashboardStudent:
    crmstudentid: int
    franchiseid: int
    first_name: str
    last_name: str
    grade_level: int | None
    portal_url: str | None
    grades: dict[str, dict[str, Any]]
    agenda: dict[str, Any]
    status: str
    passwordgood: bool | None
    error_code: str | None
    updated_at: datetime | date | str | None
    grades_snapshot: tuple[CourseGrade, ...] = field(default_factory=tuple)
    low_grades: tuple[CourseGrade, ...] = field(default_factory=tuple)
    high_grades: tuple[CourseGrade, ...] = field(default_factory=tuple)
    standing: str | None = None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_status(value: Any) -> str:
    candidate = str(value or "never").strip().lower()
    return candidate if candidate in _KNOWN_STATUSES else "never"


def _safe_error_code(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip().lower()
    return candidate if _SAFE_CODE.fullmatch(candidate) else "runner_failed"


def _safe_http_url(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def merge_student_rows(
    crm_rows: Sequence[Mapping[str, Any]],
    state_rows: Sequence[Mapping[str, Any]],
) -> list[DashboardStudent]:
    states: dict[int, Mapping[str, Any]] = {}
    for row in state_rows:
        crmstudentid = _optional_int(row.get("crmstudentid"))
        if crmstudentid is not None:
            states[crmstudentid] = row

    students: list[DashboardStudent] = []
    for crm_row in crm_rows:
        crmstudentid = _optional_int(crm_row.get("crmstudentid"))
        franchiseid = _optional_int(crm_row.get("franchiseid"))
        if crmstudentid is None or franchiseid is None:
            continue
        state = states.get(crmstudentid, {})
        student = DashboardStudent(
            crmstudentid=crmstudentid,
            franchiseid=franchiseid,
            first_name=str(crm_row.get("firstname") or "").strip(),
            last_name=str(crm_row.get("lastname") or "").strip(),
            grade_level=_optional_int(crm_row.get("grade")),
            portal_url=_safe_http_url(crm_row.get("portal_url")),
            grades=_json_object(state.get("weeklydata")),
            agenda=_json_object(state.get("weekly_agenda")),
            status=_safe_status(state.get("status")),
            passwordgood=(
                state.get("passwordgood")
                if isinstance(state.get("passwordgood"), bool)
                else None
            ),
            error_code=_safe_error_code(state.get("error_msg")),
            updated_at=state.get("updated_at"),
        )
        students.append(build_student_report(student))
    return students


def _numeric_grades(raw: Mapping[str, Any]) -> dict[str, float]:
    grades: dict[str, float] = {}
    for course, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        grades[str(course)] = float(value)
    return grades


def build_student_report(student: DashboardStudent) -> DashboardStudent:
    dated_grades = [
        (str(week), _numeric_grades(grades))
        for week, grades in sorted(
            student.grades.items(), key=lambda item: str(item[0])
        )
        if isinstance(grades, Mapping) and _numeric_grades(grades)
    ]
    if not dated_grades:
        return student

    current = dated_grades[-1][1]
    previous = dated_grades[-2][1] if len(dated_grades) > 1 else {}
    snapshot = tuple(
        CourseGrade(
            course=course,
            grade=grade,
            change=(
                "+"
                if course in previous and grade > previous[course]
                else "-"
                if course in previous and grade < previous[course]
                else None
            ),
        )
        for course, grade in current.items()
    )
    sorted_grades = tuple(sorted(snapshot, key=lambda item: item.grade))
    minimum = sorted_grades[0].grade
    standing = "Poor" if minimum < 70 else "Fair" if minimum < 80 else "Good"
    return replace(
        student,
        grades_snapshot=snapshot,
        low_grades=sorted_grades[:3],
        high_grades=sorted_grades[-2:],
        standing=standing,
    )


def _iso_timestamp(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _count(value: Any) -> int:
    parsed = _optional_int(value)
    return max(parsed or 0, 0)


def shape_job_row(row: Mapping[str, Any]) -> dict[str, Any]:
    progress = _json_object(row.get("progress"))
    return {
        "id": str(row.get("id") or ""),
        "kind": str(row.get("kind") or ""),
        "status": str(row.get("status") or ""),
        "franchiseId": _optional_int(row.get("franchise_id")),
        "studentId": _optional_int(row.get("student_id")),
        "total": _count(progress.get("total")),
        "attempted": _count(progress.get("attempted")),
        "success": _count(progress.get("success")),
        "errors": _count(progress.get("errors")),
        "startedAt": _iso_timestamp(row.get("started_at")),
        "updatedAt": _iso_timestamp(row.get("updated_at")),
        "completedAt": _iso_timestamp(row.get("completed_at")),
        "errorCode": _safe_error_code(row.get("error_msg")),
    }


def summarize_franchises(students: Sequence[DashboardStudent]) -> list[dict[str, Any]]:
    grouped: dict[int, list[DashboardStudent]] = {}
    for student in students:
        grouped.setdefault(student.franchiseid, []).append(student)

    summaries: list[dict[str, Any]] = []
    for franchiseid in sorted(grouped):
        franchise_students = grouped[franchiseid]
        updated = [
            timestamp
            for timestamp in (
                _iso_timestamp(student.updated_at) for student in franchise_students
            )
            if timestamp is not None
        ]
        summaries.append(
            {
                "id": franchiseid,
                "total": len(franchise_students),
                "synced": sum(
                    student.status == "synced" for student in franchise_students
                ),
                "errorCount": sum(
                    student.status == "error" for student in franchise_students
                ),
                "badLogins": sum(
                    student.passwordgood is False for student in franchise_students
                ),
                "lastUpdated": max(updated) if updated else None,
            }
        )
    return summaries
