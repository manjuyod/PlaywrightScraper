from __future__ import annotations

import importlib
from dataclasses import replace
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask

from ui.auth import routes, transaction
from ui.auth.client import ClientError
from ui.auth.models import AuthClaims, GrantIntrospection
from ui.auth.routes import bp


SESSION_COOKIE_NAME = "__Host-grade_checker_session"


@pytest.fixture
def config_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "CRM_AUTH_BASE_URL": "https://crm-auth.tutoringclub.com",
        "CRM_AUTH_CLIENT_ID": "grade-checker",
        "CRM_AUTH_CLIENT_SECRET": "test-client-secret",
        "CRM_AUTH_ISSUER": "https://crm-auth.tutoringclub.com",
        "CRM_AUTH_AUDIENCE": "grade-checker",
        "CRM_AUTH_JWKS_URL": "https://crm-auth.tutoringclub.com/.well-known/jwks.json",
        "CRM_DEVICE_AUTHORIZE_URL": "https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx",
        "GRADE_CHECKER_CALLBACK_URL": "https://grades.tutoringclub.com/auth/callback",
        "GRADE_CHECKER_COOKIE_SECRET": "test-cookie-secret",
        "AUTH_TRANSACTION_TTL_SECONDS": "600",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def app(config_environment: None) -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=True, SERVER_NAME="grades.tutoringclub.com")
    app.register_blueprint(bp)
    return app


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


class FakeRustClient:
    def __init__(
        self,
        claims: AuthClaims,
        grant: GrantIntrospection,
        events: list[tuple[str, ...]],
        error: ClientError | None = None,
    ) -> None:
        self.claims = claims
        self.grant = grant
        self.events = events
        self.error = error

    def redeem_authorization_code(self, code: str, code_verifier: str) -> AuthClaims:
        self.events.append(("redeem", code, code_verifier))
        if self.error is not None:
            raise self.error
        return self.claims

    def introspect_grant(self, grant_id: str, device_id: str) -> GrantIntrospection:
        self.events.append(("introspect", grant_id, device_id))
        if self.error is not None:
            raise self.error
        return self.grant


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
    grant: GrantIntrospection,
    events: list[tuple[str, ...]],
    *,
    error: ClientError | None = None,
) -> None:
    fake = FakeRustClient(claims, grant, events, error)
    monkeypatch.setattr(routes, "RustAuthClient", lambda _config: fake, raising=False)


def _start(client, monkeypatch: pytest.MonkeyPatch):
    values = iter(("state-token", "v" * 43))
    monkeypatch.setattr(transaction.secrets, "token_urlsafe", lambda _size: next(values))
    return client.get("/auth/start", base_url="https://grades.tutoringclub.com")


def _cookie_from_headers(response, name: str) -> str:
    for header in response.headers.getlist("Set-Cookie"):
        cookie = SimpleCookie()
        cookie.load(header)
        if name in cookie:
            return cookie[name].value
    raise AssertionError(f"cookie {name} was not set")


def _assert_cookie_cleared(response, name: str) -> None:
    headers = [
        header
        for header in response.headers.getlist("Set-Cookie")
        if header.startswith(f"{name}=")
    ]
    assert len(headers) == 1
    assert "Max-Age=0" in headers[0]
    assert "Secure" in headers[0]
    assert "HttpOnly" in headers[0]
    assert "Path=/" in headers[0]
    assert "SameSite=Lax" in headers[0]
    assert "Domain=" not in headers[0]


def _assert_callback_rejected_before_rust(
    response, events: list[tuple[str, ...]], sensitive_value: str
) -> None:
    assert response.status_code == 400
    assert events == []
    _assert_cookie_cleared(response, transaction.TRANSACTION_COOKIE_NAME)
    assert not any(
        header.startswith(f"{SESSION_COOKIE_NAME}=")
        for header in response.headers.getlist("Set-Cookie")
    )
    assert b"Authentication could not be completed." in response.data
    assert sensitive_value.encode() not in response.data


def test_start_uses_fixed_crm_url_one_state_and_s256_challenge(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Published independently in RFC 7636 Appendix B, not derived from production.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    values = iter(("state-token", verifier))
    monkeypatch.setattr(transaction.secrets, "token_urlsafe", lambda _size: next(values))
    response = app.test_client().get(
        "/auth/start", base_url="https://grades.tutoringclub.com"
    )
    location = response.headers["Location"]
    parsed = urlsplit(location)
    query = parse_qs(parsed.query, strict_parsing=True)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx"
    )
    assert query == {
        "state": ["state-token"],
        "code_challenge": ["E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"],
        "code_challenge_method": ["S256"],
    }
    assert location.count("state=") == 1
    assert location.count("code_challenge=") == 1

    header = response.headers.getlist("Set-Cookie")[0]
    assert header.startswith(f"{transaction.TRANSACTION_COOKIE_NAME}=")
    assert "Max-Age=600" in header
    assert "Secure" in header and "HttpOnly" in header
    assert "Path=/" in header and "SameSite=Lax" in header
    assert "Domain=" not in header


def test_start_ignores_user_supplied_destinations(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    values = iter(("state-token", "v" * 43))
    monkeypatch.setattr(transaction.secrets, "token_urlsafe", lambda _size: next(values))

    response = client.get(
        "/auth/start?return=https://attacker.example/",
        base_url="https://grades.tutoringclub.com",
    )
    signed = _cookie_from_headers(response, transaction.TRANSACTION_COOKIE_NAME)
    auth_tx = transaction.load_transaction(signed, "test-cookie-secret")

    assert auth_tx.return_path == "/"
    assert "attacker.example" not in response.headers["Location"]


def test_callback_redeems_then_introspects_and_issues_minimum_session(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
) -> None:
    client = app.test_client()
    _start(client, monkeypatch)
    events: list[tuple[str, ...]] = []
    _install_fake(monkeypatch, claims, _grant(claims), events)
    monkeypatch.setattr(routes, "_now", lambda: 1_900_000_001, raising=False)

    response = client.get(
        "/auth/callback?state=state-token&code=opaque-code",
        base_url="https://grades.tutoringclub.com",
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/franchise/16"
    assert events == [
        ("redeem", "opaque-code", "v" * 43),
        ("introspect", claims.grant_id, claims.sub),
    ]
    _assert_cookie_cleared(response, transaction.TRANSACTION_COOKIE_NAME)
    signed = _cookie_from_headers(response, SESSION_COOKIE_NAME)
    session_module = importlib.import_module("ui.auth.session")
    grade_session = session_module.load_session(
        signed, "test-cookie-secret", now=1_900_000_001
    )
    assert grade_session == session_module.GradeSession(
        device_id=claims.sub,
        grant_id=claims.grant_id,
        crm_role="3",
        franchise_id=16,
        permissions=("students.read",),
        issued_at=1_900_000_001,
        expires_at=1_900_028_800,
    )
    assert all(
        forbidden not in signed
        for forbidden in ("opaque-code", "v" * 43, claims.jti, claims.iss)
    )


def test_callback_center_admin_keeps_fixed_dashboard_landing(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
) -> None:
    admin_claims = replace(
        claims,
        crm_role="2",
        permissions=("dashboard.read", "students.read"),
    )
    client = app.test_client()
    _start(client, monkeypatch)
    _install_fake(monkeypatch, admin_claims, _grant(admin_claims), [])
    monkeypatch.setattr(routes, "_now", lambda: 1_900_000_001, raising=False)

    response = client.get(
        "/auth/callback?state=state-token&code=opaque-code",
        base_url="https://grades.tutoringclub.com",
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


@pytest.mark.parametrize(
    "query",
    (
        "code=opaque-code",
        "state=state-token",
        "state=&code=opaque-code",
        "state=state-token&code=",
        "state=state-token&state=other&code=opaque-code",
        "state=state-token&code=opaque-code&code=other",
    ),
)
def test_callback_requires_exactly_one_nonempty_state_and_code(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
    query: str,
) -> None:
    client = app.test_client()
    _start(client, monkeypatch)
    events: list[tuple[str, ...]] = []
    _install_fake(monkeypatch, claims, _grant(claims), events)

    response = client.get(
        f"/auth/callback?{query}", base_url="https://grades.tutoringclub.com"
    )

    assert response.status_code == 400
    assert events == []
    _assert_cookie_cleared(response, transaction.TRANSACTION_COOKIE_NAME)
    assert b"Authentication could not be completed." in response.data
    assert b"opaque-code" not in response.data


def test_callback_rejects_mismatched_state_before_rust_and_clears_transaction(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
) -> None:
    client = app.test_client()
    _start(client, monkeypatch)
    events: list[tuple[str, ...]] = []
    _install_fake(monkeypatch, claims, _grant(claims), events)

    response = client.get(
        "/auth/callback?state=wrong-state&code=opaque-code",
        base_url="https://grades.tutoringclub.com",
    )

    _assert_callback_rejected_before_rust(response, events, "wrong-state")


def test_callback_rejects_malformed_transaction_before_rust_and_clears_it(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
) -> None:
    client = app.test_client()
    client.set_cookie(
        transaction.TRANSACTION_COOKIE_NAME,
        "malformed-transaction",
        domain="grades.tutoringclub.com",
        secure=True,
    )
    events: list[tuple[str, ...]] = []
    _install_fake(monkeypatch, claims, _grant(claims), events)

    response = client.get(
        "/auth/callback?state=state-token&code=opaque-code",
        base_url="https://grades.tutoringclub.com",
    )

    _assert_callback_rejected_before_rust(response, events, "malformed-transaction")


def test_callback_rejects_expired_transaction_before_rust_and_clears_it(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
) -> None:
    client = app.test_client()
    expired = transaction.sign_transaction(
        transaction.AuthTransaction(
            state="state-token",
            code_verifier="expired-verifier",
            return_path="/",
            expires_at=1,
        ),
        "test-cookie-secret",
    )
    client.set_cookie(
        transaction.TRANSACTION_COOKIE_NAME,
        expired,
        domain="grades.tutoringclub.com",
        secure=True,
    )
    events: list[tuple[str, ...]] = []
    _install_fake(monkeypatch, claims, _grant(claims), events)

    response = client.get(
        "/auth/callback?state=state-token&code=opaque-code",
        base_url="https://grades.tutoringclub.com",
    )

    _assert_callback_rejected_before_rust(response, events, "expired-verifier")


def test_callback_compares_state_constant_time_and_transaction_is_one_use(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
) -> None:
    client = app.test_client()
    _start(client, monkeypatch)
    events: list[tuple[str, ...]] = []
    _install_fake(monkeypatch, claims, _grant(claims), events)
    comparisons: list[tuple[str, str]] = []
    real_compare = routes.secrets.compare_digest

    def compare(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(routes.secrets, "compare_digest", compare)
    monkeypatch.setattr(routes, "_now", lambda: 1_900_000_001, raising=False)

    first = client.get(
        "/auth/callback?state=state-token&code=opaque-code",
        base_url="https://grades.tutoringclub.com",
    )
    replay = client.get(
        "/auth/callback?state=state-token&code=opaque-code",
        base_url="https://grades.tutoringclub.com",
    )

    assert first.status_code == 302
    assert replay.status_code == 400
    assert comparisons == [("state-token", "state-token")]
    assert [event[0] for event in events] == ["redeem", "introspect"]
    _assert_cookie_cleared(replay, transaction.TRANSACTION_COOKIE_NAME)


@pytest.mark.parametrize(
    "grant_change",
    (
        {"active": False},
        {"grant_id": "wrong-grant"},
        {"device_id": "wrong-device"},
        {"crm_role": "2"},
        {"franchise_id": 99},
        {"permissions": ("dashboard.read", "students.read")},
        {"expires_at": 1_900_000_001},
    ),
)
def test_callback_rejects_any_introspection_mismatch_without_session(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
    grant_change: dict[str, object],
) -> None:
    client = app.test_client()
    _start(client, monkeypatch)
    events: list[tuple[str, ...]] = []
    grant = replace(_grant(claims), **grant_change)
    _install_fake(monkeypatch, claims, grant, events)
    monkeypatch.setattr(routes, "_now", lambda: 1_900_000_001, raising=False)

    response = client.get(
        "/auth/callback?state=state-token&code=opaque-code",
        base_url="https://grades.tutoringclub.com",
    )

    assert response.status_code == 403
    assert [event[0] for event in events] == ["redeem", "introspect"]
    _assert_cookie_cleared(response, transaction.TRANSACTION_COOKIE_NAME)
    assert not any(
        header.startswith(f"{SESSION_COOKIE_NAME}=")
        for header in response.headers.getlist("Set-Cookie")
    )


def test_callback_rust_failure_is_controlled_and_consumes_transaction(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    claims: AuthClaims,
) -> None:
    client = app.test_client()
    _start(client, monkeypatch)
    events: list[tuple[str, ...]] = []
    _install_fake(
        monkeypatch,
        claims,
        _grant(claims),
        events,
        error=ClientError("redeem_failed"),
    )

    response = client.get(
        "/auth/callback?state=state-token&code=opaque-code",
        base_url="https://grades.tutoringclub.com",
    )

    assert response.status_code == 503
    assert b"opaque-code" not in response.data
    assert b"redeem_failed" not in response.data
    _assert_cookie_cleared(response, transaction.TRANSACTION_COOKIE_NAME)


def test_logout_clears_both_cookies_and_uses_fixed_destination(app: Flask) -> None:
    response = app.test_client().get(
        "/auth/logout?return=https://attacker.example/",
        base_url="https://grades.tutoringclub.com",
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    _assert_cookie_cleared(response, transaction.TRANSACTION_COOKIE_NAME)
    _assert_cookie_cleared(response, SESSION_COOKIE_NAME)


def test_grade_origin_has_no_private_key_challenge_routes(app: Flask) -> None:
    client = app.test_client()
    assert client.post("/auth/challenge", json={"franchise_id": 16}).status_code == 404
    assert client.post("/auth/verify", json={"signature": "secret"}).status_code == 404


@pytest.mark.parametrize("role", ["2", "3"])
@pytest.mark.parametrize("path", ["/franchise/16", "/franchise/16/student/101"])
@pytest.mark.parametrize("environment", ["production", "qa"])
def test_first_authorization_returns_to_explicit_destination(app, monkeypatch, claims, role, path, environment):
    host = "grades.tutoringclub.com"
    if environment == "qa":
        host = "qa-grades.tutoringclub.com"
        for name, value in {
            "GRADE_CHECKER_ENV": "qa",
            "CRM_AUTH_BASE_URL": "https://qa-crm-auth.tutoringclub.com",
            "CRM_AUTH_ISSUER": "https://qa-crm-auth.tutoringclub.com",
            "CRM_AUTH_JWKS_URL": "https://qa-crm-auth.tutoringclub.com/.well-known/jwks.json",
            "CRM_DEVICE_AUTHORIZE_URL": "https://qa.tutoraid.net/GradeCheckerDeviceAuthorize.aspx",
            "GRADE_CHECKER_CALLBACK_URL": f"https://{host}/auth/callback",
        }.items():
            monkeypatch.setenv(name, value)
    app.config["SERVER_NAME"] = host
    scoped = replace(claims, crm_role=role)
    events = []
    _install_fake(monkeypatch, scoped, _grant(scoped), events)
    monkeypatch.setattr(routes, "_now", lambda: 1_900_000_001)
    client = app.test_client()
    start = client.get("/auth/start", query_string={"next": path}, base_url=f"https://{host}")
    auth_tx = transaction.load_transaction(
        _cookie_from_headers(start, transaction.TRANSACTION_COOKIE_NAME), "test-cookie-secret",
    )
    assert auth_tx.return_path == path
    assert set(parse_qs(urlsplit(start.headers["Location"]).query)) == {
        "state", "code_challenge", "code_challenge_method",
    }
    response = client.get(
        "/auth/callback", query_string={"state": auth_tx.state, "code": "opaque-code"},
        base_url=f"https://{host}",
    )
    assert response.status_code == 302
    assert response.headers["Location"] == path
    assert [event[0] for event in events] == ["redeem", "introspect"]
    assert _cookie_from_headers(response, SESSION_COOKIE_NAME)
    _assert_cookie_cleared(response, transaction.TRANSACTION_COOKIE_NAME)


@pytest.mark.parametrize("query", [
    "next=", "next=/franchise/16&next=/franchise/17", "next=//evil.example/",
    "next=https://evil.example/", "next=%252Ffranchise%252F16",
    "next=%2Ffranchise%2F16%3Fgrade_filter%3Dall", "next=%2Ffranchise%2F16%23report",
])
def test_start_rejects_invalid_destinations_without_issuing_transaction(app, query, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid destinations must not generate state or call Rust")
    monkeypatch.setattr(transaction.secrets, "token_urlsafe", forbidden)
    monkeypatch.setattr(routes, "RustAuthClient", forbidden)
    response = app.test_client().get(f"/auth/start?{query}", base_url="https://grades.tutoringclub.com")
    assert response.status_code == 400
    assert "Location" not in response.headers
    _assert_cookie_cleared(response, transaction.TRANSACTION_COOKIE_NAME)


@pytest.mark.parametrize("path,permissions", [
    ("/franchise/99/student/101", ("students.read",)),
    ("/franchise/16/student/101", ("dashboard.read",)),
])
def test_callback_rejects_unauthorized_destination_without_session(app, monkeypatch, claims, path, permissions):
    scoped = replace(claims, permissions=permissions)
    events = []
    _install_fake(monkeypatch, scoped, _grant(scoped), events)
    monkeypatch.setattr(routes, "_now", lambda: 1_900_000_001)
    auth_tx = transaction.build_transaction(path)
    client = app.test_client()
    client.set_cookie(transaction.TRANSACTION_COOKIE_NAME,
                      transaction.sign_transaction(auth_tx, "test-cookie-secret"),
                      domain="grades.tutoringclub.com", secure=True)
    response = client.get("/auth/callback", query_string={"state": auth_tx.state, "code": "opaque-code"},
                          base_url="https://grades.tutoringclub.com")
    assert response.status_code == 403
    assert [event[0] for event in events] == ["redeem", "introspect"]
    assert not any(h.startswith(f"{SESSION_COOKIE_NAME}=") for h in response.headers.getlist("Set-Cookie"))
    _assert_cookie_cleared(response, transaction.TRANSACTION_COOKIE_NAME)


def test_new_authorization_supersedes_previous_tab(app, monkeypatch, claims):
    client = app.test_client()
    events = []
    _install_fake(monkeypatch, claims, _grant(claims), events)
    first = client.get("/auth/start?next=/franchise/16/student/101", base_url="https://grades.tutoringclub.com")
    first_tx = transaction.load_transaction(_cookie_from_headers(first, transaction.TRANSACTION_COOKIE_NAME), "test-cookie-secret")
    client.get("/auth/start?next=/franchise/16/student/102", base_url="https://grades.tutoringclub.com")
    response = client.get("/auth/callback", query_string={"state": first_tx.state, "code": "opaque-code"},
                          base_url="https://grades.tutoringclub.com")
    assert response.status_code == 400
    assert events == []
    assert "Location" not in response.headers


@pytest.mark.parametrize("role,path", [("2", "/franchise/16"), ("3", "/franchise/16/student/101")])
def test_first_click_journey_returns_through_real_guards_and_target_page(monkeypatch, config_environment, claims, role, path):
    import sys
    import time
    from ui.auth import guards, session

    for name in ("ui.routes", "ui.app"):
        sys.modules.pop(name, None)
    app_module = importlib.import_module("ui.app")
    pages = importlib.import_module("ui.routes")
    app_module.app.config.update(TESTING=True, SERVER_NAME="grades.tutoringclub.com")
    now = int(time.time())
    scoped = replace(claims, crm_role=role, iat=now-1, nbf=now-1, exp=now+1500)
    grant = replace(_grant(scoped), expires_at=now+3600)
    events = []
    fake = FakeRustClient(scoped, grant, events)
    monkeypatch.setattr(routes, "RustAuthClient", lambda _config: fake)
    monkeypatch.setattr(guards, "RustAuthClient", lambda _config: fake)
    student = pages.dashboard.merge_student_rows([{
        "crmstudentid": 101, "franchiseid": 16, "firstname": "Synthetic",
        "lastname": "Student", "grade": 10, "portal_url": "https://portal.example/login",
    }], [])[0]
    data_calls = []

    def students(*, franchise_id):
        data_calls.append(("students", franchise_id))
        return [student]

    def one_student(franchise_id, crmstudentid):
        data_calls.append(("student", franchise_id, crmstudentid))
        return student if (franchise_id, crmstudentid) == (16, 101) else None

    monkeypatch.setattr(pages.dashboard, "load_students", students)
    monkeypatch.setattr(pages.dashboard, "load_student", one_student)
    monkeypatch.setattr(pages.dashboard, "load_franchise_name", lambda _fid: "Synthetic Center")
    client = app_module.app.test_client()
    base_url = "https://grades.tutoringclub.com"
    initial = client.get(path, base_url=base_url)
    assert initial.status_code == 302
    assert parse_qs(urlsplit(initial.location).query) == {"next": [path]}
    start = client.get(initial.location, base_url=base_url)
    auth_tx = transaction.load_transaction(_cookie_from_headers(start, transaction.TRANSACTION_COOKIE_NAME), "test-cookie-secret")
    assert data_calls == []
    callback = client.get("/auth/callback", query_string={"state": auth_tx.state, "code": "opaque-code"}, base_url=base_url)
    assert callback.location == path
    assert [event[0] for event in events] == ["redeem", "introspect"]
    assert data_calls == []
    landing = client.get(callback.location, base_url=base_url)
    assert landing.status_code == 200
    assert b"Synthetic" in landing.data
    assert data_calls == ([("students", 16)] if role == "2" else [("student", 16, 101)])
    assert client.get(path, base_url=base_url).status_code == 200
    signed = client.get_cookie(SESSION_COOKIE_NAME, domain="grades.tutoringclub.com").value
    expired = replace(session.load_session(signed, "test-cookie-secret"), expires_at=now-1)
    client.set_cookie(SESSION_COOKIE_NAME, session.sign_session(expired, "test-cookie-secret"), domain="grades.tutoringclub.com")
    restart = client.get(path, base_url=base_url)
    assert restart.status_code == 302
    assert parse_qs(urlsplit(restart.location).query) == {"next": [path]}
