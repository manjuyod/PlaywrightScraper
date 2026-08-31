from __future__ import annotations

import pytest

from ui.auth.config import AuthConfig, load_auth_config


def _set_required_environment(
    monkeypatch: pytest.MonkeyPatch, environment: str | None = None
) -> None:
    if environment is None:
        monkeypatch.delenv("GRADE_CHECKER_ENV", raising=False)
    else:
        monkeypatch.setenv("GRADE_CHECKER_ENV", environment)
    if environment == "qa":
        auth_origin = "https://qa-crm-auth.tutoringclub.com"
        crm_authorize = "https://qa.tutoraid.net/GradeCheckerDeviceAuthorize.aspx"
        callback = "https://qa-grades.tutoringclub.com/auth/callback"
    else:
        auth_origin = "https://crm-auth.tutoringclub.com"
        crm_authorize = "https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx"
        callback = "https://grades.tutoringclub.com/auth/callback"
    monkeypatch.setenv("CRM_AUTH_BASE_URL", auth_origin)
    monkeypatch.setenv("CRM_AUTH_CLIENT_ID", "grade-checker")
    monkeypatch.setenv("CRM_AUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("CRM_AUTH_ISSUER", auth_origin)
    monkeypatch.setenv("CRM_AUTH_AUDIENCE", "grade-checker")
    monkeypatch.setenv("CRM_AUTH_JWKS_URL", f"{auth_origin}/.well-known/jwks.json")
    monkeypatch.setenv("CRM_DEVICE_AUTHORIZE_URL", crm_authorize)
    monkeypatch.setenv("GRADE_CHECKER_CALLBACK_URL", callback)
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
        grade_checker_origin="https://grades.tutoringclub.com",
        grade_checker_callback_url="https://grades.tutoringclub.com/auth/callback",
        grade_checker_cookie_secret="test-cookie-secret",
        auth_transaction_ttl_seconds=600,
    )


def test_load_auth_config_exposes_the_fixed_grade_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_environment(monkeypatch)

    assert load_auth_config().grade_checker_origin == "https://grades.tutoringclub.com"


def test_load_auth_config_accepts_explicit_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_environment(monkeypatch, "production")

    assert load_auth_config().grade_checker_origin == "https://grades.tutoringclub.com"


def test_load_auth_config_accepts_the_closed_qa_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_environment(monkeypatch, "qa")

    config = load_auth_config()
    assert config.crm_auth_base_url == "https://qa-crm-auth.tutoringclub.com"
    assert config.crm_auth_issuer == "https://qa-crm-auth.tutoringclub.com"
    assert config.crm_auth_jwks_url == (
        "https://qa-crm-auth.tutoringclub.com/.well-known/jwks.json"
    )
    assert config.crm_device_authorize_url == (
        "https://qa.tutoraid.net/GradeCheckerDeviceAuthorize.aspx"
    )
    assert config.grade_checker_origin == "https://qa-grades.tutoringclub.com"
    assert config.grade_checker_callback_url == (
        "https://qa-grades.tutoringclub.com/auth/callback"
    )


@pytest.mark.parametrize("environment", ["staging", "QA", " qa "])
def test_load_auth_config_rejects_unknown_environment(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    _set_required_environment(monkeypatch)
    monkeypatch.setenv("GRADE_CHECKER_ENV", environment)

    with pytest.raises(RuntimeError, match="GRADE_CHECKER_ENV"):
        load_auth_config()


@pytest.mark.parametrize(
    ("name", "production_value"),
    [
        ("CRM_AUTH_BASE_URL", "https://crm-auth.tutoringclub.com"),
        ("CRM_AUTH_ISSUER", "https://crm-auth.tutoringclub.com"),
        (
            "CRM_AUTH_JWKS_URL",
            "https://crm-auth.tutoringclub.com/.well-known/jwks.json",
        ),
        (
            "CRM_DEVICE_AUTHORIZE_URL",
            "https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx",
        ),
        (
            "GRADE_CHECKER_CALLBACK_URL",
            "https://grades.tutoringclub.com/auth/callback",
        ),
    ],
)
def test_qa_profile_rejects_production_urls(
    monkeypatch: pytest.MonkeyPatch, name: str, production_value: str
) -> None:
    _set_required_environment(monkeypatch, "qa")
    monkeypatch.setenv(name, production_value)

    with pytest.raises(RuntimeError):
        load_auth_config()


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
