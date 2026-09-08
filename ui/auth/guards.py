from __future__ import annotations

from functools import wraps
from typing import Callable, ParamSpec, TypeVar, cast

from flask import Response, g, jsonify, make_response, redirect, request, url_for

from .client import ClientError, RustAuthClient
from .config import load_auth_config
from .models import AuthClaims, GrantIntrospection, Role
from .session import (
    GradeSession,
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    load_session,
)


_CLAIMS_KEY = "grade_auth_claims"
_P = ParamSpec("_P")
_R = TypeVar("_R")


class _AuthFlowError(RuntimeError):
    def __init__(self, status: int, *, restart: bool = False, clear: bool = False):
        super().__init__()
        self.status = status
        self.restart = restart
        self.clear = clear


def current_claims() -> AuthClaims:
    cached = g.get(_CLAIMS_KEY)
    if isinstance(cached, AuthClaims):
        return cached

    config = load_auth_config()
    signed = request.cookies.get(SESSION_COOKIE_NAME)
    if not signed:
        raise _AuthFlowError(302, restart=True, clear=True)
    try:
        grade_session = load_session(signed, config.grade_checker_cookie_secret)
    except ValueError as exc:
        raise _AuthFlowError(302, restart=True, clear=True) from exc

    try:
        grant = RustAuthClient(config).introspect_grant(
            grade_session.grant_id, grade_session.device_id
        )
    except ClientError as exc:
        raise _AuthFlowError(503) from exc

    if not grant.active:
        raise _AuthFlowError(302, restart=True, clear=True)
    if not _matches_session(grant, grade_session):
        raise _AuthFlowError(403, clear=True)

    claims = _trusted_claims(
        grade_session, config.crm_auth_issuer, config.crm_auth_audience
    )
    setattr(g, _CLAIMS_KEY, claims)
    return claims


def require_permission(
    permission: str, api: bool = False
) -> Callable[[Callable[_P, _R]], Callable[_P, _R | Response]]:
    def decorator(view: Callable[_P, _R]) -> Callable[_P, _R | Response]:
        @wraps(view)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R | Response:
            try:
                claims = current_claims()
            except _AuthFlowError as exc:
                return _failure_response(exc, api=api)
            if permission not in claims.permissions:
                return _forbidden(api=api)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def require_franchise(
    permission: str,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R | Response]]:
    def decorator(view: Callable[_P, _R]) -> Callable[_P, _R | Response]:
        @wraps(view)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R | Response:
            try:
                claims = current_claims()
            except _AuthFlowError as exc:
                return _failure_response(exc, api=False)
            route_franchise = kwargs.get("franchise_id")
            if (
                permission not in claims.permissions
                or type(route_franchise) is not int
                or route_franchise != claims.franchise_id
            ):
                return _forbidden(api=False)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def _matches_session(grant: GrantIntrospection, grade_session: GradeSession) -> bool:
    return (
        grant.grant_id == grade_session.grant_id
        and grant.device_id == grade_session.device_id
        and grant.crm_role == grade_session.crm_role
        and grant.franchise_id == grade_session.franchise_id
        and grant.permissions == grade_session.permissions
    )


def _trusted_claims(
    grade_session: GradeSession, issuer: str, audience: str
) -> AuthClaims:
    return AuthClaims(
        sub=grade_session.device_id,
        jti="",
        iss=issuer,
        aud=audience,
        grant_id=grade_session.grant_id,
        crm_role=cast(Role, grade_session.crm_role),
        franchise_id=grade_session.franchise_id,
        permissions=grade_session.permissions,
        iat=grade_session.issued_at,
        nbf=grade_session.issued_at,
        exp=grade_session.expires_at,
    )


def _failure_response(error: _AuthFlowError, *, api: bool) -> Response:
    if error.restart:
        response = redirect(url_for("auth.start_auth"))
    elif error.status == 403:
        response = _forbidden(api=api)
    else:
        response = make_response("Authentication service temporarily unavailable.", 503)
    if error.clear:
        clear_session_cookie(response)
    return _no_store(response)


def _forbidden(*, api: bool) -> Response:
    if api:
        response = make_response(jsonify({"error": "forbidden"}), 403)
    else:
        response = make_response("Access denied.", 403)
    return _no_store(response)


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
