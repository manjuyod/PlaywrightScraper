from __future__ import annotations

import importlib
import os
import secrets
import sys
from unittest.mock import patch

from ui.report_models import Student
from ui.auth import CrmLoginResult


def _load_fresh_ui_modules() -> tuple[object, object]:
    for name in list(sys.modules):
        if name.startswith("ui.app") or name.startswith("ui.routes"):
            del sys.modules[name]
    os.environ["SESSION_SECRET"] = "test-session-secret-with-enough-length"
    os.environ["INTERNAL_KEY"] = "internal-key"
    app_module = importlib.import_module("ui.app")
    routes = importlib.import_module("ui.routes")
    return app_module, routes


def test_login_redirects_root_without_headers() -> None:
    app_module, routes = _load_fresh_ui_modules()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")


def test_login_redirects_root_even_with_dev_bypass_enabled() -> None:
    with patch.dict(os.environ, {"DEV_BYPASS": "1"}):
        app_module, routes = _load_fresh_ui_modules()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")


def test_login_redirects_root_even_with_internal_handoff_headers() -> None:
    app_module, routes = _load_fresh_ui_modules()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        response = client.get(
            "/",
            headers={"X-Franchise": "21", "X-Internal-Key": "internal-key"},
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")


def _create_auth_client():
    app_module, routes = _load_fresh_ui_modules()
    app_module.app.config["TESTING"] = True
    return app_module.app, routes


def test_login_success_sets_crm_session_state() -> None:
    app, routes = _create_auth_client()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = "token-1"

    routes.crm_login = lambda *a, **k: CrmLoginResult(
        authenticated=True,
        role=2,
        franchise_id=21,
    )
    response = client.post(
        "/login",
        data={"username": "alice", "password": "secret", "csrf_token": "token-1"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/franchise/21")
    with client.session_transaction() as sess:
        assert sess["session_type"] == "crm"
        assert sess["franchise_id"] == 21
        assert sess["role"] == 2


def test_login_franchise_one_starts_fresh_health_session() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 21
            sess["csrf_token"] = "token-1"

        with patch(
            "ui.routes.crm_login",
            return_value=CrmLoginResult(
                authenticated=True,
                role=2,
                franchise_id=1,
            ),
        ) as crm_login:
            response = client.post(
                "/login",
                data={
                    "username": "test@test.com",
                    "password": "test",
                    "csrf_token": "token-1",
                },
            )

        assert crm_login.call_count == 1
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/health")

    with client.session_transaction() as sess:
        assert sess.get("session_type") == "health_test"
        assert not sess.get("franchise_id")
        assert sess.get("authorized") is None
        assert len(sess.get("user_fingerprint", "")) == 64


def test_login_get_with_unknown_session_type_shows_login() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "internal"
            sess["csrf_token"] = "token-1"

        response = client.get("/login")

        assert response.status_code == 200
        with client.session_transaction() as sess:
            assert not sess.get("authorized")


def test_failed_login_attempts_are_rate_limited() -> None:
    app, routes = _create_auth_client()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = "token-1"

    routes.crm_login = lambda *a, **k: CrmLoginResult(authenticated=False)

    for _ in range(5):
        response = client.post(
            "/login",
            data={"username": "alice", "password": "wrong", "csrf_token": "token-1"},
        )
        assert response.status_code == 302

    response = client.post(
        "/login",
        data={"username": "alice", "password": "wrong", "csrf_token": "token-1"},
    )

    assert response.status_code == 429


def test_session_security_config_matches_deployment_mode() -> None:
    app_module, _ = _load_fresh_ui_modules()

    assert app_module.app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app_module.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app_module.app.config["SESSION_COOKIE_SECURE"] is False
    assert "SESSION_FILE_MODE" not in app_module.app.config
    assert "SESSION_FILE_DIR" not in app_module.app.config
    assert app_module.app.config["PERMANENT_SESSION_LIFETIME"].total_seconds() <= 8 * 60 * 60


def test_authenticated_session_is_permanent_for_server_side_expiry() -> None:
    app, routes = _create_auth_client()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf_token"] = "token-1"

    routes.crm_login = lambda *a, **k: CrmLoginResult(
        authenticated=True,
        role=2,
        franchise_id=21,
    )
    client.post(
        "/login",
        data={"username": "alice", "password": "secret", "csrf_token": "token-1"},
    )

    with client.session_transaction() as sess:
        assert sess.permanent is True


def _sample_student(**overrides: object) -> Student:
    data = {
        "id": 123,
        "grade_level": 10,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "grades": {},
        "status": "synced",
        "portal": "homeaccess",
        "portal_url": "https://portal.example.test",
        "alt_portal_url": "https://alt.example.test",
    }
    data.update(overrides)
    return Student(**data)


def _filter_fixture_students() -> list[Student]:
    return [
        _sample_student(id=101, grade_level=6, first_name="Maya", last_name="Six"),
        _sample_student(id=102, grade_level=8, first_name="Eli", last_name="Eight"),
        _sample_student(id=201, grade_level=9, first_name="Noah", last_name="Nine"),
        _sample_student(id=202, grade_level=12, first_name="Zoe", last_name="Twelve"),
    ]


def _api_student(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "crmstudentid": 123,
        "franchiseid": 11,
        "firstname": "Ada",
        "lastname": "Lovelace",
        "grade": 10,
        "portal1": "https://portal.example.test",
        "has_portal1_username": True,
        "has_portal1_password": True,
        "portal2": "https://alt.example.test",
        "has_portal2_username": True,
        "has_portal2_password": True,
        "yearstart": 2026,
        "yearend": 2027,
        "weeklydata": {},
        "portal": "homeaccess",
        "passwordgood": True,
        "status": "synced",
        "error_msg": None,
        "track_agenda": False,
        "weekly_agenda": {},
    }
    data.update(overrides)
    return data


def test_franchise_page_fetches_students_through_api(monkeypatch) -> None:
    app, routes = _create_auth_client()
    calls: list[tuple[str, str]] = []

    def fake_request_json(method, path, **_kwargs):
        calls.append((method, path))
        if path == "/api/students":
            return {"students": [_api_student(firstname="Maya", crmstudentid=321)]}
        if path == "/api/jobs/current":
            return {"jobs": []}
        raise AssertionError(path)

    monkeypatch.setattr(routes.grade_api, "request_json", fake_request_json)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["role"] = 2
            sess["csrf_token"] = "token-1"

        response = client.get("/franchise/11")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Maya" in body
    assert ("GET", "/api/students") in calls


def test_franchise_manual_pull_uses_api_job_without_local_runner(monkeypatch) -> None:
    app, routes = _create_auth_client()
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    job_id = "00000000-0000-0000-0000-000000000123"

    def fake_request_json(method, path, **kwargs):
        calls.append((method, path, kwargs.get("payload")))
        if path == "/api/students":
            return {"students": [_api_student(crmstudentid=123)]}
        if path == "/api/jobs/current":
            return {"jobs": []}
        if path == "/api/jobs/manual-pull":
            return {"job_id": job_id, "status": "queued"}
        raise AssertionError(path)

    monkeypatch.setattr(routes.grade_api, "request_json", fake_request_json)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["role"] = 2
            sess["csrf_token"] = "token-1"

        response = client.post(
            "/franchise/11",
            data={"csrf_token": "token-1", "run_scraper": "1"},
        )

    assert response.status_code == 200
    assert ("POST", "/api/jobs/manual-pull", {"kind": "grade"}) in calls
    with client.session_transaction() as sess:
        assert sess["active_job_ids"] == {"grade": job_id}


def test_franchise_page_middle_school_filter_renders_only_grades_6_to_8() -> None:
    app, routes = _create_auth_client()
    routes.get_dashboard_students = lambda _franchise_id: _filter_fixture_students()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "token-1"

        response = client.get("/franchise/11?grade_filter=middle_school")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Maya" in body
    assert "Eli" in body
    assert "Noah" not in body
    assert "Zoe" not in body


def test_franchise_page_high_school_filter_renders_only_grades_9_to_12() -> None:
    app, routes = _create_auth_client()
    routes.get_dashboard_students = lambda _franchise_id: _filter_fixture_students()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "token-1"

        response = client.get("/franchise/11?grade_filter=high_school")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Maya" not in body
    assert "Eli" not in body
    assert "Noah" in body
    assert "Zoe" in body


def test_franchise_page_invalid_or_missing_grade_filter_renders_all_students() -> None:
    app, routes = _create_auth_client()
    routes.get_dashboard_students = lambda _franchise_id: _filter_fixture_students()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "token-1"

        missing_response = client.get("/franchise/11")
        invalid_response = client.get("/franchise/11?grade_filter=elementary")

    for response in (missing_response, invalid_response):
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "Maya" in body
        assert "Eli" in body
        assert "Noah" in body
        assert "Zoe" in body


def test_franchise_page_grade_filter_accepts_neon_grade_labels() -> None:
    app, routes = _create_auth_client()
    routes.get_dashboard_students = lambda _franchise_id: [
        _sample_student(id=301, grade_level="6th", first_name="Iris"),
        _sample_student(id=302, grade_level="8th", first_name="Omar"),
        _sample_student(id=303, grade_level="10th", first_name="Pia"),
        _sample_student(id=304, grade_level="", first_name="Quinn"),
    ]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "token-1"

        response = client.get("/franchise/11?grade_filter=middle_school")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Iris" in body
    assert "Omar" in body
    assert "Pia" not in body
    assert "Quinn" not in body


def test_franchise_page_filtered_scraper_post_uses_full_franchise_count() -> None:
    app, routes = _create_auth_client()
    captured_pulls: list[tuple[int, str]] = []
    routes.get_dashboard_students = lambda _franchise_id: _filter_fixture_students()
    routes.create_manual_pull = lambda franchise_id, kind="grade": captured_pulls.append(
        (franchise_id, kind)
    )

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "token-1"

        response = client.post(
            "/franchise/11?grade_filter=middle_school",
            data={"csrf_token": "token-1", "run_scraper": "1"},
    )

    assert response.status_code == 200
    assert captured_pulls == [(11, "grade")]


def test_franchise_page_does_not_render_student_portal_credentials(monkeypatch) -> None:
    app, routes = _create_auth_client()
    student = _sample_student()
    monkeypatch.setattr(routes, "get_dashboard_students", lambda _franchise_id: [student])

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        response = client.get("/franchise/11")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "student-secret" not in body
    assert "alt-secret" not in body
    assert "ada-login" not in body
    assert "alt-login" not in body


def test_student_page_does_not_render_student_portal_credentials(monkeypatch) -> None:
    app, routes = _create_auth_client()
    student = _sample_student()
    monkeypatch.setattr(routes, "get_dashboard_students", lambda _franchise_id: [student])

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        response = client.get("/franchise/11/student/123")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "student-secret" not in body
    assert "alt-secret" not in body
    assert "ada-login" not in body
    assert "alt-login" not in body


def test_dashboard_student_edit_action_is_removed() -> None:
    app, routes = _create_auth_client()
    routes.get_dashboard_students = lambda _franchise_id: [_sample_student()]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "token-1"

        response = client.post(
            "/franchise/11?student_id=123",
            data={
                "csrf_token": "token-1",
                "edit_student": "1",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "grade": "10",
                "portal_url": "https://portal.example.test",
                "portal_username": "",
                "portal_password": "",
                "alt_portal_url": "https://alt.example.test",
                "alt_portal_username": "",
                "alt_portal_password": "",
            },
        )

    assert response.status_code == 400
    assert not hasattr(routes, "update_dashboard_student")


def test_dashboard_student_delete_action_is_removed() -> None:
    app, routes = _create_auth_client()
    routes.get_dashboard_students = lambda _franchise_id: [_sample_student(id=123)]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "token-1"

        response = client.post(
            "/franchise/11",
            data={
                "csrf_token": "token-1",
                "delete_students": "1",
                "student_id": ["123", "999"],
            },
        )

    assert response.status_code == 400
    assert not hasattr(routes, "delete_dashboard_student")


def test_student_view_requires_login() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        response = client.get("/franchise/19/student/42")
        assert response.status_code == 403


def test_student_view_returns_404_when_student_not_in_session_franchise() -> None:
    app, routes = _create_auth_client()
    calls: list[int] = []
    routes.get_dashboard_students = lambda franchise_id: calls.append(franchise_id) or []
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)
            sess["students_11"] = [{"id": 123, "firstname": "Ada"}]

        response = client.get("/franchise/11/student/999")
        assert response.status_code == 404


def test_student_view_does_not_fetch_global_student_on_fresh_session() -> None:
    app, routes = _create_auth_client()
    calls: list[int] = []
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        routes.get_dashboard_students = lambda franchise_id: calls.append(franchise_id) or []

        response = client.get("/franchise/11/student/999")
        assert response.status_code == 404
        assert calls == [11]
        assert calls == [11]


def test_student_view_loads_fresh_session_students_through_api() -> None:
    app, routes = _create_auth_client()
    routes.get_dashboard_students = lambda _franchise_id: [
        _sample_student(id=123, first_name="ApiLoaded")
    ]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        response = client.get("/franchise/11/student/123")

    assert response.status_code == 200
    assert "ApiLoaded" in response.get_data(as_text=True)


def test_student_manual_pull_uses_api_job_without_local_runner() -> None:
    app, routes = _create_auth_client()
    captured_pulls: list[tuple[int, str, int | None]] = []
    routes.get_dashboard_students = lambda _franchise_id: [_sample_student(id=123)]
    routes.create_manual_pull = (
        lambda franchise_id, kind="grade", student_id=None: captured_pulls.append(
            (franchise_id, kind, student_id)
        )
    )

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "token-1"

        response = client.post(
            "/franchise/11/student/123",
            data={"csrf_token": "token-1", "run_scraper": "1"},
        )

    assert response.status_code == 302
    assert captured_pulls == [(11, "grade", 123)]


def test_crm_session_isolated_to_its_franchise() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        response = client.get("/franchise/12")
        assert response.status_code == 403


def test_health_forbidden_for_crm_sessions() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        response = client.get("/health")
        assert response.status_code == 403


def test_status_restricted_by_job_franchise() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        response = client.get(
            "/status/00000000-0000-0000-0000-000000000012"
        )
        assert response.status_code == 403


def test_status_can_be_reached_for_matching_job() -> None:
    app, routes = _create_auth_client()
    job_id = "00000000-0000-0000-0000-000000000011"
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)
            sess["active_job_ids"] = {"grade": job_id}

        routes.get_dashboard_job = lambda _job_id: {
            "id": job_id,
            "kind": "grade",
            "status": "running",
            "scope": {"franchise_id": 11, "student_id": None},
            "progress": {"total": 4, "attempted": 2, "success": 2, "errors": 0},
        }
        response = client.get(f"/status/{job_id}")
        assert response.status_code == 200
        assert response.json == {
            "total": 4,
            "step": 2,
            "steps": 4,
            "pct": 0.5,
        }


def test_health_test_session_is_limited_to_health_and_logout() -> None:
    app, routes = _create_auth_client()
    routes.get_dashboard_health = lambda: {
        "health": [{"id": 11, "synced": 1, "total": 1}],
        "count_all": 1,
        "count_synced": 1,
        "count_bad_logins": 0,
        "jobs": [],
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "health_test"
            sess["csrf_token"] = "token-1"

        response = client.get("/health")
        assert response.status_code == 200

        response = client.post("/logout", data={"csrf_token": "token-1"})
        assert response.status_code == 302

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "health_test"
            sess["csrf_token"] = "token-1"

        response = client.get("/franchise/11")
        assert response.status_code == 403

        response = client.get("/status/11")
        assert response.status_code == 403


def test_legacy_dev_and_internal_sessions_do_not_authorize_protected_routes() -> None:
    app, _ = _create_auth_client()
    for session_type in ("dev", "internal"):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["authorized"] = True
                sess["session_type"] = session_type
                sess["csrf_token"] = "token-1"

            response = client.get("/status/11")
            assert response.status_code == 403


def test_logout_clears_session() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "logout-token"

        response = client.post("/logout", data={"csrf_token": "logout-token"})
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")
        with client.session_transaction() as sess:
            assert not sess.get("authorized")


def test_csrf_validation_blocks_mutating_requests() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        response = client.post("/franchise/11", data={"run_scraper": "1"})
        assert response.status_code == 403
