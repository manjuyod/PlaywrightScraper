from __future__ import annotations

import os
from typing import Any, Iterable

from flask import abort, jsonify, redirect, render_template, request, url_for

from ui import dashboard_data as dashboard
from ui.app import app


GRADE_FILTER_LEVELS = {
    "middle_school": {6, 7, 8},
    "high_school": {9, 10, 11, 12},
}


def _normalize_grade_filter(raw_filter: str | None) -> str:
    return raw_filter if raw_filter in GRADE_FILTER_LEVELS else "all"


def _filter_students_by_grade(
    students: Iterable[dashboard.DashboardStudent], grade_filter: str
) -> list[dashboard.DashboardStudent]:
    levels = GRADE_FILTER_LEVELS.get(grade_filter)
    if levels is None:
        return list(students)
    return [student for student in students if student.grade_level in levels]


def _grade_items(grades: Iterable[dashboard.CourseGrade]) -> list[dict[str, Any]]:
    return [
        {"course": grade.course, "grade": grade.grade, "change": grade.change}
        for grade in grades
    ]


def _public_grade_history(
    student: dashboard.DashboardStudent,
) -> dict[str, dict[str, float]]:
    history: dict[str, dict[str, float]] = {}
    for week, grades in sorted(student.grades.items(), key=lambda item: str(item[0])):
        if not isinstance(grades, dict):
            continue
        public_grades = {
            str(course): float(value)
            for course, value in grades.items()
            if not isinstance(value, bool) and isinstance(value, (int, float))
        }
        if public_grades:
            history[str(week)] = public_grades
    return history


def _agenda_items(student: dashboard.DashboardStudent) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for due_date, assignments in sorted(
        student.agenda.items(), key=lambda item: str(item[0])
    ):
        if not isinstance(assignments, list):
            continue
        for assignment in assignments:
            if not isinstance(assignment, (list, tuple)) or len(assignment) < 2:
                continue
            items.append(
                {
                    "dueDate": str(due_date)[:64],
                    "course": str(assignment[0])[:500],
                    "title": str(assignment[1])[:500],
                }
            )
    return items


def _student_card(student: dashboard.DashboardStudent) -> dict[str, Any]:
    return {
        "id": student.crmstudentid,
        "detailUrl": url_for(
            "student_view",
            franchise_id=student.franchiseid,
            crmstudentid=student.crmstudentid,
        ),
        "firstName": student.first_name,
        "lastName": student.last_name,
        "gradeLevel": student.grade_level,
        "portalUrl": student.portal_url,
        "status": student.status,
        "errorCode": student.error_code,
        "updatedAt": dashboard._iso_timestamp(student.updated_at),
        "standing": student.standing,
        "gradesSnapshot": _grade_items(student.grades_snapshot),
        "lowGrades": _grade_items(student.low_grades),
        "highGrades": _grade_items(student.high_grades),
    }


def _student_detail(student: dashboard.DashboardStudent) -> dict[str, Any]:
    payload = _student_card(student)
    payload.pop("detailUrl", None)
    payload["grades"] = _public_grade_history(student)
    payload["agendaItems"] = _agenda_items(student)
    return payload


def _render_dashboard(page_data: dict[str, Any]):
    return render_template(
        "dashboard.html",
        page_data=page_data,
        page_title=page_data.get("title", "TC Grade Dashboard"),
    )


def _is_dev_mode() -> bool:
    return os.getenv("PYTHON_ENV", "").strip().lower() == "dev"


def _unauthorized():
    return render_template("unauthorized.html"), 403


@app.get("/")
def index():
    if not _is_dev_mode():
        return _unauthorized()
    students = dashboard.load_students()
    jobs = dashboard.load_jobs(limit=20)
    franchises = dashboard.summarize_franchises(students)
    for franchise in franchises:
        franchise["url"] = url_for("franchise_view", franchise_id=franchise["id"])
    return _render_dashboard(
        {
            "page": "home",
            "title": "Grade Operations Overview",
            "logoUrl": url_for("static", filename="imgs/tc_logo.webp"),
            "jobsUrl": url_for("jobs_api"),
            "countAll": len(students),
            "countSynced": sum(student.status == "synced" for student in students),
            "countBadLogins": sum(
                student.passwordgood is False for student in students
            ),
            "jobs": jobs,
            "franchises": franchises,
        }
    )


@app.get("/health")
def health():
    return redirect(url_for("index"))


@app.get("/login")
def login():
    return redirect(url_for("index"))


@app.get("/franchise/<int:franchise_id>")
def franchise_view(franchise_id: int):
    grade_filter = _normalize_grade_filter(request.args.get("grade_filter"))
    students = dashboard.load_students(franchise_id=franchise_id)
    visible_students = _filter_students_by_grade(students, grade_filter)
    filters = [
        {
            "value": value,
            "label": label,
            "url": url_for(
                "franchise_view",
                franchise_id=franchise_id,
                grade_filter=value,
            ),
        }
        for value, label in (
            ("all", "All"),
            ("middle_school", "Middle School"),
            ("high_school", "High School"),
        )
    ]
    page_data = {
        "page": "franchise",
        "title": f"Franchise {franchise_id}",
        "logoUrl": url_for("static", filename="imgs/tc_logo.webp"),
        "franchiseId": franchise_id,
        "gradeFilter": grade_filter,
        "filters": filters,
        "students": [_student_card(student) for student in visible_students],
    }
    return _render_dashboard(page_data)


@app.get("/franchise/<int:franchise_id>/student/<int:crmstudentid>")
def student_view(franchise_id: int, crmstudentid: int):
    student = dashboard.load_student(franchise_id, crmstudentid)
    if student is None:
        abort(404)
    page_data = {
        "page": "student",
        "title": f"{student.first_name} {student.last_name}",
        "logoUrl": url_for("static", filename="imgs/tc_logo.webp"),
        "backUrl": url_for("franchise_view", franchise_id=franchise_id),
        "student": _student_detail(student),
    }
    if _is_dev_mode():
        page_data["homeUrl"] = url_for("index")
    return _render_dashboard(page_data)


@app.get("/api/jobs")
def jobs_api():
    if not _is_dev_mode():
        return _unauthorized()
    return jsonify({"jobs": dashboard.load_jobs(limit=20)})
