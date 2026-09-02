from __future__ import annotations

import importlib
from dataclasses import replace
from http.cookies import SimpleCookie

import pytest
from flask import Flask, make_response

from ui.auth.models import AuthClaims, GrantIntrospection


@pytest.fixture
def claims() -> AuthClaims:
    return AuthClaims(
        sub="b4fc2e7f-9ca8-44d6-95ad-09851b690c63",
        jti="b4fc2e7f-9ca8-44d6-95ad-09851b690c64",
        iss="https://crm-auth.tutoringclub.com",
        aud="grade-checker",
        grant_id="b4fc2e7f-9ca8-44d6-95ad-09851b690c65",
        crm_role="3",
        franchise_id=16,
        permissions=("students.read",),
        iat=1_900_000_000,
        nbf=1_900_000_000,
        exp=1_900_001_500,
    )


def _grant(claims: AuthClaims) -> GrantIntrospection:
    return GrantIntrospection(
        active=True,
        grant_id=claims.grant_id,
        device_id=claims.sub,
        crm_role=claims.crm_role,
        franchise_id=claims.franchise_id,
        permissions=claims.permissions,
        expires_at=1_900_028_800,
    )


def _session_module():
    try:
        return importlib.import_module("ui.auth.session")
    except ModuleNotFoundError:
        pytest.fail("Grade session module is missing")


def test_create_session_uses_only_minimum_claims_and_grant_expiry(
    claims: AuthClaims,
) -> None:
    session = _session_module()

    grade_session = session.create_session(claims, _grant(claims), now=1_900_000_001)

    assert grade_session == session.GradeSession(
        device_id=claims.sub,
        grant_id=claims.grant_id,
        crm_role="3",
        franchise_id=16,
        permissions=("students.read",),
        issued_at=1_900_000_001,
        expires_at=1_900_028_800,
    )
    assert not hasattr(grade_session, "assertion")
    assert not hasattr(grade_session, "actor_ref")
    assert not hasattr(grade_session, "authorization_code")
    assert grade_session.expires_at > claims.exp


@pytest.mark.parametrize(
    "grant_change",
    (
        {"active": False},
        {"grant_id": "wrong-grant"},
        {"device_id": "wrong-device"},
        {"crm_role": "2"},
        {"franchise_id": 99},
        {"permissions": ("dashboard.read", "students.read")},
        {"expires_at": None},
        {"expires_at": 1_900_000_001},
    ),
)
def test_create_session_requires_exact_active_introspection_match(
    claims: AuthClaims,
    grant_change: dict[str, object],
) -> None:
    session = _session_module()

    with pytest.raises(ValueError, match="grant"):
        session.create_session(
            claims,
            replace(_grant(claims), **grant_change),
            now=1_900_000_001,
        )


def test_signed_session_rejects_tampering_and_expiry(claims: AuthClaims) -> None:
    session = _session_module()
    grade_session = session.create_session(claims, _grant(claims), now=1_900_000_001)
    signed = session.sign_session(grade_session, "cookie-secret")

    assert session.load_session(
        signed, "cookie-secret", now=1_900_028_799
    ) == grade_session
    with pytest.raises(ValueError, match="session"):
        session.load_session(
            f"{signed[:-1]}{'A' if signed[-1] != 'A' else 'B'}",
            "cookie-secret",
            now=1_900_000_002,
        )
    with pytest.raises(ValueError, match="session"):
        session.load_session(signed, "cookie-secret", now=1_900_028_800)


def test_session_cookie_is_grant_clamped_host_only_secure_http_only_and_lax(
    claims: AuthClaims,
) -> None:
    session = _session_module()
    grade_session = session.create_session(claims, _grant(claims), now=1_900_000_001)
    app = Flask(__name__)
    with app.app_context():
        response = make_response("")
        session.set_session_cookie(
            response, "signed-value", grade_session, now=1_900_000_001
        )

    header = response.headers.getlist("Set-Cookie")[0]
    cookie = SimpleCookie()
    cookie.load(header)
    morsel = cookie[session.SESSION_COOKIE_NAME]

    assert morsel.value == "signed-value"
    assert morsel["path"] == "/"
    assert morsel["secure"] is True
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "Lax"
    assert morsel["max-age"] == str(1_900_028_800 - 1_900_000_001)
    assert morsel["domain"] == ""
