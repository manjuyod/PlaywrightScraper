from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import secrets
import time
from typing import Any

from flask import Response
from itsdangerous import BadData, URLSafeSerializer


TRANSACTION_COOKIE_NAME = "__Host-grade_checker_auth_tx"
_TRANSACTION_SIGNING_SALT = "grade-checker-auth-transaction-v1"
_ALLOWED_RETURN_PATHS = frozenset({"/"})


@dataclass(frozen=True)
class AuthTransaction:
    state: str
    code_verifier: str
    return_path: str
    expires_at: int


def build_transaction(
    return_path: str, ttl_seconds: int = 600, *, now: int | None = None
) -> AuthTransaction:
    if return_path not in _ALLOWED_RETURN_PATHS:
        raise ValueError("invalid return path")
    issued_at = int(time.time()) if now is None else now
    return AuthTransaction(
        state=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(32),
        return_path=return_path,
        expires_at=issued_at + ttl_seconds,
    )


def format_code_challenge(raw_code: str) -> str:
    digest = hashlib.sha256(raw_code.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def sign_transaction(auth_tx: AuthTransaction, secret: str) -> str:
    return _serializer(secret).dumps(asdict(auth_tx))


def load_transaction(
    signed: str, secret: str, *, now: int | None = None
) -> AuthTransaction:
    try:
        payload = _serializer(secret).loads(signed)
        auth_tx = _transaction_from_payload(payload)
    except (BadData, TypeError, ValueError) as exc:
        raise ValueError("invalid auth transaction") from exc
    current_time = int(time.time()) if now is None else now
    if auth_tx.expires_at <= current_time:
        raise ValueError("invalid auth transaction")
    return auth_tx


def set_transaction_cookie(
    response: Response, signed: str, *, max_age: int
) -> None:
    response.set_cookie(
        TRANSACTION_COOKIE_NAME,
        signed,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="Lax",
        path="/",
    )


def clear_transaction_cookie(response: Response) -> None:
    response.delete_cookie(
        TRANSACTION_COOKIE_NAME,
        secure=True,
        httponly=True,
        samesite="Lax",
        path="/",
    )


def _serializer(secret: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret, salt=_TRANSACTION_SIGNING_SALT)


def _transaction_from_payload(payload: Any) -> AuthTransaction:
    if not isinstance(payload, dict) or set(payload) != {
        "state",
        "code_verifier",
        "return_path",
        "expires_at",
    }:
        raise ValueError
    if not all(
        isinstance(payload[name], str) and payload[name]
        for name in ("state", "code_verifier", "return_path")
    ):
        raise ValueError
    if type(payload["expires_at"]) is not int:
        raise ValueError
    if payload["return_path"] not in _ALLOWED_RETURN_PATHS:
        raise ValueError
    return AuthTransaction(
        state=payload["state"],
        code_verifier=payload["code_verifier"],
        return_path=payload["return_path"],
        expires_at=payload["expires_at"],
    )
