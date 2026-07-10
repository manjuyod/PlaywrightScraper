from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from scraper import api_client as worker_api
from ui.auth import CrmLoginResult
from ui import api_client
import ui.auth as auth


LEASE_TOKEN = "00000000-0000-0000-0000-000000000042"


class _FakeHTTPResponse:
    def __init__(self, payload: dict | list | None = None, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        if self.payload is None:
            return b""
        return json.dumps(self.payload).encode("utf-8")


def test_dashboard_request_signs_scope_headers(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        captured["timeout"] = timeout
        return _FakeHTTPResponse({"ok": True})

    monkeypatch.setenv("GRADE_API_BASE_URL", "http://api.local:3000")
    monkeypatch.setenv("SESSION_HMAC_SECRET", "test-secret")
    monkeypatch.setattr(api_client.time, "time", lambda: 1234567890)
    monkeypatch.setattr(api_client.urllib.request, "urlopen", fake_urlopen)

    result = api_client.request_json(
        "POST",
        "/api/jobs/manual-pull",
        scope=api_client.ApiScope(franchise_id=11, role=2, user="alice"),
        payload={"kind": "grade"},
    )

    assert result == {"ok": True}
    assert captured["url"] == "http://api.local:3000/api/jobs/manual-pull"
    assert captured["method"] == "POST"
    assert captured["headers"]["X-api-franchise-id"] == "11"
    assert captured["headers"]["X-api-role"] == "2"
    assert captured["headers"]["X-api-user"] == "alice"
    assert captured["headers"]["X-api-timestamp"] == "1234567890"
    assert uuid.UUID(captured["headers"]["X-api-nonce"])
    assert len(captured["headers"]["X-api-signature"]) == 64
    assert json.loads(captured["body"]) == {"kind": "grade"}
    assert captured["timeout"] == 20


def test_dashboard_signature_binds_the_user_nonce_and_body(monkeypatch):
    monkeypatch.setenv("SESSION_HMAC_SECRET", "test-secret")
    scope = api_client.ApiScope(franchise_id=11, role=2, user="alice")
    original = api_client._signature(
        method="POST",
        path_with_query="/api/students?student_id=1",
        body=b'{"grade":12}',
        timestamp="1234567890",
        nonce="00000000-0000-0000-0000-000000000042",
        scope=scope,
    )

    assert original != api_client._signature(
        method="POST",
        path_with_query="/api/students?student_id=1",
        body=b'{"grade":12}',
        timestamp="1234567890",
        nonce="00000000-0000-0000-0000-000000000043",
        scope=scope,
    )
    assert original != api_client._signature(
        method="POST",
        path_with_query="/api/students?student_id=1",
        body=b'{"grade":11}',
        timestamp="1234567890",
        nonce="00000000-0000-0000-0000-000000000042",
        scope=scope,
    )
    assert original != api_client._signature(
        method="POST",
        path_with_query="/api/students?student_id=1",
        body=b'{"grade":12}',
        timestamp="1234567890",
        nonce="00000000-0000-0000-0000-000000000042",
        scope=api_client.ApiScope(franchise_id=11, role=2, user="bob"),
    )


def test_dashboard_request_raises_safe_error(monkeypatch):
    def fake_urlopen(_request, _timeout):
        raise HTTPError(
            url="http://api.local/api/students",
            code=503,
            msg="database password leaked here",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setenv("GRADE_API_BASE_URL", "http://api.local:3000")
    monkeypatch.setenv("SESSION_HMAC_SECRET", "test-secret")
    monkeypatch.setattr(api_client.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(api_client.ApiClientError) as exc_info:
        api_client.request_json(
            "GET",
            "/api/students",
            scope=api_client.ApiScope(franchise_id=11, role=2),
        )

    assert exc_info.value.status == 503
    assert "password leaked" not in str(exc_info.value)


def test_worker_client_adds_bearer_auth_and_claims_without_a_body(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data
        captured["timeout"] = timeout
        return _FakeHTTPResponse(
            {
                "job_id": "job-1",
                "lease_token": LEASE_TOKEN,
                "lease_expires_at": "2030-01-01T00:05:00Z",
            }
        )

    monkeypatch.setenv("GRADE_API_BASE_URL", "http://api.local:3000")
    monkeypatch.setenv("WORKER_API_KEY", "worker-secret")
    monkeypatch.setenv("WORKER_ID", "ec2-worker")
    monkeypatch.setattr(worker_api.urllib.request, "urlopen", fake_urlopen)

    result = worker_api.claim_job()

    assert result == {
        "job_id": "job-1",
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": "2030-01-01T00:05:00Z",
    }
    assert captured["url"] == "http://api.local:3000/api/worker/jobs/claim"
    assert captured["method"] == "POST"
    assert captured["headers"]["Authorization"] == "Bearer worker-secret"
    assert captured["body"] is None
    assert captured["timeout"] == 30


def test_worker_artifact_client_builds_only_canonical_payloads(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker_api,
        "request_json",
        lambda method, path, *, payload=None, **_kwargs: calls.append((method, path, payload)),
    )

    worker_api.heartbeat(
        "job-1", LEASE_TOKEN, kind="grade", total=3, attempted=1, success=1, errors=0
    )
    worker_api.event("job-1", LEASE_TOKEN, "student_started", crmstudentid=42)
    worker_api.complete(
        "job-1", LEASE_TOKEN, kind="grade", total=3, attempted=3, success=2, errors=1
    )
    worker_api.fail("job-1", LEASE_TOKEN, "worker_failed")

    assert calls == [
        (
            "POST",
            "/api/worker/jobs/job-1/heartbeat",
            {"kind": "grade", "total": 3, "attempted": 1, "success": 1, "errors": 0},
        ),
        (
            "POST",
            "/api/worker/jobs/job-1/events",
            {"code": "student_started", "crmstudentid": 42},
        ),
        (
            "POST",
            "/api/worker/jobs/job-1/complete",
            {"kind": "grade", "total": 3, "attempted": 3, "success": 2, "errors": 1},
        ),
        ("POST", "/api/worker/jobs/job-1/fail", {"code": "worker_failed"}),
    ]


def test_worker_result_uses_a_stable_uuid_idempotency_key(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker_api,
        "request_json",
        lambda method, path, *, payload=None, **_kwargs: calls.append((method, path, payload)),
    )

    key = "00000000-0000-0000-0000-000000000042"
    returned_key = worker_api.result(
        "job-1",
        LEASE_TOKEN,
        crmstudentid=42,
        status="synced",
        parsed_grades={"math": {"score": 95}},
        idempotency_key=key,
    )

    assert returned_key == key
    assert calls == [
        (
            "POST",
            "/api/worker/jobs/job-1/results",
            {
                "crmstudentid": 42,
                "idempotency_key": key,
                "status": "synced",
                "parsed_grades": {"math": {"score": 95}},
            },
        )
    ]


def test_worker_claim_returns_none_for_an_empty_204_response(monkeypatch):
    monkeypatch.setenv("WORKER_ID", "worker-1")
    monkeypatch.setattr(worker_api, "request_json", lambda *_args, **_kwargs: None)

    assert worker_api.claim_job() is None


def test_worker_client_passes_the_claim_lease_on_post_claim_calls(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return _FakeHTTPResponse({"students": []})

    monkeypatch.setenv("GRADE_API_BASE_URL", "http://api.local:3000")
    monkeypatch.setenv("WORKER_API_KEY", "worker-secret")
    monkeypatch.setattr(worker_api.urllib.request, "urlopen", fake_urlopen)

    worker_api.job_context("job-1", LEASE_TOKEN)

    assert captured["headers"]["X-worker-lease"] == LEASE_TOKEN
    assert captured["timeout"] == 30


def test_worker_result_default_key_is_deterministic_per_job_and_student(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker_api,
        "request_json",
        lambda method, path, *, payload=None, **_kwargs: calls.append(payload),
    )
    first = worker_api.result(
        "job-1", LEASE_TOKEN, crmstudentid=42, status="synced"
    )
    second = worker_api.result(
        "job-1", LEASE_TOKEN, crmstudentid=42, status="synced"
    )

    assert first == second
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"] == first


def test_worker_result_default_key_is_canonical_across_leases_and_changes_with_semantics(monkeypatch):
    payloads = []
    monkeypatch.setattr(
        worker_api,
        "request_json",
        lambda _method, _path, *, payload=None, **_kwargs: payloads.append(payload),
    )
    common = {
        "crmstudentid": 42,
        "status": "synced",
        "failure_code": None,
        "passwordgood": True,
        "parsed_grades": {"math": {"score": 95}, "english": {"score": 88}},
        "weekly_agenda": {"missing": []},
    }
    first = worker_api.result(
        "job-1",
        LEASE_TOKEN,
        **common,
    )
    same_semantics_new_lease = worker_api.result(
        "job-1",
        "00000000-0000-0000-0000-000000000043",
        **{
            **common,
            "parsed_grades": {"english": {"score": 88}, "math": {"score": 95}},
        },
    )
    changed_outcome = worker_api.result(
        "job-1",
        LEASE_TOKEN,
        **{**common, "status": "failed", "failure_code": "worker_failed"},
    )
    changed_data = worker_api.result(
        "job-1",
        LEASE_TOKEN,
        **{**common, "parsed_grades": {"math": {"score": 94}, "english": {"score": 88}}},
    )

    assert first == same_semantics_new_lease
    assert len({first, changed_outcome, changed_data}) == 3
    assert payloads[0]["idempotency_key"] == payloads[1]["idempotency_key"]


def test_lease_renewal_interval_is_bounded_by_claim_expiry():
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert worker_api.lease_renewal_interval(
        (now + timedelta(seconds=600)).isoformat(), now=now
    ) == 60
    assert worker_api.lease_renewal_interval(
        (now + timedelta(seconds=90)).isoformat(), now=now
    ) == 30


def test_worker_claim_rejects_missing_lease_expiry(monkeypatch):
    monkeypatch.setenv("WORKER_ID", "worker-1")
    monkeypatch.setattr(
        worker_api,
        "request_json",
        lambda *_args, **_kwargs: {"job_id": "job-1", "lease_token": LEASE_TOKEN},
    )

    with pytest.raises(worker_api.WorkerApiError):
        worker_api.claim_job()


def test_worker_result_retries_one_transient_failure_with_the_same_idempotency_key(monkeypatch):
    calls = []

    def fail_once(method, path, *, payload=None, **_kwargs):
        calls.append((method, path, payload))
        if len(calls) == 1:
            raise worker_api.WorkerApiError(503)

    monkeypatch.setattr(worker_api, "request_json", fail_once)

    key = worker_api.result(
        "job-1",
        LEASE_TOKEN,
        crmstudentid=42,
        status="synced",
        parsed_grades={"math": {"score": 95}},
    )

    assert len(calls) == 2
    assert calls[0][2]["idempotency_key"] == key
    assert calls[1][2]["idempotency_key"] == key


@pytest.mark.parametrize(
    "call",
    [
            lambda: worker_api.heartbeat(
                "job-1", LEASE_TOKEN, kind="unknown", total=1, attempted=0, success=0, errors=0
            ),
            lambda: worker_api.heartbeat(
                "job-1", LEASE_TOKEN, kind="grade", total=-1, attempted=0, success=0, errors=0
            ),
        lambda: worker_api.event("job-1", LEASE_TOKEN, "unknown"),
        lambda: worker_api.event("job-1", LEASE_TOKEN, "job_started", crmstudentid=42),
        lambda: worker_api.event("job-1", LEASE_TOKEN, "student_started"),
        lambda: worker_api.event("job-1", LEASE_TOKEN, "student_started", crmstudentid=-1),
        lambda: worker_api.complete(
            "job-1", LEASE_TOKEN, kind="grade", total=2, attempted=1, success=1, errors=0
        ),
        lambda: worker_api.result(
            "job-1",
            LEASE_TOKEN,
            crmstudentid=42,
            status="synced",
            parsed_grades={"detail": "unsafe"},
        ),
        lambda: worker_api.fail("job-1", LEASE_TOKEN, "exception_text"),
    ],
)
def test_worker_artifact_client_rejects_unsafe_payload_values(call):
    with pytest.raises(ValueError):
        call()


def test_worker_result_dynamic_validation_rejects_exact_diagnostics_not_safe_names(monkeypatch):
    with pytest.raises(ValueError):
        worker_api.result(
            "job-1",
            LEASE_TOKEN,
            crmstudentid=42,
            status="synced",
            parsed_grades={"nested": {"traceback": "unsafe"}},
        )

    captured = []
    monkeypatch.setattr(
        worker_api,
        "request_json",
        lambda method, path, *, payload=None, **_kwargs: captured.append(payload),
    )
    worker_api.result(
        "job-1",
        LEASE_TOKEN,
        crmstudentid=42,
        status="synced",
        parsed_grades={"author": "ok", "monkey": "ok", "assignmentKey": "ok"},
    )
    assert captured


def test_crm_login_uses_api_boundary(monkeypatch):
    monkeypatch.setattr(
        auth.api_client,
        "login",
        lambda username, password: {
            "authenticated": True,
            "role": 2,
            "franchise_id": 19,
            "display_name": f"{username}-display",
        },
    )

    result = auth.crm_login("alice", "secret")

    assert result == CrmLoginResult(
        authenticated=True,
        role=2,
        franchise_id=19,
        display_name="alice-display",
    )


def test_crm_login_returns_failure_for_api_errors(monkeypatch):
    def fail_login(_username, _password):
        raise api_client.ApiClientError(503, "API is unavailable")

    monkeypatch.setattr(auth.api_client, "login", fail_login)

    assert auth.crm_login("alice", "secret") == CrmLoginResult(authenticated=False)


def test_worker_result_accepts_portal_failure_code(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker_api,
        "request_json",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)),
    )

    worker_api.result(
        "job-1",
        LEASE_TOKEN,
        crmstudentid=42,
        status="failed",
        failure_code="portal_failure",
    )

    assert calls[0][2]["payload"]["failure_code"] == "portal_failure"
