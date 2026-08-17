from __future__ import annotations

import importlib
import json
import re
import sys
from datetime import UTC, date, datetime
from typing import Any

from ui import dashboard_data


def _student(
    student_id: int,
    *,
    franchise_id: int = 57,
    grade: object = 10,
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

    students = [
        _student(101, grade="7th"),
        _student(102, grade="10th"),
        _student(103, grade="college"),
    ]
    monkeypatch.setattr(routes.dashboard, "load_students", lambda **_kwargs: students)
    monkeypatch.setattr(routes.dashboard, "load_jobs", lambda limit=20: [_job()])
    monkeypatch.setattr(
        routes.dashboard,
        "load_franchise_name",
        lambda franchise_id: "Tutoring Club of Gilbert",
    )
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
    assert page_data["countAll"] == 3
    assert page_data["countSynced"] == 3
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

    assert response.status_code == 200
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


def test_franchise_page_payload_exposes_the_cover_name_only(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    page_data = _page_data(client.get("/franchise/57"))

    assert page_data["franchiseName"] == "Tutoring Club of Gilbert"
    assert page_data["title"] == "Franchise 57"


def test_franchise_middle_school_filter_uses_ordinal_crm_grade(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    page_data = _page_data(
        client.get("/franchise/57?grade_filter=middle_school")
    )

    assert page_data["gradeFilter"] == "middle_school"
    assert [student["id"] for student in page_data["students"]] == [101]


def test_franchise_all_and_invalid_filters_keep_unknown_grades(monkeypatch) -> None:
    client, _routes = _create_client(monkeypatch)

    all_data = _page_data(client.get("/franchise/57?grade_filter=all"))
    invalid_data = _page_data(client.get("/franchise/57?grade_filter=unsupported"))

    assert [student["id"] for student in all_data["students"]] == [101, 102, 103]
    assert invalid_data["gradeFilter"] == "all"
    assert [student["id"] for student in invalid_data["students"]] == [101, 102, 103]


def test_dev_franchise_and_student_pages_do_not_expose_overview_navigation(
    monkeypatch,
) -> None:
    client, _routes = _create_client(monkeypatch)

    franchise_data = _page_data(client.get("/franchise/57"))
    student_data = _page_data(client.get("/franchise/57/student/101"))

    assert "homeUrl" not in franchise_data
    assert "homeUrl" not in student_data
    assert student_data["backUrl"] == "/franchise/57"


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
    assert student["agendaSlots"] == []


def test_student_page_keeps_legacy_items_for_noncanonical_slot_hybrid(monkeypatch) -> None:
    client, routes = _create_client(monkeypatch)
    student = _student(101)
    student = student.__class__(
        **{
            **student.__dict__,
            "agenda": {
                "2026-07-15": [["English", "Essay"]],
                "agenda1": [],
                "agenda2": [],
            },
        }
    )
    monkeypatch.setattr(routes.dashboard, "load_student", lambda *_args: student)

    payload = _page_data(client.get("/franchise/57/student/101"))["student"]

    assert payload["agendaSlots"] == []
    assert payload["agendaItems"] == [
        {"dueDate": "2026-07-15", "course": "English", "title": "Essay"}
    ]


def test_student_page_projects_portal_slots_and_hides_legacy_items(monkeypatch) -> None:
    client, routes = _create_client(monkeypatch)
    student = _student(101)
    portal_agenda = {
        "agenda1": {
            "portal": "canvas",
            "weeks": {
                "2026-08-10": {
                    "English 11": {
                        "missing": [
                            {
                                "title": "Late reading",
                                "dueDate": "2026-08-11",
                                "dueTime": None,
                            }
                        ],
                        "low_score": [
                            {
                                "title": "Earlier quiz",
                                "dueDate": "2026-08-14",
                                "dueTime": None,
                            }
                        ],
                        "due": [
                            {
                                "title": "Reading response",
                                "dueDate": "2026-08-16",
                                "dueTime": "23:59",
                            }
                        ],
                    }
                }
            },
        },
        "agenda2": {"portal": "parentvue", "weeks": {}},
    }
    student = student.__class__(**{**student.__dict__, "agenda": portal_agenda})
    monkeypatch.setattr(routes.dashboard, "load_student", lambda *_args: student)

    payload = _page_data(client.get("/franchise/57/student/101"))["student"]

    assert payload["agendaSlots"] == [
        {
            "number": 1,
            "portal": "canvas",
            "portalLabel": "Canvas",
            "weeks": [
                {
                    "weekStart": "2026-08-10",
                    "label": "Week of Aug 10",
                    "classes": [
                        {
                            "name": "English 11",
                            "count": 3,
                            "assignments": [
                                {
                                    "status": "missing",
                                    "title": "Late reading",
                                    "dueDate": "2026-08-11",
                                    "dueTime": None,
                                    "dueDisplay": "Aug 11",
                                },
                                {
                                    "status": "low_score",
                                    "title": "Earlier quiz",
                                    "dueDate": "2026-08-14",
                                    "dueTime": None,
                                    "dueDisplay": "Aug 14",
                                },
                                {
                                    "status": "due",
                                    "title": "Reading response",
                                    "dueDate": "2026-08-16",
                                    "dueTime": "23:59",
                                    "dueDisplay": "Aug 16 · 23:59",
                                },
                            ],
                        }
                    ],
                }
            ],
        },
        {"number": 2, "portal": "parentvue", "portalLabel": "ParentVUE", "weeks": []},
    ]
    assert payload["agendaItems"] == []


def test_agenda_slots_orders_and_skips_malformed_nested_data(monkeypatch) -> None:
    _client, routes = _create_client(monkeypatch)
    student = _student(101)
    student = student.__class__(
        **{
            **student.__dict__,
            "agenda": {
                "agenda1": {
                    "portal": "untrusted portal!",
                    "weeks": {
                        "2026-08-17": {"zebra": {"missing": [], "due": []}},
                        "2026-08-03": {"Alpha": {"missing": [], "due": []}},
                        "2026-08-10": {
                            "zebra": {
                                "missing": [
                                    {
                                        "title": "Z" * 501,
                                        "dueDate": "2026-08-12",
                                        "dueTime": None,
                                    }
                                ],
                                "due": [
                                    {
                                        "title": "Later",
                                        "dueDate": "2026-08-13",
                                        "dueTime": "10:00",
                                    },
                                    {
                                        "title": "Earlier",
                                        "dueDate": "2026-08-13",
                                        "dueTime": "09:00",
                                    },
                                ],
                            },
                            "alpha": {"missing": "bad", "due": []},
                            8: {"missing": [], "due": []},
                        },
                        "2026-08-11": {"Bad": {"missing": [], "due": []}},
                        "bad-week": {},
                    },
                },
                "agenda2": {"portal": "canvas", "weeks": {}},
            },
        }
    )

    slots = routes._agenda_slots(student, today=date(2026, 8, 13))

    assert [week["weekStart"] for week in slots[0]["weeks"]] == [
        "2026-08-10",
        "2026-08-03",
        "2026-08-17",
    ]
    assert slots[0]["portal"] is None
    assert "portalLabel" not in slots[0]
    current_classes = slots[0]["weeks"][0]["classes"]
    assert [item["name"] for item in current_classes] == ["zebra"]
    assert [item["status"] for item in current_classes[0]["assignments"]] == [
        "missing",
        "due",
        "due",
    ]
    assert [item["title"] for item in current_classes[0]["assignments"]] == [
        "Z" * 500,
        "Earlier",
        "Later",
    ]
    assert slots[1] == {"number": 2, "portal": "canvas", "portalLabel": "Canvas", "weeks": []}


def test_agenda_slots_skips_rows_outside_the_enclosing_week(monkeypatch) -> None:
    _client, routes = _create_client(monkeypatch)
    student = _student(101)
    student = student.__class__(
        **{
            **student.__dict__,
            "agenda": {
                "agenda1": {
                    "portal": "canvas",
                    "weeks": {
                        "2026-08-10": {
                            "English": {
                                "missing": [],
                                "due": [
                                    {
                                        "title": "This week",
                                        "dueDate": "2026-08-13",
                                        "dueTime": None,
                                    },
                                    {
                                        "title": "Wrong week",
                                        "dueDate": "2026-09-01",
                                        "dueTime": None,
                                    },
                                ],
                            }
                        }
                    },
                },
                "agenda2": {"portal": None, "weeks": {}},
            },
        }
    )

    assignments = routes._agenda_slots(student)[0]["weeks"][0]["classes"][0][
        "assignments"
    ]

    assert [assignment["title"] for assignment in assignments] == ["This week"]


def test_agenda_slots_labels_unlisted_safe_portals(monkeypatch) -> None:
    _client, routes = _create_client(monkeypatch)
    student = _student(101)
    student = student.__class__(
        **{
            **student.__dict__,
            "agenda": {
                "agenda1": {"portal": "district_portal", "weeks": {}},
                "agenda2": {"portal": None, "weeks": {}},
            },
        }
    )

    slots = routes._agenda_slots(student)

    assert slots[0]["portalLabel"] == "District Portal"


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

    assert response.status_code == 200
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
