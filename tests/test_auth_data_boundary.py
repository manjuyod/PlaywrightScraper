from __future__ import annotations

from dataclasses import dataclass, replace
import importlib
import sys
import time
from types import SimpleNamespace

import pytest

from ui.auth.client import ClientError
from ui.auth.models import GrantIntrospection
from ui.auth.session import GradeSession, SESSION_COOKIE_NAME, sign_session


_COOKIE_SECRET = "data-boundary-test-cookie-secret"


@dataclass
class RustStub:
    grant: GrantIntrospection
    calls: list[tuple[str, str]]
    unavailable: bool = False

    def introspect_grant(
        self, grant_id: str, device_id: str
    ) -> GrantIntrospection:
        self.calls.append((grant_id, device_id))
        if self.unavailable:
            raise ClientError("introspection_failed")
        return self.grant


@dataclass
class BoundaryHarness:
    client: object
    routes: object
    rust: RustStub
    session: GradeSession
    data_calls: list[tuple[object, ...]]

    def set_session(self, grade_session: GradeSession) -> None:
        self.session = grade_session
        self.rust.grant = GrantIntrospection(
            active=True,
            grant_id=grade_session.grant_id,
            device_id=grade_session.device_id,
            crm_role=grade_session.crm_role,
            franchise_id=grade_session.franchise_id,
            permissions=grade_session.permissions,
            expires_at=grade_session.expires_at,
        )
        self.client.set_cookie(
            SESSION_COOKIE_NAME,
            sign_session(grade_session, _COOKIE_SECRET),
        )


@pytest.fixture
def boundary_harness(monkeypatch: pytest.MonkeyPatch) -> BoundaryHarness:
    for module_name in ("ui.routes", "ui.app"):
        sys.modules.pop(module_name, None)

    app_module = importlib.import_module("ui.app")
    guards = importlib.import_module("ui.auth.guards")
    routes = importlib.import_module("ui.routes")
    app_module.app.config.update(TESTING=True)

    now = int(time.time())
    grade_session = GradeSession(
        device_id="boundary-device",
        grant_id="boundary-grant",
        crm_role="2",
        franchise_id=57,
        permissions=("dashboard.read", "students.read"),
        issued_at=now - 60,
        expires_at=now + 3_600,
    )
    rust = RustStub(
        grant=GrantIntrospection(
            active=True,
            grant_id=grade_session.grant_id,
            device_id=grade_session.device_id,
            crm_role=grade_session.crm_role,
            franchise_id=grade_session.franchise_id,
            permissions=grade_session.permissions,
            expires_at=grade_session.expires_at,
        ),
        calls=[],
    )
    config = SimpleNamespace(
        grade_checker_cookie_secret=_COOKIE_SECRET,
        crm_auth_issuer="https://crm-auth.tutoringclub.com",
        crm_auth_audience="grade-checker",
    )
    monkeypatch.setattr(guards, "load_auth_config", lambda: config)
    monkeypatch.setattr(guards, "RustAuthClient", lambda _config: rust)

    data_calls: list[tuple[object, ...]] = []

    def load_students(*, franchise_id=None):
        data_calls.append(("students", franchise_id))
        return []

    def load_jobs(franchise_id=None, limit=20):
        data_calls.append(("jobs", franchise_id, limit))
        return []

    def load_franchise_name(franchise_id):
        data_calls.append(("franchise_name", franchise_id))
        return "Gilbert"

    def load_student(franchise_id, crmstudentid):
        data_calls.append(("student", franchise_id, crmstudentid))
        return None

    monkeypatch.setattr(routes.dashboard, "load_students", load_students)
    monkeypatch.setattr(routes.dashboard, "load_jobs", load_jobs)
    monkeypatch.setattr(routes.dashboard, "load_franchise_name", load_franchise_name)
    monkeypatch.setattr(routes.dashboard, "load_student", load_student)

    client = app_module.app.test_client()
    harness = BoundaryHarness(
        client=client,
        routes=routes,
        rust=rust,
        session=grade_session,
        data_calls=data_calls,
    )
    harness.set_session(grade_session)
    return harness


@pytest.mark.parametrize(
    "path",
    (
        "/",
        "/api/jobs",
        "/franchise/57",
        "/franchise/57/student/101",
    ),
)
def test_anonymous_requests_fail_before_private_data_loaders(
    boundary_harness: BoundaryHarness, path: str
) -> None:
    boundary_harness.client.delete_cookie(SESSION_COOKIE_NAME)

    response = boundary_harness.client.get(path)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/start")
    assert boundary_harness.rust.calls == []
    assert boundary_harness.data_calls == []


@pytest.mark.parametrize(
    "path",
    (
        "/",
        "/api/jobs",
        "/franchise/57",
        "/franchise/57/student/101",
    ),
)
def test_unavailable_introspection_fails_before_private_data_loaders(
    boundary_harness: BoundaryHarness, path: str
) -> None:
    boundary_harness.rust.unavailable = True

    response = boundary_harness.client.get(path)

    assert response.status_code == 503
    assert boundary_harness.rust.calls == [("boundary-grant", "boundary-device")]
    assert boundary_harness.data_calls == []


@pytest.mark.parametrize(
    "path",
    (
        "/",
        "/api/jobs",
        "/franchise/57",
        "/franchise/57/student/101",
    ),
)
def test_inactive_grant_fails_before_private_data_loaders(
    boundary_harness: BoundaryHarness, path: str
) -> None:
    boundary_harness.rust.grant = GrantIntrospection(active=False)

    response = boundary_harness.client.get(path)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/start")
    assert boundary_harness.data_calls == []


@pytest.mark.parametrize("path", ("/", "/api/jobs"))
def test_role_three_dashboard_requests_fail_before_private_data_loaders(
    boundary_harness: BoundaryHarness, path: str
) -> None:
    boundary_harness.set_session(
        replace(
            boundary_harness.session,
            crm_role="3",
            permissions=("students.read",),
        )
    )

    response = boundary_harness.client.get(path)

    assert response.status_code == 403
    assert boundary_harness.data_calls == []


@pytest.mark.parametrize(
    "path", ("/franchise/99", "/franchise/99/student/101")
)
def test_cross_franchise_requests_fail_before_private_data_loaders(
    boundary_harness: BoundaryHarness, path: str
) -> None:
    response = boundary_harness.client.get(path)

    assert response.status_code == 403
    assert boundary_harness.data_calls == []


def test_dashboard_loaders_receive_only_validated_session_franchise(
    boundary_harness: BoundaryHarness,
) -> None:
    response = boundary_harness.client.get("/?franchise_id=99")

    assert response.status_code == 200
    assert boundary_harness.data_calls == [
        ("students", 57),
        ("jobs", 57, 20),
    ]
    assert boundary_harness.rust.calls == [("boundary-grant", "boundary-device")]


def test_jobs_loader_receives_only_validated_session_franchise(
    boundary_harness: BoundaryHarness,
) -> None:
    response = boundary_harness.client.get("/api/jobs?franchise_id=99")

    assert response.status_code == 200
    assert boundary_harness.data_calls == [("jobs", 57, 20)]


def test_franchise_loaders_receive_only_validated_session_franchise(
    boundary_harness: BoundaryHarness,
) -> None:
    response = boundary_harness.client.get("/franchise/57?franchise_id=99")

    assert response.status_code == 200
    assert boundary_harness.data_calls == [
        ("franchise_name", 57),
        ("students", 57),
    ]


def test_student_loader_receives_only_validated_session_franchise(
    boundary_harness: BoundaryHarness,
) -> None:
    response = boundary_harness.client.get(
        "/franchise/57/student/101?franchise_id=99"
    )

    assert response.status_code == 404
    assert boundary_harness.data_calls == [("student", 57, 101)]


def test_guard_and_dashboard_share_request_local_claims_only(
    boundary_harness: BoundaryHarness,
) -> None:
    first = boundary_harness.client.get("/")
    second = boundary_harness.client.get("/")

    assert first.status_code == 200
    assert second.status_code == 200
    assert boundary_harness.rust.calls == [
        ("boundary-grant", "boundary-device"),
        ("boundary-grant", "boundary-device"),
    ]
