from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Any

from flask import Response
from itsdangerous import BadData, URLSafeSerializer

from .models import AuthClaims, GrantIntrospection


SESSION_COOKIE_NAME = "__Host-grade_checker_session"
_SESSION_SIGNING_SALT = "grade-checker-session-v1"


@dataclass(frozen=True)
class GradeSession:
    device_id: str
    grant_id: str
    crm_role: str
    franchise_id: int
    permissions: tuple[str, ...]
    issued_at: int
    expires_at: int


def create_session(
    claims: AuthClaims, grant: GrantIntrospection, *, now: int | None = None
) -> GradeSession:
    issued_at = int(time.time()) if now is None else now
    if (
        not grant.active
        or grant.grant_id != claims.grant_id
        or grant.device_id != claims.sub
        or grant.crm_role != claims.crm_role
        or grant.franchise_id != claims.franchise_id
        or grant.permissions != claims.permissions
        or type(grant.expires_at) is not int
        or grant.expires_at <= issued_at
    ):
        raise ValueError("grant does not match assertion")
    return GradeSession(
        device_id=claims.sub,
        grant_id=claims.grant_id,
        crm_role=claims.crm_role,
        franchise_id=claims.franchise_id,
        permissions=claims.permissions,
        issued_at=issued_at,
        expires_at=grant.expires_at,
    )


def sign_session(grade_session: GradeSession, secret: str) -> str:
    return _serializer(secret).dumps(asdict(grade_session))


def load_session(signed: str, secret: str, *, now: int | None = None) -> GradeSession:
    try:
        payload = _serializer(secret).loads(signed)
        grade_session = _session_from_payload(payload)
    except (BadData, TypeError, ValueError) as exc:
        raise ValueError("invalid Grade session") from exc
    current_time = int(time.time()) if now is None else now
    if grade_session.issued_at > current_time or grade_session.expires_at <= current_time:
        raise ValueError("invalid Grade session")
    return grade_session


def set_session_cookie(
    response: Response,
    signed: str,
    grade_session: GradeSession,
    *,
    now: int | None = None,
) -> None:
    current_time = int(time.time()) if now is None else now
    max_age = grade_session.expires_at - current_time
    if max_age <= 0:
        raise ValueError("invalid Grade session")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        signed,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="Lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        secure=True,
        httponly=True,
        samesite="Lax",
        path="/",
    )


def _serializer(secret: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret, salt=_SESSION_SIGNING_SALT)


def _session_from_payload(payload: Any) -> GradeSession:
    if not isinstance(payload, dict) or set(payload) != {
        "device_id",
        "grant_id",
        "crm_role",
        "franchise_id",
        "permissions",
        "issued_at",
        "expires_at",
    }:
        raise ValueError
    if not all(
        isinstance(payload[name], str) and payload[name]
        for name in ("device_id", "grant_id", "crm_role")
    ):
        raise ValueError
    if type(payload["franchise_id"]) is not int or payload["franchise_id"] <= 0:
        raise ValueError
    if not all(type(payload[name]) is int for name in ("issued_at", "expires_at")):
        raise ValueError
    permissions = payload["permissions"]
    if not isinstance(permissions, list) or not all(
        isinstance(permission, str) for permission in permissions
    ):
        raise ValueError
    if payload["issued_at"] >= payload["expires_at"]:
        raise ValueError
    return GradeSession(
        device_id=payload["device_id"],
        grant_id=payload["grant_id"],
        crm_role=payload["crm_role"],
        franchise_id=payload["franchise_id"],
        permissions=tuple(permissions),
        issued_at=payload["issued_at"],
        expires_at=payload["expires_at"],
    )
