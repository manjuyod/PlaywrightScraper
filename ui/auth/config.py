from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlsplit


CRM_AUTH_BASE_URL = "https://crm-auth.tutoringclub.com"
CRM_AUTH_CLIENT_ID = "grade-checker"
CRM_AUTH_ISSUER = "https://crm-auth.tutoringclub.com"
CRM_AUTH_AUDIENCE = "grade-checker"
CRM_AUTH_JWKS_URL = "https://crm-auth.tutoringclub.com/.well-known/jwks.json"
CRM_DEVICE_AUTHORIZE_URL = "https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx"
GRADE_CHECKER_CALLBACK_URL = "https://grades.tutoringclub.com/auth/callback"
GRADE_CHECKER_ORIGIN = "https://grades.tutoringclub.com"
QA_CRM_AUTH_BASE_URL = "https://qa-crm-auth.tutoringclub.com"
QA_CRM_AUTH_ISSUER = "https://qa-crm-auth.tutoringclub.com"
QA_CRM_AUTH_JWKS_URL = "https://qa-crm-auth.tutoringclub.com/.well-known/jwks.json"
QA_CRM_DEVICE_AUTHORIZE_URL = "https://qa.tutoraid.net/GradeCheckerDeviceAuthorize.aspx"
QA_GRADE_CHECKER_CALLBACK_URL = "https://qa-grades.tutoringclub.com/auth/callback"
QA_GRADE_CHECKER_ORIGIN = "https://qa-grades.tutoringclub.com"
AUTH_TRANSACTION_TTL_SECONDS = 600


@dataclass(frozen=True)
class AuthConfig:
    crm_auth_base_url: str
    crm_auth_client_id: str
    crm_auth_client_secret: str
    crm_auth_issuer: str
    crm_auth_audience: str
    crm_auth_jwks_url: str
    crm_device_authorize_url: str
    grade_checker_origin: str
    grade_checker_callback_url: str
    grade_checker_cookie_secret: str
    auth_transaction_ttl_seconds: int


@dataclass(frozen=True)
class _EnvironmentProfile:
    crm_auth_base_url: str
    crm_auth_issuer: str
    crm_auth_jwks_url: str
    crm_device_authorize_url: str
    grade_checker_origin: str
    grade_checker_callback_url: str


_PRODUCTION_PROFILE = _EnvironmentProfile(
    crm_auth_base_url=CRM_AUTH_BASE_URL,
    crm_auth_issuer=CRM_AUTH_ISSUER,
    crm_auth_jwks_url=CRM_AUTH_JWKS_URL,
    crm_device_authorize_url=CRM_DEVICE_AUTHORIZE_URL,
    grade_checker_origin=GRADE_CHECKER_ORIGIN,
    grade_checker_callback_url=GRADE_CHECKER_CALLBACK_URL,
)
_QA_PROFILE = _EnvironmentProfile(
    crm_auth_base_url=QA_CRM_AUTH_BASE_URL,
    crm_auth_issuer=QA_CRM_AUTH_ISSUER,
    crm_auth_jwks_url=QA_CRM_AUTH_JWKS_URL,
    crm_device_authorize_url=QA_CRM_DEVICE_AUTHORIZE_URL,
    grade_checker_origin=QA_GRADE_CHECKER_ORIGIN,
    grade_checker_callback_url=QA_GRADE_CHECKER_CALLBACK_URL,
)


def load_auth_config() -> AuthConfig:
    profile = _load_environment_profile()
    callback_url = _require_fixed_url(
        "GRADE_CHECKER_CALLBACK_URL", profile.grade_checker_callback_url
    )
    return AuthConfig(
        crm_auth_base_url=_require_fixed_url(
            "CRM_AUTH_BASE_URL", profile.crm_auth_base_url
        ),
        crm_auth_client_id=_require_fixed_value("CRM_AUTH_CLIENT_ID", CRM_AUTH_CLIENT_ID),
        crm_auth_client_secret=_require_env("CRM_AUTH_CLIENT_SECRET"),
        crm_auth_issuer=_require_fixed_url("CRM_AUTH_ISSUER", profile.crm_auth_issuer),
        crm_auth_audience=_require_fixed_value("CRM_AUTH_AUDIENCE", CRM_AUTH_AUDIENCE),
        crm_auth_jwks_url=_require_fixed_url(
            "CRM_AUTH_JWKS_URL", profile.crm_auth_jwks_url
        ),
        crm_device_authorize_url=_require_fixed_url(
            "CRM_DEVICE_AUTHORIZE_URL", profile.crm_device_authorize_url
        ),
        grade_checker_origin=_origin_from_callback(
            callback_url, profile.grade_checker_origin
        ),
        grade_checker_callback_url=callback_url,
        grade_checker_cookie_secret=_require_env("GRADE_CHECKER_COOKIE_SECRET"),
        auth_transaction_ttl_seconds=_require_fixed_ttl(),
    )


def _load_environment_profile() -> _EnvironmentProfile:
    environment = os.environ.get("GRADE_CHECKER_ENV")
    if environment in (None, "", "production"):
        return _PRODUCTION_PROFILE
    if environment == "qa":
        return _QA_PROFILE
    raise RuntimeError("Invalid GRADE_CHECKER_ENV")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _require_fixed_value(name: str, expected: str) -> str:
    value = _require_env(name)
    if value != expected:
        raise RuntimeError(f"Invalid {name}")
    return value


def _require_fixed_url(name: str, expected: str) -> str:
    value = _require_fixed_value(name, expected)
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"Invalid {name}")
    return value


def _require_fixed_ttl() -> int:
    value = _require_env("AUTH_TRANSACTION_TTL_SECONDS")
    try:
        ttl = int(value)
    except ValueError as exc:
        raise RuntimeError("Invalid AUTH_TRANSACTION_TTL_SECONDS") from exc
    if ttl != AUTH_TRANSACTION_TTL_SECONDS:
        raise RuntimeError("Invalid AUTH_TRANSACTION_TTL_SECONDS")
    return ttl


def _origin_from_callback(callback_url: str, expected_origin: str) -> str:
    parsed = urlsplit(callback_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin != expected_origin:
        raise RuntimeError("Invalid GRADE_CHECKER_CALLBACK_URL")
    return origin
