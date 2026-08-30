from __future__ import annotations

import base64
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt
import pytest

from ui.auth.assertions import validate_assertion
from ui.auth.config import AuthConfig


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


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
        grade_checker_callback_url="https://grades.tutoringclub.com/auth/callback",
        grade_checker_cookie_secret="test-cookie-secret",
        auth_transaction_ttl_seconds=600,
    )


@pytest.fixture
def signed_assertion() -> tuple[str, dict[str, Any], dict[str, Any]]:
    key = Ed25519PrivateKey.generate()
    public_key = key.public_key().public_bytes_raw()
    jwks = {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": _base64url(public_key),
                "use": "sig",
                "alg": "EdDSA",
                "kid": "fixture-key",
            }
        ]
    }
    now = int(time.time())
    claims = {
        "iss": "https://crm-auth.tutoringclub.com",
        "aud": "grade-checker",
        "sub": "b4fc2e7f-9ca8-44d6-95ad-09851b690c63",
        "jti": "b4fc2e7f-9ca8-44d6-95ad-09851b690c64",
        "grant_id": "b4fc2e7f-9ca8-44d6-95ad-09851b690c65",
        "crm_role": "3",
        "actor_ref": "fixture-tutor",
        "franchise_id": 16,
        "permissions": ["students.read"],
        "iat": now,
        "nbf": now,
        "exp": now + 60,
    }
    raw = jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "fixture-key"})
    return raw, jwks, claims


def test_validate_assertion_verifies_ed25519_jwks_signature(
    signed_assertion: tuple[str, dict[str, Any], dict[str, Any]], config: AuthConfig
) -> None:
    raw, jwks, _ = signed_assertion

    claims = validate_assertion(raw, jwks, config)

    assert claims.sub == "b4fc2e7f-9ca8-44d6-95ad-09851b690c63"
    assert claims.permissions == ("students.read",)
    assert not hasattr(claims, "actor_ref")


def test_validate_assertion_rejects_tampered_unverified_decode(
    signed_assertion: tuple[str, dict[str, Any], dict[str, Any]], config: AuthConfig
) -> None:
    raw, jwks, _ = signed_assertion
    header, payload, signature = raw.split(".")
    replacement_payload = _base64url(b'{"sub":"attacker"}')
    tampered = f"{header}.{replacement_payload}.{signature}"

    with pytest.raises(ValueError):
        validate_assertion(tampered, jwks, config)


@pytest.mark.parametrize(
    "change",
    [
        lambda claims: claims.update(iss="https://attacker.example"),
        lambda claims: claims.update(aud="attacker"),
        lambda claims: claims.update(exp="tomorrow"),
        lambda claims: claims.update(nbf=int(time.time()) + 60),
        lambda claims: claims.update(permissions=["unknown.permission"]),
        lambda claims: claims.pop("grant_id"),
    ],
)
def test_validate_assertion_rejects_invalid_claims(
    signed_assertion: tuple[str, dict[str, Any], dict[str, Any]], config: AuthConfig, change: Any
) -> None:
    _, jwks, original_claims = signed_assertion
    payload = original_claims.copy()
    change(payload)
    key = Ed25519PrivateKey.generate()
    public_key = key.public_key().public_bytes_raw()
    jwks["keys"][0]["x"] = _base64url(public_key)
    raw = jwt.encode(payload, key, algorithm="EdDSA", headers={"kid": "fixture-key"})

    with pytest.raises(ValueError):
        validate_assertion(raw, jwks, config)


def test_validate_assertion_rejects_algorithm_substitution(
    signed_assertion: tuple[str, dict[str, Any], dict[str, Any]], config: AuthConfig
) -> None:
    _, jwks, payload = signed_assertion
    substituted = jwt.encode(
        payload, "shared-secret-with-at-least-thirty-two-bytes", algorithm="HS256", headers={"kid": "fixture-key"}
    )

    with pytest.raises(ValueError):
        validate_assertion(substituted, jwks, config)
