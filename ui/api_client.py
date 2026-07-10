from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from api_transport import HttpsTransportProfile, TransportConfigError


DEFAULT_API_BASE_URL = "http://127.0.0.1:3000"


@dataclass(frozen=True)
class ApiScope:
    franchise_id: int | None = None
    role: int | str | None = None
    user: str | None = None


class ApiClientError(RuntimeError):
    def __init__(self, status: int, message: str = "API request failed"):
        super().__init__(message)
        self.status = status


def api_base_url() -> str:
    return os.getenv("GRADE_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def _hmac_secret() -> str:
    secret = os.getenv("DASHBOARD_HMAC_SIGNING_SECRET", "")
    if not secret and os.getenv("DEPLOYMENT_ENV", "development").lower() != "production":
        secret = os.getenv("SESSION_HMAC_SECRET", "")
    if not secret or secret.strip() != secret:
        raise ApiClientError(500, "Dashboard API signing is not configured")
    return secret


def _json_body(payload: Any | None) -> bytes:
    if payload is None:
        return b""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _signature(
    *,
    method: str,
    path_with_query: str,
    body: bytes,
    timestamp: str,
    nonce: str,
    scope: ApiScope,
) -> str:
    franchise_id = "" if scope.franchise_id is None else str(scope.franchise_id)
    role = "" if scope.role is None else str(scope.role)
    user = scope.user or ""
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join(
        (
            timestamp,
            method.upper(),
            path_with_query,
            franchise_id,
            role,
            user,
            nonce,
            body_hash,
        )
    )
    return hmac.new(
        _hmac_secret().encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _request_path(path: str, query: dict[str, Any] | None = None) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if not query:
        return path
    encoded = urllib.parse.urlencode(
        {key: value for key, value in query.items() if value is not None}, doseq=True
    )
    return f"{path}?{encoded}" if encoded else path


def request_json(
    method: str,
    path: str,
    *,
    scope: ApiScope,
    payload: Any | None = None,
    query: dict[str, Any] | None = None,
    timeout: int = 20,
) -> Any:
    body = _json_body(payload)
    path_with_query = _request_path(path, query)
    url = api_base_url() + path_with_query
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    franchise_id = "" if scope.franchise_id is None else str(scope.franchise_id)
    role = "" if scope.role is None else str(scope.role)
    user = scope.user or ""

    headers = {
        "Accept": "application/json",
        "X-Api-Timestamp": timestamp,
        "X-Api-Franchise-Id": franchise_id,
        "X-Api-Role": role,
        "X-Api-User": user,
        "X-Api-Nonce": nonce,
        "X-Api-Signature": _signature(
            method=method,
            path_with_query=path_with_query,
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            scope=scope,
        ),
    }
    if body:
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url=url,
        data=body if body else None,
        headers=headers,
        method=method.upper(),
    )
    try:
        transport = HttpsTransportProfile.from_env(
            "GRADE_API", default_timeout_seconds=timeout
        )
        with transport.open(request) as response:
            raw = response.read()
    except TransportConfigError as exc:
        raise ApiClientError(500, "Dashboard API transport is not configured") from exc
    except urllib.error.HTTPError as exc:
        raise ApiClientError(exc.code, "API request failed") from exc
    except urllib.error.URLError as exc:
        raise ApiClientError(503, "API is unavailable") from exc

    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiClientError(502, "API returned invalid JSON") from exc


def login(username: str, password: str) -> dict[str, Any]:
    return request_json(
        "POST",
        "/api/auth/login",
        scope=ApiScope(),
        payload={"username": username, "password": password},
    )
