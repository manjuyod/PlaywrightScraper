from __future__ import annotations

import pytest

from ui.auth.config import AuthConfig, load_auth_config


def _set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_AUTH_BASE_URL", "https://crm-auth.tutoringclub.com")
    monkeypatch.setenv("CRM_AUTH_CLIENT_ID", "grade-checker")
    monkeypatch.setenv("CRM_AUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("CRM_AUTH_ISSUER", "https://crm-auth.tutoringclub.com")
    monkeypatch.setenv("CRM_AUTH_AUDIENCE", "grade-checker")
    monkeypatch.setenv(
        "CRM_AUTH_JWKS_URL", "https://crm-auth.tutoringclub.com/.well-known/jwks.json"
    )
    monkeypatch.setenv(
        "CRM_DEVICE_AUTHORIZE_URL", "https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx"
    )
    monkeypatch.setenv(
        "GRADE_CHECKER_CALLBACK_URL", "https://grades.tutoringclub.com/auth/callback"
    )
    monkeypatch.setenv("GRADE_CHECKER_COOKIE_SECRET", "test-cookie-secret")
    monkeypatch.setenv("AUTH_TRANSACTION_TTL_SECONDS", "600")


def test_load_auth_config_accepts_only_the_fixed_v2_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_environment(monkeypatch)

    assert load_auth_config() == AuthConfig(
        crm_auth_base_url="https://crm-auth.tutoringclub.com",
        crm_auth_client_id="grade-checker",
        crm_auth_client_secret="test-client-secret",
        crm_auth_issuer="https://crm-auth.tutoringclub.com",
        crm_auth_audience="grade-checker",
        crm_auth_jwks_url="https://crm-auth.tutoringclub.com/.well-known/jwks.json",
        crm_device_authorize_url="https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx",
        grade_checker_callback_url="https://grades.tutoringclub.com/auth/callback",
        grade_checker_cookie_secret="test-cookie-secret",
        auth_transaction_ttl_seconds=600,
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CRM_AUTH_BASE_URL", "http://crm-auth.tutoringclub.com"),
        ("CRM_AUTH_CLIENT_ID", "another-client"),
        ("CRM_AUTH_ISSUER", "https://attacker.example"),
        ("CRM_AUTH_AUDIENCE", "another-audience"),
        ("CRM_AUTH_JWKS_URL", "https://crm-auth.tutoringclub.com/other-jwks"),
        ("CRM_DEVICE_AUTHORIZE_URL", "https://tutoraid.net/other"),
        ("GRADE_CHECKER_CALLBACK_URL", "https://grades.tutoringclub.com/other"),
        ("AUTH_TRANSACTION_TTL_SECONDS", "601"),
    ],
)
def test_load_auth_config_rejects_contract_drift(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError):
        load_auth_config()
