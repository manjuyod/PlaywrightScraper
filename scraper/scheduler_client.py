from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any, Literal

from api_transport import HttpsTransportProfile, TransportConfigError


DEFAULT_API_BASE_URL = "http://127.0.0.1:3000"


class SchedulerApiError(RuntimeError):
    def __init__(self, status: int, message: str = "Scheduler API request failed"):
        super().__init__(message)
        self.status = status


def api_base_url() -> str:
    return os.getenv("GRADE_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def scheduler_api_key() -> str:
    value = os.getenv("SCHEDULER_API_KEY", "")
    if not value or value.strip() != value:
        raise SchedulerApiError(500, "SCHEDULER_API_KEY is not configured")
    return value


def scheduler_id() -> str:
    value = os.getenv("SCHEDULER_ID", "").strip()
    if not value:
        raise SchedulerApiError(500, "SCHEDULER_ID is not configured")
    return value


def request_json(method: str, path: str, payload: Any | None = None) -> Any:
    scheduler_id()
    body = (
        b""
        if payload is None
        else json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {scheduler_api_key()}",
    }
    if body:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        api_base_url() + (path if path.startswith("/") else f"/{path}"),
        data=body or None,
        headers=headers,
        method=method.upper(),
    )
    try:
        transport = HttpsTransportProfile.from_env(
            "SCHEDULER_API",
            default_timeout_seconds=30,
            fallback_prefix="GRADE_API",
        )
        with transport.open(request) as response:
            raw = response.read()
    except TransportConfigError as exc:
        raise SchedulerApiError(500, "Scheduler API transport is not configured") from exc
    except urllib.error.HTTPError as exc:
        raise SchedulerApiError(exc.code) from exc
    except urllib.error.URLError as exc:
        raise SchedulerApiError(503, "Scheduler API is unavailable") from exc
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SchedulerApiError(502, "Scheduler API returned invalid JSON") from exc


def reconcile_students() -> dict[str, Any]:
    payload = request_json("POST", "/api/scheduler/reconcile-students")
    if not isinstance(payload, dict):
        raise SchedulerApiError(502, "Scheduler API returned an invalid reconciliation")
    return payload


def enqueue_job(
    *,
    franchise_id: int,
    kind: Literal["grade", "agenda"],
    idempotency_key: str,
    student_id: int | None = None,
) -> dict[str, Any]:
    if type(franchise_id) is not int or franchise_id <= 0:
        raise ValueError("franchise_id must be positive")
    if kind not in {"grade", "agenda"}:
        raise ValueError("kind must be grade or agenda")
    key = str(uuid.UUID(idempotency_key))
    payload: dict[str, Any] = {
        "franchise_id": franchise_id,
        "kind": kind,
        "idempotency_key": key,
    }
    if student_id is not None:
        if type(student_id) is not int or student_id <= 0:
            raise ValueError("student_id must be positive")
        payload["student_id"] = student_id
    response = request_json("POST", "/api/scheduler/jobs", payload)
    if not isinstance(response, dict) or not response.get("id"):
        raise SchedulerApiError(502, "Scheduler API returned an invalid job")
    return response
