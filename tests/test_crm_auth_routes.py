from __future__ import annotations

import importlib
import os
import secrets
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from db import Student
from ui.auth import CrmLoginResult


def _configure_crm_env() -> None:
    os.environ["CRMSrvAddress"] = "crm-host"
    os.environ["CRMSrvDbQA"] = "crm-db"
    os.environ["CRMSrvUs"] = "crm-user"
    os.environ["CRMSrvPs"] = "crm-pass"


def _load_fresh_ui_modules() -> tuple[object, object]:
    for name in list(sys.modules):
        if name.startswith("ui.app") or name.startswith("ui.routes"):
            del sys.modules[name]
    os.environ["SESSION_SECRET"] = "test-session-secret-with-enough-length"
    os.environ["INTERNAL_KEY"] = "internal-key"
    app_module = importlib.import_module("ui.app")
    routes = importlib.import_module("ui.routes")
    return app_module, routes


class _FakeCursor:
    def __init__(
        self,
        row_data: dict[str, object] | None = None,
        result_sets: list[tuple[list[str], list[tuple[object, ...]]]] | None = None,
    ):
        self._result_sets = result_sets
        self._set_index = 0
        self._row_index = 0
        self.description = [("Role",), ("FranchiseID",), ("ID",)]
        self._calls: list[tuple[str, tuple[object, ...]]] = []
        self._row = self._to_row(row_data) if row_data else None
        self._closed = False
        if self._result_sets:
            self.description = [(name,) for name in self._result_sets[0][0]]

    def _to_row(self, row_data: dict[str, object]) -> tuple[object, object, object]:
        return (
            row_data.get("Role"),
            row_data.get("FranchiseID"),
            row_data.get("ID"),
        )

    def execute(self, query: str, *params: object) -> "_FakeCursor":
        self._calls.append((query, params))
        return self

    def fetchone(self) -> tuple[object, object, object] | None:
        if self._result_sets is not None:
            rows = self._result_sets[self._set_index][1]
            if self._row_index >= len(rows):
                return None
            row = rows[self._row_index]
            self._row_index += 1
            return row

        if self._row is None:
            return None
        row = self._row
        self._row = None
        return row

    def nextset(self) -> bool:
        if self._result_sets is None:
            return False
        if self._set_index + 1 >= len(self._result_sets):
            return False
        self._set_index += 1
        self._row_index = 0
        self.description = [(name,) for name in self._result_sets[self._set_index][0]]
        return True

    def close(self) -> None:
        self._closed = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


class _FakePyodbc:
    def __init__(self, cursor: _FakeCursor):
        self.connect_calls: list[str] = []
        self.cursor_for_connection = cursor

    def connect(self, connection_string: str) -> _FakeConnection:
        self.connect_calls.append(connection_string)
        return _FakeConnection(self.cursor_for_connection)


class _FailingPyodbc:
    class Error(Exception):
        pass

    def connect(self, _connection_string: str) -> _FakeConnection:
        raise self.Error("database unavailable")


@dataclass
class _AuthInput:
    row_data: dict[str, object] | None


def test_crm_login_accepts_roles_and_fid() -> None:
    _configure_crm_env()
    fake_cursor = _FakeCursor({"Role": 2, "FranchiseID": 33})
    fake_pyodbc = _FakePyodbc(fake_cursor)

    with patch("ui.auth.pyodbc", fake_pyodbc):
        result = importlib.import_module("ui.auth").crm_login("alice", "secret")

    assert result == CrmLoginResult(authenticated=True, role=2, franchise_id=33)


def test_crm_login_rejects_other_roles_and_empty_fid() -> None:
    _configure_crm_env()
    rows = (
        _AuthInput({"Role": 1, "FranchiseID": 33}),
        _AuthInput({"Role": 2, "FranchiseID": 0}),
    )
    for row in rows:
        fake_cursor = _FakeCursor(row.row_data)
        fake_pyodbc = _FakePyodbc(fake_cursor)
        with patch("ui.auth.pyodbc", fake_pyodbc):
            result = importlib.import_module("ui.auth").crm_login("alice", "secret")

        assert not result.authenticated


def test_crm_login_uses_fallback_fid_column() -> None:
    _configure_crm_env()
    fake_cursor = _FakeCursor({"Role": 3, "ID": 88})
    fake_pyodbc = _FakePyodbc(fake_cursor)

    with patch("ui.auth.pyodbc", fake_pyodbc):
        result = importlib.import_module("ui.auth").crm_login("alice", "secret")

    assert result == CrmLoginResult(authenticated=True, role=3, franchise_id=88)


def test_crm_login_combines_role_and_franchise_from_separate_result_sets() -> None:
    _configure_crm_env()
    fake_cursor = _FakeCursor(
        result_sets=[
            (["Role"], [(2,)]),
            (["ID", "Name"], [(74, "Franchise 74")]),
        ]
    )
    fake_pyodbc = _FakePyodbc(fake_cursor)

    with patch("ui.auth.pyodbc", fake_pyodbc):
        result = importlib.import_module("ui.auth").crm_login("alice", "secret")

    assert result.authenticated is True
    assert result.role == 2
    assert result.franchise_id == 74
    assert result.display_name == "Franchise 74"


def test_crm_login_returns_generic_failure_for_sql_errors() -> None:
    _configure_crm_env()

    with patch("ui.auth.pyodbc", _FailingPyodbc()):
        result = importlib.import_module("ui.auth").crm_login("alice", "secret")

    assert not result.authenticated


def test_crm_login_executes_parameterized_usp_login() -> None:
    _configure_crm_env()
    fake_cursor = _FakeCursor({"Role": 2, "FranchiseID": 19})
    fake_pyodbc = _FakePyodbc(fake_cursor)

    with patch("ui.auth.pyodbc", fake_pyodbc):
        importlib.import_module("ui.auth").crm_login("alice", "secret")

    query, params = fake_cursor._calls[0]
    assert "usp_login" in query
    assert "?" in query
    assert params == ("alice", "secret")


def test_crm_connection_prefers_primary_db() -> None:
    os.environ["CRMSrvDb"] = "crm-primary-db"
    os.environ["CRMSrvDbQA"] = "crm-qa-db"

    connect_string = importlib.import_module("ui.auth")._connect_string()

    assert "DATABASE=crm-primary-db;" in connect_string
    assert "DATABASE=crm-qa-db;" not in connect_string


def test_crm_connection_requires_validated_tls() -> None:
    connect_string = importlib.import_module("ui.auth")._connect_string()

    assert "Encrypt=yes" in connect_string
    assert "TrustServerCertificate=no" in connect_string


def test_crm_connection_falls_back_to_qa_db() -> None:
    _configure_crm_env()
    os.environ.pop("CRMSrvDb", None)

    connect_string = importlib.import_module("ui.auth")._connect_string()

    assert "DATABASE=crm-db;" in connect_string


def test_crm_connection_does_not_use_empty_db_and_fails_closed() -> None:
    _configure_crm_env()
    original_db = os.environ.pop("CRMSrvDb", None)
    original_db_qa = os.environ.pop("CRMSrvDbQA", None)
    try:
        with patch("ui.auth.pyodbc", _FailingPyodbc()):
            result = importlib.import_module("ui.auth").crm_login("alice", "secret")

        assert not result.authenticated
    finally:
        if original_db is not None:
            os.environ["CRMSrvDb"] = original_db
        else:
            os.environ.pop("CRMSrvDb", None)
        if original_db_qa is not None:
            os.environ["CRMSrvDbQA"] = original_db_qa
        else:
            os.environ.pop("CRMSrvDbQA", None)


def test_crm_connection_trust_server_certificate_toggle_allows_configured_values() -> None:
    _configure_crm_env()
    for value in ("1", "true", "yes"):
        os.environ["CRM_TRUST_SERVER_CERTIFICATE"] = value
        connect_string = importlib.import_module("ui.auth")._connect_string()
        assert "TrustServerCertificate=yes" in connect_string
    os.environ.pop("CRM_TRUST_SERVER_CERTIFICATE", None)


def test_login_redirects_root_without_headers() -> None:
    app_module, routes = _load_fresh_ui_modules()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        response = client.get("/")
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


def test_login_health_credentials_start_fresh_health_session() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 21
            sess["csrf_token"] = "token-1"

        with patch("ui.routes.crm_login") as crm_login:
            response = client.post(
                "/login",
                data={
                    "username": "test@test.com",
                    "password": "test",
                    "csrf_token": "token-1",
                },
            )

        assert crm_login.call_count == 0
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/health")

    with client.session_transaction() as sess:
        assert sess.get("session_type") == "health_test"
        assert not sess.get("franchise_id")
        assert sess.get("authorized") is True


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
    assert app_module.app.config["SESSION_FILE_MODE"] == 0o600
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
        "portal_username": "ada-login",
        "portal_password": "student-secret",
        "alt_portal_url": "https://alt.example.test",
        "alt_portal_username": "alt-login",
        "alt_portal_password": "alt-secret",
    }
    data.update(overrides)
    return Student(**data)


def test_franchise_page_does_not_render_student_portal_credentials() -> None:
    app, routes = _create_auth_client()
    student = _sample_student()
    routes.db.get_students = lambda _franchise_id: [student]

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


def test_edit_student_blank_credentials_keep_existing_values() -> None:
    app, routes = _create_auth_client()
    existing = _sample_student()
    captured: dict[str, object] = {}

    routes.get_students_from_session = lambda _franchise_id: [existing]
    routes.db.update_student = lambda student_id, student, master_key: captured.update(
        {"student_id": student_id, "student": student, "master_key": master_key}
    )

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = "token-1"
            sess["dek"] = b"0" * 32

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

    assert response.status_code == 302
    updated = captured["student"]
    assert isinstance(updated, Student)
    assert updated.portal_username == "ada-login"
    assert updated.portal_password == "student-secret"
    assert updated.alt_portal_username == "alt-login"
    assert updated.alt_portal_password == "alt-secret"


def test_bulk_delete_rejects_student_ids_outside_loaded_franchise() -> None:
    app, routes = _create_auth_client()
    deleted: list[list[int]] = []
    routes.get_students_from_session = lambda _franchise_id: [_sample_student(id=123)]
    routes.db.delete_students = lambda student_ids: deleted.append(student_ids)

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

    assert response.status_code == 403
    assert deleted == []


def test_student_view_requires_login() -> None:
    app, _ = _create_auth_client()
    with app.test_client() as client:
        response = client.get("/franchise/19/student/42")
        assert response.status_code == 403


def test_student_view_returns_404_when_student_not_in_session_franchise() -> None:
    app, _ = _create_auth_client()
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
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        def fail_global_fetch(*_args, **_kwargs):
            raise AssertionError("global student lookup must not be used")

        routes.db.get_student = fail_global_fetch
        routes.db.get_students = lambda franchise_id: []

        response = client.get("/franchise/11/student/999")
        assert response.status_code == 404


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

        response = client.get("/status/12")
        assert response.status_code == 403


def test_status_can_be_reached_for_matching_job() -> None:
    app, routes = _create_auth_client()
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authorized"] = True
            sess["session_type"] = "crm"
            sess["franchise_id"] = 11
            sess["csrf_token"] = secrets.token_urlsafe(12)

        routes.get_status = lambda _job_id: SimpleNamespace(
            total=1,
            step=1,
            steps=1,
            pct=1.0,
        )
        response = client.get("/status/11")
        assert response.status_code == 200
        assert response.json["total"] == 1


def test_health_test_session_is_limited_to_health_and_logout() -> None:
    app, routes = _create_auth_client()
    routes.db.fetch = lambda _query: [{"franchiseid": 11, "status": "synced"}]
    routes.filter_group = lambda items, key, value: [
        item for item in items if getattr(item, key, None) == value
    ]
    routes.check_students_status = lambda _students: {"bad_logins": 0}
    routes.db.get_active_franchises = lambda: []

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
            sess["session_type"] = "dev"
            sess["csrf_token"] = secrets.token_urlsafe(12)

        response = client.post("/franchise/11", data={"run_scraper": "1"})
        assert response.status_code == 403
