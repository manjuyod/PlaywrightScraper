from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

from api_transport import HttpsTransportProfile, TransportConfigError


DEFAULT_API_BASE_URL = "http://127.0.0.1:3000"
MAX_WORKER_ARTIFACT_COUNTER = 100_000
WORKER_ARTIFACT_KINDS = {"grade", "agenda"}
WORKER_EVENT_CODES = {"job_started", "student_started"}
WORKER_FAILURE_CODES = {"worker_failed", "portal_failure"}
WORKER_RESULT_STATUSES = {"synced", "agenda_synced", "bad_login", "failed"}
MAX_WORKER_RESULT_DEPTH = 8
MAX_WORKER_RESULT_NODES = 1_000
MAX_WORKER_RESULT_STRING_BYTES = 4_096
SENSITIVE_WORKER_RESULT_KEYS = {
    "password",
    "p1password",
    "p2password",
    "secret",
    "clientsecret",
    "token",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "authheader",
    "apikey",
    "privatekey",
    "credential",
    "credentials",
    "session",
    "sessionid",
    "cookie",
    "username",
    "p1username",
    "p2username",
    "error",
    "errors",
    "exception",
    "traceback",
    "stack",
    "detail",
    "message",
}


class WorkerApiError(RuntimeError):
    def __init__(self, status: int, message: str = "Worker API request failed"):
        super().__init__(message)
        self.status = status


class ResultDeliveryAmbiguous(WorkerApiError):
    """The result may have been accepted, so the lease must be allowed to expire."""


def api_base_url() -> str:
    return os.getenv("GRADE_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def worker_api_key() -> str:
    key = os.getenv("WORKER_API_KEY", "")
    if not key:
        raise WorkerApiError(500, "WORKER_API_KEY is not configured")
    return key


def worker_id() -> str:
    value = os.getenv("WORKER_ID", "").strip()
    if not value:
        raise WorkerApiError(500, "WORKER_ID is not configured")
    return value


def _request_path(path: str, query: dict[str, Any] | None = None) -> str:
    if not path.startswith("/"):
        path = "/" + path
    if not query:
        return path
    encoded = urllib.parse.urlencode(
        {key: value for key, value in query.items() if value is not None}, doseq=True
    )
    return f"{path}?{encoded}" if encoded else path


def _json_body(payload: Any | None) -> bytes:
    if payload is None:
        return b""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _lease_token(lease_token: str) -> str:
    try:
        return str(uuid.UUID(lease_token))
    except (AttributeError, ValueError, TypeError) as exc:
        raise ValueError("Worker lease token must be a UUID") from exc


def _lease_expiry(lease_expires_at: str) -> datetime:
    if not isinstance(lease_expires_at, str):
        raise ValueError("Worker lease expiry must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(lease_expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Worker lease expiry must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("Worker lease expiry must include a timezone")
    return parsed.astimezone(timezone.utc)


def lease_renewal_interval(
    lease_expires_at: str, *, now: datetime | None = None
) -> float:
    current_time = now or datetime.now(timezone.utc)
    remaining_seconds = (_lease_expiry(lease_expires_at) - current_time).total_seconds()
    if remaining_seconds <= 0:
        raise ResultDeliveryAmbiguous(409, "Worker lease is already expired")
    return min(60.0, remaining_seconds / 3.0)


def request_json(
    method: str,
    path: str,
    *,
    payload: Any | None = None,
    query: dict[str, Any] | None = None,
    lease_token: str | None = None,
    timeout: int = 30,
) -> Any:
    body = _json_body(payload)
    path_with_query = _request_path(path, query)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {worker_api_key()}",
    }
    if lease_token is not None:
        headers["X-Worker-Lease"] = _lease_token(lease_token)
    if body:
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url=api_base_url() + path_with_query,
        data=body if body else None,
        headers=headers,
        method=method.upper(),
    )
    try:
        transport = HttpsTransportProfile.from_env(
            "WORKER_API",
            default_timeout_seconds=timeout,
            fallback_prefix="GRADE_API",
        )
        with transport.open(request) as response:
            raw = response.read()
    except TransportConfigError as exc:
        raise WorkerApiError(500, "Worker API transport is not configured") from exc
    except urllib.error.HTTPError as exc:
        raise WorkerApiError(exc.code, "Worker API request failed") from exc
    except urllib.error.URLError as exc:
        raise WorkerApiError(503, "Worker API is unavailable") from exc

    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkerApiError(502, "Worker API returned invalid JSON") from exc


def claim_job() -> dict[str, Any] | None:
    worker_id()
    claim = request_json("POST", "/api/worker/jobs/claim")
    if claim is None:
        return None
    if not isinstance(claim, dict) or not claim.get("job_id"):
        raise WorkerApiError(502, "Worker API returned an invalid job claim")
    try:
        claim["lease_token"] = _lease_token(claim.get("lease_token"))
        _lease_expiry(claim.get("lease_expires_at"))
    except ValueError as exc:
        raise WorkerApiError(502, "Worker API returned an invalid worker lease") from exc
    return claim


def job_context(job_id: str, lease_token: str) -> dict[str, Any]:
    return request_json(
        "GET", f"/api/worker/jobs/{job_id}/context", lease_token=lease_token
    )


def _worker_summary(
    *,
    kind: str,
    total: int,
    attempted: int,
    success: int,
    errors: int,
    require_complete: bool = False,
) -> dict[str, int | str]:
    if kind not in WORKER_ARTIFACT_KINDS:
        raise ValueError("Unsupported worker artifact kind")
    counters = {
        "total": total,
        "attempted": attempted,
        "success": success,
        "errors": errors,
    }
    if any(type(value) is not int or not 0 <= value <= MAX_WORKER_ARTIFACT_COUNTER for value in counters.values()):
        raise ValueError("Worker artifact counters must be bounded nonnegative integers")
    if attempted > total or success + errors > attempted:
        raise ValueError("Worker artifact counters are inconsistent")
    if require_complete and (attempted != total or success + errors != attempted):
        raise ValueError("Worker completion counters are incomplete")
    return {"kind": kind, **counters}


def heartbeat(
    job_id: str,
    lease_token: str,
    *,
    kind: str,
    total: int,
    attempted: int,
    success: int,
    errors: int,
) -> None:
    request_json(
        "POST",
        f"/api/worker/jobs/{job_id}/heartbeat",
        payload=_worker_summary(
            kind=kind,
            total=total,
            attempted=attempted,
            success=success,
            errors=errors,
        ),
        lease_token=lease_token,
    )


def event(
    job_id: str, lease_token: str, code: str, *, crmstudentid: int | None = None
) -> None:
    if code not in WORKER_EVENT_CODES:
        raise ValueError("Unsupported worker event code")
    if (code == "job_started") != (crmstudentid is None):
        raise ValueError("Worker event fields are inconsistent")
    payload: dict[str, int | str] = {"code": code}
    if crmstudentid is not None:
        if type(crmstudentid) is not int or crmstudentid <= 0:
            raise ValueError("crmstudentid must be a positive integer")
        payload["crmstudentid"] = crmstudentid
    request_json(
        "POST",
        f"/api/worker/jobs/{job_id}/events",
        payload=payload,
        lease_token=lease_token,
    )


def result(
    job_id: str,
    lease_token: str,
    *,
    crmstudentid: int,
    status: str,
    idempotency_key: str | None = None,
    failure_code: str | None = None,
    passwordgood: bool | None = None,
    parsed_grades: dict[str, Any] | list[Any] | None = None,
    weekly_agenda: dict[str, Any] | list[Any] | None = None,
) -> str:
    if type(crmstudentid) is not int or crmstudentid <= 0:
        raise ValueError("crmstudentid must be a positive integer")
    if status not in WORKER_RESULT_STATUSES:
        raise ValueError("Unsupported worker result status")
    if (failure_code is not None) != (status == "failed"):
        raise ValueError("Worker result failure code does not match status")
    if failure_code is not None and failure_code not in WORKER_FAILURE_CODES:
        raise ValueError("Unsupported worker failure code")
    if passwordgood is not None and type(passwordgood) is not bool:
        raise ValueError("passwordgood must be a boolean")
    if parsed_grades is not None:
        _validate_worker_result_json(parsed_grades)
    if weekly_agenda is not None:
        _validate_worker_result_json(weekly_agenda)
    key = (
        str(uuid.UUID(idempotency_key))
        if idempotency_key
        else deterministic_result_idempotency_key(
            job_id,
            crmstudentid,
            status,
            failure_code,
            passwordgood,
            parsed_grades,
            weekly_agenda,
        )
    )
    payload: dict[str, Any] = {
        "crmstudentid": crmstudentid,
        "idempotency_key": key,
        "status": status,
    }
    if failure_code is not None:
        payload["failure_code"] = failure_code
    if passwordgood is not None:
        payload["passwordgood"] = passwordgood
    if parsed_grades is not None:
        payload["parsed_grades"] = parsed_grades
    if weekly_agenda is not None:
        payload["weekly_agenda"] = weekly_agenda
    try:
        request_json(
            "POST",
            f"/api/worker/jobs/{job_id}/results",
            payload=payload,
            lease_token=lease_token,
        )
    except WorkerApiError as exc:
        if not 500 <= exc.status <= 599:
            raise
        try:
            request_json(
                "POST",
                f"/api/worker/jobs/{job_id}/results",
                payload=payload,
                lease_token=lease_token,
            )
        except WorkerApiError as retry_exc:
            if 500 <= retry_exc.status <= 599:
                raise ResultDeliveryAmbiguous(
                    retry_exc.status, "Worker result delivery is ambiguous"
                ) from retry_exc
            raise
    return key


def deterministic_result_idempotency_key(
    job_id: str,
    crmstudentid: int,
    status: str,
    failure_code: str | None,
    passwordgood: bool | None,
    parsed_grades: dict[str, Any] | list[Any] | None,
    weekly_agenda: dict[str, Any] | list[Any] | None,
) -> str:
    semantic_payload = {
        "job_id": job_id,
        "crmstudentid": crmstudentid,
        "status": status,
        "failure_code": failure_code,
        "passwordgood": passwordgood,
        "parsed_grades": parsed_grades,
        "weekly_agenda": weekly_agenda,
    }
    canonical = json.dumps(
        semantic_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"grade-worker-result:{canonical}"))


def _normalized_result_key(key: str) -> str:
    return "".join(character.lower() for character in key if character.isalnum())


def _validate_worker_result_json(value: Any) -> None:
    nodes = 0

    def visit(current: Any, depth: int) -> None:
        nonlocal nodes
        if depth > MAX_WORKER_RESULT_DEPTH:
            raise ValueError("Worker result JSON is too deeply nested")
        nodes += 1
        if nodes > MAX_WORKER_RESULT_NODES:
            raise ValueError("Worker result JSON is too large")
        if isinstance(current, str):
            if len(current.encode("utf-8")) > MAX_WORKER_RESULT_STRING_BYTES:
                raise ValueError("Worker result JSON string is too large")
            return
        if isinstance(current, dict):
            for key, nested in current.items():
                if not isinstance(key, str) or _normalized_result_key(key) in SENSITIVE_WORKER_RESULT_KEYS:
                    raise ValueError("Worker result JSON contains a sensitive field")
                visit(nested, depth + 1)
        elif isinstance(current, list):
            for nested in current:
                visit(nested, depth + 1)

    visit(value, 0)


def complete(
    job_id: str,
    lease_token: str,
    *,
    kind: str,
    total: int,
    attempted: int,
    success: int,
    errors: int,
) -> None:
    request_json(
        "POST",
        f"/api/worker/jobs/{job_id}/complete",
        payload=_worker_summary(
            kind=kind,
            total=total,
            attempted=attempted,
            success=success,
            errors=errors,
            require_complete=True,
        ),
        lease_token=lease_token,
    )


def fail(job_id: str, lease_token: str, code: str) -> None:
    if code not in WORKER_FAILURE_CODES:
        raise ValueError("Unsupported worker failure code")
    request_json(
        "POST",
        f"/api/worker/jobs/{job_id}/fail",
        payload={"code": code},
        lease_token=lease_token,
    )
