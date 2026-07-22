from __future__ import annotations

import importlib
import json
import re
import sys
from datetime import UTC, datetime
from typing import Any

from ui import dashboard_data


def _student(
    student_id: int,
    *,
    franchise_id: int = 57,
    grade: int = 10,
    status: str = "synced",
) -> dashboard_data.DashboardStudent:
    return dashboard_data.merge_student_rows(
        [
            {
                "crmstudentid": student_id,
                "franchiseid": franchise_id,
                "firstname": "Ada",
                "lastname": f"Student {student_id}",
                "grade": grade,
                "portal_url": "https://grades.example.test/login",
            }
        ],
        [
            {
                "crmstudentid": student_id,
                "weeklydata": {"2026-07-13": {"English": 91.5}},
                "weekly_agenda": {"2026-07-15": [["English", "Essay"]]},
                "status": status,
                "passwordgood": status == "synced",
                "error_msg": None if status == "synced" else "scrape_failed",
                "updated_at": datetime(2026, 7, 14, 12, 30, tzinfo=UTC),
            }
        ],
    )[0]


def _job() -> dict[str, Any]:
    return {
        "id": "7a74c220-ae45-4db7-9d5d-328db45530c9",
        "kind": "grade",
        "status": "running",
        "franchiseId": 57,
        "studentId": None,
        "total": 40,
        "attempted": 4,
        "success": 1,
        "errors": 3,
        "startedAt": "2026-07-14T12:00:00+00:00",
        "updatedAt": "2026-07-14T12:05:00+00:00",
        "completedAt": None,
        "errorCode": None,
    }


def _create_client(monkeypatch, *, environment: str = "dev"):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("PYTHON_ENV", environment)
    for module_name in ("ui.routes", "ui.app"):
        sys.modules.pop(module_name, None)

    app_module = importlib.import_module("ui.app")
    routes = importlib.import_module("ui.routes")
    app_module.app.config.update(TESTING=True)

    students = [_student(101, grade=7), _student(102, grade=10)]
    monkeypatch.setattr(routes.dashboard, "load_students", lambda **_kwargs: students)
    monkeypatch.setattr(routes.dashboard, "load_jobs", lambda limit=20: [_job()])
    monkeypatch.setattr(
        routes.dashboard,
        "load_student",
        lambda franchise_id, crmstudentid: next(
            (
                student
                for student in students
                if student.franchiseid == franchise_id
                and student.crmstudentid == crmstudentid
            ),
            None,
        ),
    )
    return app_module.app.test_client(), routes


def _page_data(response) -> dict[str, Any]:
    html = response.get_data(as_text=True)
    match = re.search(
        r'<script id="tc-page-data" type="application/json">\s*(.*?)\s*</script>',
        html,
        re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_home_is_dev_only_overview_without_session_cookie(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    response = client.get("/")
    page_data = _page_data(response)

    assert response.status_code == 200
    assert page_data["page"] == "home"
    assert page_data["countAll"] == 2
    assert page_data["countSynced"] == 2
    assert page_data["franchises"][0]["id"] == 57
    assert page_data["jobs"] == [_job()]
    assert "Set-Cookie" not in response.headers


def test_non_dev_home_is_unauthorized_without_loading_dashboard_data(
    monkeypatch,
) -> None:
    client, routes = _create_client(monkeypatch, environment="production")

    def unexpected_call(**_kwargs):
        raise AssertionError("non-dev overview must not query dashboard data")

    monkeypatch.setattr(routes.dashboard, "load_students", unexpected_call)
    monkeypatch.setattr(routes.dashboard, "load_jobs", unexpected_call)

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 403
    assert "Unauthorized" in body
    assert "development environment" in body
    assert 'id="tc-page-data"' not in body


def test_login_and_health_are_compatibility_redirects(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    assert client.get("/login").headers["Location"].endswith("/")
    assert client.get("/health").headers["Location"].endswith("/")


def test_web_surface_rejects_old_mutation_routes(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    assert client.post("/").status_code == 405
    assert client.post("/franchise/57").status_code == 405
    assert client.post("/franchise/57/student/101").status_code == 405
    assert client.post("/login").status_code == 405
    assert client.post("/logout").status_code == 404
    assert client.get("/status/57").status_code == 404


def test_franchise_filter_uses_crm_grade_and_crmstudentid(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    response = client.get("/franchise/57?grade_filter=high_school")
    page_data = _page_data(response)

    assert response.status_code == 200
    assert page_data["gradeFilter"] == "high_school"
    assert [student["id"] for student in page_data["students"]] == [102]
    assert page_data["students"][0]["portalUrl"] == "https://grades.example.test/login"
    assert "altPortalUrl" not in page_data["students"][0]
    assert "username" not in response.get_data(as_text=True).lower()
    assert "password" not in response.get_data(as_text=True).lower()


def test_dev_franchise_page_does_not_expose_home_url(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    page_data = _page_data(client.get("/franchise/57"))

    assert "homeUrl" not in page_data


def test_dev_student_page_keeps_home_url(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    page_data = _page_data(client.get("/franchise/57/student/101"))

    assert page_data["homeUrl"] == "/"
    assert page_data["backUrl"] == "/franchise/57"


def test_student_route_returns_404_when_not_currently_runnable(monkeypatch) -> None:
    client, routes = _create_client(monkeypatch)
    monkeypatch.setattr(
        routes.dashboard, "load_student", lambda *_args, **_kwargs: None
    )

    response = client.get("/franchise/57/student/999")

    assert response.status_code == 404


def test_student_page_contains_canonical_grades_and_agenda(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    response = client.get("/franchise/57/student/101")
    student = _page_data(response)["student"]

    assert response.status_code == 200
    assert student["id"] == 101
    assert student["grades"] == {"2026-07-13": {"English": 91.5}}
    assert student["agendaItems"] == [
        {"dueDate": "2026-07-15", "course": "English", "title": "Essay"}
    ]


def test_non_dev_direct_franchise_and_student_urls_remain_available(
    monkeypatch,
) -> None:
    client, _routes = _create_client(monkeypatch, environment="production")

    franchise_response = client.get("/franchise/57")
    student_response = client.get("/franchise/57/student/101")
    franchise_data = _page_data(franchise_response)
    student_data = _page_data(student_response)

    assert franchise_response.status_code == 200
    assert franchise_data["page"] == "franchise"
    assert "homeUrl" not in franchise_data
    assert student_response.status_code == 200
    assert student_data["page"] == "student"
    assert "homeUrl" not in student_data
    assert student_data["backUrl"] == "/franchise/57"


def test_jobs_api_returns_only_shaped_public_fields(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    response = client.get("/api/jobs")

    assert response.status_code == 200
    assert response.get_json() == {"jobs": [_job()]}
    body = response.get_data(as_text=True).lower()
    for forbidden in ("runner_id", "lease_token", "payload", "summary"):
        assert forbidden not in body


def test_non_dev_jobs_api_is_unauthorized_without_loading_jobs(monkeypatch) -> None:
    client, routes = _create_client(monkeypatch, environment="production")

    def unexpected_call(**_kwargs):
        raise AssertionError("non-dev jobs endpoint must not query Neon")

    monkeypatch.setattr(routes.dashboard, "load_jobs", unexpected_call)

    response = client.get("/api/jobs")

    assert response.status_code == 403
    assert "Unauthorized" in response.get_data(as_text=True)


def test_dependency_failure_is_sanitized(monkeypatch) -> None:
    client, routes = _create_client(monkeypatch)

    def fail(**_kwargs):
        raise dashboard_data.DashboardDataError("postgres://owner:secret@private")

    monkeypatch.setattr(routes.dashboard, "load_students", fail)
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 503
    assert "Dashboard temporarily unavailable" in body
    assert "owner:secret" not in body
    assert "private" not in body


def test_dashboard_responses_set_private_data_headers(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    response = client.get("/")

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
