from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from ui.auth.client import ClientError, RustAuthClient
from ui.auth.config import AuthConfig
from ui.auth.models import AuthClaims


@dataclass
class _Response:
    payload: dict[str, Any]
    status_code: int = 200
    raw_body: bytes | None = None
    json_called: bool = False

    def json(self) -> dict[str, Any]:
        self.json_called = True
        return self.payload

    def iter_content(self, chunk_size: int) -> Any:
        body = self.raw_body if self.raw_body is not None else json.dumps(self.payload).encode()
        return (body[index : index + chunk_size] for index in range(0, len(body), chunk_size))


class HttpStub:
    def __init__(self) -> None:
        self.last_path = ""
        self.last_json: dict[str, Any] | None = None
        self.last_auth: tuple[str, str] | None = None
        self.last_timeout: tuple[int, int] | None = None
        self.response = _Response({})
        self.get_response = _Response({"keys": []})

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.last_path = url.removeprefix("https://crm-auth.tutoringclub.com")
        self.last_json = kwargs["json"]
        self.last_auth = kwargs["auth"]
        self.last_timeout = kwargs["timeout"]
        return self.response

    def get(self, url: str, **kwargs: Any) -> _Response:
        assert url == "https://crm-auth.tutoringclub.com/.well-known/jwks.json"
        assert kwargs["timeout"] == (3, 3)
        return self.get_response


@pytest.fixture
def config() -> AuthConfig:
    return AuthConfig(
        crm_auth_base_url="https://crm-auth.tutoringclub.com",
        crm_auth_client_id="grade-checker",
        crm_auth_client_secret="test-secret",
        crm_auth_issuer="https://crm-auth.tutoringclub.com",
        crm_auth_audience="grade-checker",
        crm_auth_jwks_url="https://crm-auth.tutoringclub.com/.well-known/jwks.json",
        crm_device_authorize_url="https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx",
        grade_checker_origin="https://grades.tutoringclub.com",
        grade_checker_callback_url="https://grades.tutoringclub.com/auth/callback",
        grade_checker_cookie_secret="test-cookie-secret",
        auth_transaction_ttl_seconds=600,
    )


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
        iat=1_700_000_000,
        nbf=1_700_000_000,
        exp=1_700_001_500,
    )


def test_redeem_and_introspection_shapes_are_exact(
    monkeypatch: pytest.MonkeyPatch, config: AuthConfig, claims: AuthClaims
) -> None:
    http_stub = HttpStub()
    http_stub.response = _Response({"assertion": "signed-assertion"})
    monkeypatch.setattr("ui.auth.client.validate_assertion", lambda *_: claims)
    client = RustAuthClient(config, session=http_stub)

    actual_claims = client.redeem_authorization_code("opaque-code", "v" * 43)
    assert actual_claims == claims
    assert http_stub.last_path == "/v2/authorization/redeem"
    assert http_stub.last_json == {
        "authorization_code": "opaque-code",
        "code_verifier": "v" * 43,
    }
    assert http_stub.last_auth == ("grade-checker", "test-secret")
    assert http_stub.last_timeout == (3, 3)

    http_stub.response = _Response(
        {
            "active": True,
            "grant_id": claims.grant_id,
            "device_id": claims.sub,
            "crm_role": "3",
            "franchise_id": 16,
            "permissions": ["students.read"],
            "expires_at": 1_700_001_500,
        }
    )
    result = client.introspect_grant(claims.grant_id, claims.sub)
    assert http_stub.last_path == "/v2/grants/introspect"
    assert http_stub.last_json == {"grant_id": claims.grant_id, "device_id": claims.sub}
    assert result.active is True
    assert result.permissions == ("students.read",)


def test_client_exposes_controlled_error_without_response_details(
    config: AuthConfig,
) -> None:
    http_stub = HttpStub()
    http_stub.response = _Response({"error": "authorization code leaked"}, status_code=400)

    with pytest.raises(ClientError, match="redeem_failed") as error:
        RustAuthClient(config, session=http_stub).redeem_authorization_code("secret", "v" * 43)

    assert error.value.code == "redeem_failed"
    assert "leaked" not in str(error.value)


@pytest.mark.parametrize("operation", ["redeem", "introspection"])
def test_client_rejects_oversized_post_body_before_json_parsing(
    monkeypatch: pytest.MonkeyPatch, config: AuthConfig, operation: str
) -> None:
    http_stub = HttpStub()
    http_stub.response = _Response({}, raw_body=b"x" * 65_537)
    monkeypatch.setattr("ui.auth.client.validate_assertion", lambda *_: pytest.fail("unexpected validation"))
    client = RustAuthClient(config, session=http_stub)

    with pytest.raises(ClientError):
        if operation == "redeem":
            client.redeem_authorization_code("opaque-code", "v" * 43)
        else:
            client.introspect_grant("grant-id", "device-id")

    assert http_stub.response.json_called is False


def test_client_rejects_oversized_jwks_before_json_parsing(
    monkeypatch: pytest.MonkeyPatch, config: AuthConfig, claims: AuthClaims
) -> None:
    http_stub = HttpStub()
    http_stub.response = _Response({"assertion": "signed-assertion"})
    http_stub.get_response = _Response({}, raw_body=b"x" * 65_537)
    monkeypatch.setattr("ui.auth.client.validate_assertion", lambda *_: claims)

    with pytest.raises(ClientError, match="jwks_failed"):
        RustAuthClient(config, session=http_stub).redeem_authorization_code("opaque-code", "v" * 43)

    assert http_stub.get_response.json_called is False


def test_client_translates_assertion_validation_details(
    monkeypatch: pytest.MonkeyPatch, config: AuthConfig
) -> None:
    http_stub = HttpStub()
    http_stub.response = _Response({"assertion": "signed-assertion"})
    monkeypatch.setattr(
        "ui.auth.client.validate_assertion",
        lambda *_: (_ for _ in ()).throw(ValueError("signature detail must not escape")),
    )

    with pytest.raises(ClientError, match="redeem_failed") as error:
        RustAuthClient(config, session=http_stub).redeem_authorization_code("opaque-code", "v" * 43)

    assert "signature detail" not in str(error.value)
