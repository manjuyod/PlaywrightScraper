from __future__ import annotations

from dataclasses import dataclass, replace
import importlib
import sys
import time
from types import SimpleNamespace

import pytest
from flask import Blueprint, Flask, jsonify

from ui.auth.client import ClientError
from ui.auth.models import AuthClaims, GrantIntrospection
from ui.auth.session import GradeSession, SESSION_COOKIE_NAME, sign_session


_COOKIE_SECRET = "guard-test-cookie-secret"


@dataclass
class DataSpy:
    calls: int = 0

    def read(self) -> None:
        self.calls += 1


@dataclass
class RustStub:
    grant: GrantIntrospection
    introspection_calls: int = 0
    raise_unavailable: bool = False

    def introspect_grant(self, grant_id: str, device_id: str) -> GrantIntrospection:
        self.introspection_calls += 1
        if self.raise_unavailable:
            raise ClientError("introspection_failed")
        assert grant_id == "grant-123"
        assert device_id == "device-456"
        return self.grant


@dataclass
class GuardHarness:
    app: Flask
    client: object
    data_spy: DataSpy
    rust_stub: RustStub
    grade_session: GradeSession

    def set_session(self, grade_session: GradeSession) -> None:
        self.grade_session = grade_session
        self.rust_stub.grant = GrantIntrospection(
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
def guard_harness(monkeypatch: pytest.MonkeyPatch) -> GuardHarness:
    from ui.auth import guards

    now = int(time.time())
    grade_session = GradeSession(
        device_id="device-456",
        grant_id="grant-123",
        crm_role="2",
        franchise_id=57,
        permissions=("dashboard.read", "students.read"),
        issued_at=now - 60,
        expires_at=now + 3_600,
    )
    rust_stub = RustStub(
        GrantIntrospection(
            active=True,
            grant_id=grade_session.grant_id,
            device_id=grade_session.device_id,
            crm_role=grade_session.crm_role,
            franchise_id=grade_session.franchise_id,
            permissions=grade_session.permissions,
            expires_at=grade_session.expires_at,
        )
    )
    config = SimpleNamespace(
        grade_checker_cookie_secret=_COOKIE_SECRET,
        crm_auth_issuer="https://crm-auth.tutoringclub.com",
        crm_auth_audience="grade-checker",
    )
    monkeypatch.setattr(guards, "load_auth_config", lambda: config)
    monkeypatch.setattr(guards, "RustAuthClient", lambda _config: rust_stub)

    app = Flask(__name__)
    app.config.update(TESTING=True)
    auth = Blueprint("auth", __name__)

    @auth.get("/auth/start")
    def start_auth():
        return "auth start"

    app.register_blueprint(auth)
    data_spy = DataSpy()

    @app.get("/")
    @guards.require_permission("dashboard.read")
    def protected():
        first = guards.current_claims()
        second = guards.current_claims()
        data_spy.read()
        return jsonify(
            {
                "typed": isinstance(first, AuthClaims),
                "same_request_claims": first is second,
                "device_id": first.sub,
                "grant_id": first.grant_id,
                "role": first.crm_role,
                "franchise_id": first.franchise_id,
                "permissions": list(first.permissions),
            }
        )

    @app.get("/api/private")
    @guards.require_permission("dashboard.read", api=True)
    def api_private():
        data_spy.read()
        return jsonify({"private": True})

    @app.get("/franchise/<int:franchise_id>")
    @guards.require_franchise("students.read")
    def franchise_private(franchise_id: int):
        data_spy.read()
        return jsonify({"franchise_id": franchise_id})

    client = app.test_client()
    harness = GuardHarness(app, client, data_spy, rust_stub, grade_session)
    harness.set_session(grade_session)
    return harness


def test_each_request_introspects_without_cross_request_cache(
    guard_harness: GuardHarness,
) -> None:
    first = guard_harness.client.get("/")
    second = guard_harness.client.get("/")

    assert first.status_code == 200
    assert second.status_code == 200
    assert guard_harness.rust_stub.introspection_calls == 2
    assert first.get_json() == {
        "typed": True,
        "same_request_claims": True,
        "device_id": "device-456",
        "grant_id": "grant-123",
        "role": "2",
        "franchise_id": 57,
        "permissions": ["dashboard.read", "students.read"],
    }


def test_unavailable_introspection_prevents_data_access(
    guard_harness: GuardHarness,
) -> None:
    guard_harness.rust_stub.raise_unavailable = True

    response = guard_harness.client.get("/")

    assert response.status_code == 503
    assert guard_harness.data_spy.calls == 0
    assert "Location" not in response.headers
    assert "introspection_failed" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"


def test_inactive_grant_clears_session_and_restarts_auth(
    guard_harness: GuardHarness,
) -> None:
    guard_harness.rust_stub.grant = GrantIntrospection(active=False)

    response = guard_harness.client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/start")
    assert guard_harness.data_spy.calls == 0
    assert _clears_session_cookie(response)
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("signed", (None, "malformed-session"))
def test_missing_or_invalid_session_clears_cookie_and_restarts_auth(
    guard_harness: GuardHarness,
    signed: str | None,
) -> None:
    guard_harness.client.delete_cookie(SESSION_COOKIE_NAME)
    if signed is not None:
        guard_harness.client.set_cookie(SESSION_COOKIE_NAME, signed)

    response = guard_harness.client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/start")
    assert guard_harness.rust_stub.introspection_calls == 0
    assert guard_harness.data_spy.calls == 0
    assert _clears_session_cookie(response)


@pytest.mark.parametrize(
    "grant_change",
    (
        {"grant_id": "other-grant"},
        {"device_id": "other-device"},
        {"crm_role": "3"},
        {"franchise_id": 99},
        {"permissions": ("students.read", "dashboard.read")},
    ),
)
def test_exact_introspection_mismatch_is_forbidden_before_data_access(
    guard_harness: GuardHarness,
    grant_change: dict[str, object],
) -> None:
    guard_harness.rust_stub.grant = replace(
        guard_harness.rust_stub.grant, **grant_change
    )

    response = guard_harness.client.get("/")

    assert response.status_code == 403
    assert guard_harness.data_spy.calls == 0
    assert _clears_session_cookie(response)
    assert response.headers["Cache-Control"] == "no-store"


def test_permission_denial_is_controlled_and_does_not_access_data(
    guard_harness: GuardHarness,
) -> None:
    guard_harness.set_session(
        replace(
            guard_harness.grade_session,
            crm_role="3",
            permissions=("students.read",),
        )
    )

    response = guard_harness.client.get("/")

    assert response.status_code == 403
    assert guard_harness.data_spy.calls == 0
    assert response.headers["Cache-Control"] == "no-store"


def test_api_permission_denial_is_json_without_data_existence_leakage(
    guard_harness: GuardHarness,
) -> None:
    guard_harness.set_session(
        replace(
            guard_harness.grade_session,
            crm_role="3",
            permissions=("students.read",),
        )
    )

    response = guard_harness.client.get("/api/private")

    assert response.status_code == 403
    assert response.is_json
    assert response.get_json() == {"error": "forbidden"}
    assert guard_harness.data_spy.calls == 0
    assert response.headers["Cache-Control"] == "no-store"


def test_franchise_guard_uses_validated_claim_not_route_or_query_input(
    guard_harness: GuardHarness,
) -> None:
    response = guard_harness.client.get("/franchise/99?franchise_id=57")

    assert response.status_code == 403
    assert guard_harness.data_spy.calls == 0
    assert response.headers["Cache-Control"] == "no-store"


def test_matching_franchise_and_student_permission_allow_data_access(
    guard_harness: GuardHarness,
) -> None:
    guard_harness.set_session(
        replace(
            guard_harness.grade_session,
            crm_role="3",
            permissions=("students.read",),
        )
    )

    response = guard_harness.client.get("/franchise/57")

    assert response.status_code == 200
    assert response.get_json() == {"franchise_id": 57}
    assert guard_harness.data_spy.calls == 1
    assert guard_harness.rust_stub.introspection_calls == 1


def test_application_registers_auth_blueprint_and_protects_auth_response() -> None:
    sys.modules.pop("ui.app", None)
    app_module = importlib.import_module("ui.app")
    app_module.app.config.update(TESTING=True)

    response = app_module.app.test_client().post("/auth/logout")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def _clears_session_cookie(response: object) -> bool:
    return any(
        header.startswith(f"{SESSION_COOKIE_NAME}=") and "Max-Age=0" in header
        for header in response.headers.getlist("Set-Cookie")
    )
