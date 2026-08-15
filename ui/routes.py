from __future__ import annotations

import os
import re
from datetime import date, timedelta
from typing import Any, Iterable

from flask import abort, jsonify, redirect, render_template, request, url_for

from ui import dashboard_data as dashboard
from ui.app import app


GRADE_FILTER_LEVELS = {
    "middle_school": {6, 7, 8},
    "high_school": {9, 10, 11, 12},
}

_PORTAL_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DUE_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_PORTAL_LABELS = {
    "aeries": "Aeries",
    "asuprep": "ASU Prep",
    "blackbaud": "Blackbaud",
    "canvas": "Canvas",
    "classlink": "ClassLink",
    "google_classroom": "Google Classroom",
    "gps": "GPS",
    "homeaccess": "Home Access Center",
    "howsschoolgoing": "How's School Going",
    "infinite_campus": "Infinite Campus",
    "k12": "K12",
    "microsoft_benjamin_franklin": "Benjamin Franklin",
    "parentvue": "ParentVUE",
    "powerschool": "PowerSchool",
    "schoology": "Schoology",
    "schooltool": "SchoolTool",
    "student_connection": "Student Connection",
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


def _agenda_slots(
    student: dashboard.DashboardStudent, *, today: date | None = None
) -> list[dict[str, Any]]:
    agenda = student.agenda
    if not isinstance(agenda, dict):
        return []

    raw_slots = [agenda.get(key) for key in ("agenda1", "agenda2")]
    if any(
        not isinstance(slot, dict)
        or "portal" not in slot
        or "weeks" not in slot
        or (slot["portal"] is not None and not isinstance(slot["portal"], str))
        or not isinstance(slot["weeks"], dict)
        for slot in raw_slots
    ):
        return []

    reference = today or date.today()
    current_monday = reference - timedelta(days=reference.weekday())

    def display_text(value: object) -> str:
        return value[:500] if isinstance(value, str) else ""

    def week_rank(week_start: date) -> tuple[int, int]:
        ordinal = week_start.toordinal()
        if week_start == current_monday:
            return (0, 0)
        if week_start < current_monday:
            return (1, -ordinal)
        return (2, ordinal)

    def project_slot(number: int) -> dict[str, Any]:
        raw_slot = agenda.get(f"agenda{number}")
        slot = raw_slot if isinstance(raw_slot, dict) else {}
        raw_portal = slot.get("portal")
        portal = (
            raw_portal
            if isinstance(raw_portal, str) and _PORTAL_KEY.fullmatch(raw_portal)
            else None
        )
        result: dict[str, Any] = {"number": number, "portal": portal, "weeks": []}
        if portal is not None:
            result["portalLabel"] = _PORTAL_LABELS.get(
                portal, portal.replace("_", " ").title()
            )

        raw_weeks = slot.get("weeks")
        if not isinstance(raw_weeks, dict):
            return result

        weeks: list[tuple[date, dict[str, Any]]] = []
        for raw_week_start, raw_classes in raw_weeks.items():
            if not isinstance(raw_week_start, str) or not isinstance(raw_classes, dict):
                continue
            try:
                week_start = date.fromisoformat(raw_week_start)
            except ValueError:
                continue
            if week_start.weekday() != 0:
                continue

            classes: list[dict[str, Any]] = []
            for raw_name, raw_buckets in raw_classes.items():
                name = display_text(raw_name)
                if not name or not isinstance(raw_buckets, dict):
                    continue
                missing = raw_buckets.get("missing")
                due = raw_buckets.get("due")
                if not isinstance(missing, list) or not isinstance(due, list):
                    continue

                assignments: list[dict[str, Any]] = []
                for status, rows in (("missing", missing), ("due", due)):
                    valid_rows: list[tuple[date, str, str, dict[str, Any]]] = []
                    for raw_row in rows:
                        if not isinstance(raw_row, dict):
                            continue
                        title = display_text(raw_row.get("title"))
                        raw_due_date = raw_row.get("dueDate")
                        raw_due_time = raw_row.get("dueTime")
                        if not title or not isinstance(raw_due_date, str):
                            continue
                        try:
                            due_date = date.fromisoformat(raw_due_date)
                        except ValueError:
                            continue
                        if due_date - timedelta(days=due_date.weekday()) != week_start:
                            continue
                        if raw_due_time is not None and (
                            not isinstance(raw_due_time, str)
                            or _DUE_TIME.fullmatch(raw_due_time) is None
                        ):
                            continue
                        due_time = raw_due_time if isinstance(raw_due_time, str) else None
                        due_display = f"{due_date.strftime('%b')} {due_date.day}"
                        if due_time is not None:
                            due_display = f"{due_display} · {due_time}"
                        valid_rows.append(
                            (
                                due_date,
                                due_time or "",
                                title.casefold(),
                                {
                                    "status": status,
                                    "title": title,
                                    "dueDate": due_date.isoformat(),
                                    "dueTime": due_time,
                                    "dueDisplay": due_display,
                                },
                            )
                        )
                    assignments.extend(row[3] for row in sorted(valid_rows))
                classes.append(
                    {"name": name, "count": len(assignments), "assignments": assignments}
                )

            weeks.append(
                (
                    week_start,
                    {
                        "weekStart": week_start.isoformat(),
                        "label": f"Week of {week_start.strftime('%b')} {week_start.day}",
                        "classes": sorted(classes, key=lambda item: item["name"].casefold()),
                    },
                )
            )

        result["weeks"] = [week for _, week in sorted(weeks, key=lambda item: week_rank(item[0]))]
        return result

    return [project_slot(1), project_slot(2)]


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
    agenda_slots = _agenda_slots(student)
    payload["agendaSlots"] = agenda_slots
    payload["agendaItems"] = [] if agenda_slots else _agenda_items(student)
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
    return render_template("unauthorized.html"), 200


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
    franchise_name = dashboard.load_franchise_name(franchise_id)
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
        "franchiseName": franchise_name,
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
    return _render_dashboard(page_data)


@app.get("/api/jobs")
def jobs_api():
    if not _is_dev_mode():
        return _unauthorized()
    return jsonify({"jobs": dashboard.load_jobs(limit=20)})
