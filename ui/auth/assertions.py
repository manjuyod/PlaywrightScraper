from __future__ import annotations

from typing import Any
from uuid import UUID

import jwt


from .config import AuthConfig
from .models import AuthClaims


_ROLE_PERMISSIONS = {
    "2": ("dashboard.read", "students.read"),
    "3": ("students.read",),
}
_REQUIRED_CLAIMS = (
    "iss", "aud", "sub", "jti", "grant_id", "crm_role", "actor_ref", "franchise_id",
    "permissions", "iat", "nbf", "exp",
)


def validate_assertion(raw: str, jwks: dict[str, Any], config: AuthConfig) -> AuthClaims:
    try:
        header = jwt.get_unverified_header(raw)
        if header.get("alg") != "EdDSA" or not isinstance(header.get("kid"), str):
            raise ValueError
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise ValueError
        matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == header["kid"]]
        if len(matches) != 1:
            raise ValueError
        key = jwt.PyJWK.from_dict(matches[0])
        if key.algorithm_name != "EdDSA":
            raise ValueError
        claims = jwt.decode(
            raw,
            key.key,
            algorithms=["EdDSA"],
            issuer=config.crm_auth_issuer,
            audience=config.crm_auth_audience,
            options={"require": list(_REQUIRED_CLAIMS)},
        )
        return _claims_from_payload(claims)
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid assertion") from exc


def _claims_from_payload(claims: dict[str, Any]) -> AuthClaims:
    string_names = ("iss", "aud", "sub", "jti", "grant_id", "crm_role", "actor_ref")
    if not all(isinstance(claims.get(name), str) and claims[name] for name in string_names):
        raise ValueError
    for name in ("sub", "jti", "grant_id"):
        UUID(claims[name])
    if type(claims.get("franchise_id")) is not int or claims["franchise_id"] <= 0:
        raise ValueError
    if not all(type(claims.get(name)) is int for name in ("iat", "nbf", "exp")):
        raise ValueError
    if claims["iat"] > claims["nbf"] or claims["exp"] - claims["iat"] > 1_500:
        raise ValueError
    permissions = claims.get("permissions")
    if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
        raise ValueError
    if tuple(permissions) != _ROLE_PERMISSIONS.get(claims["crm_role"]):
        raise ValueError
    return AuthClaims(
        sub=claims["sub"],
        jti=claims["jti"],
        iss=claims["iss"],
        aud=claims["aud"],
        grant_id=claims["grant_id"],
        crm_role=claims["crm_role"],
        franchise_id=claims["franchise_id"],
        permissions=tuple(permissions),
        iat=claims["iat"],
        nbf=claims["nbf"],
        exp=claims["exp"],
    )
