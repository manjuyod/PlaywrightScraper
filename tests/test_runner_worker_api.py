from __future__ import annotations

import asyncio
import threading

from scraper import agenda
from scraper import runner


def test_students_from_worker_context_maps_api_context_to_existing_scraper_shape():
    context = {
        "students": [
            {
                "crmstudentid": 42,
                "firstname": "Ada",
                "portal1": "https://portal.example.test",
                "p1username": "ada-login",
                "p1password": "secret",
                "portal2": "https://alt.example.test",
                "p2username": "alt-login",
                "p2password": "alt-secret",
                "portal": "homeaccess",
                "track_agenda": True,
                "status": "queued",
                "passwordgood": True,
                "auth_images": ["cat", "dog"],
            }
        ]
    }

    students = runner.students_from_worker_context(
        context,
        job_id="job-42",
        lease_token="00000000-0000-0000-0000-000000000042",
    )

    assert students == [
        {
            "db_id": 42,
            "student_name": "Ada",
            "login_url": "https://portal.example.test",
            "id": "ada-login",
            "password": "secret",
            "alt_login_url": "https://alt.example.test",
            "alt_id": "alt-login",
            "alt_password": "alt-secret",
            "portal": "homeaccess",
            "auth_images": ["cat", "dog"],
            "track_agenda": True,
            "status": "queued",
            "passwordgood": True,
            "job_id": "job-42",
            "lease_token": "00000000-0000-0000-0000-000000000042",
        }
    ]


def test_mark_bad_login_reports_to_worker_api_when_job_context_exists(monkeypatch):
    captured = []
    monkeypatch.setattr(
        runner.worker_api,
        "result",
        lambda job_id, lease_token, **payload: captured.append((job_id, lease_token, payload)),
    )
    runner.mark_bad_login(
        {"db_id": 42, "job_id": "job-42", "lease_token": "00000000-0000-0000-0000-000000000042"}
    )

    assert captured == [
        (
            "job-42",
            "00000000-0000-0000-0000-000000000042",
            {
                "crmstudentid": 42,
                "status": "bad_login",
                "passwordgood": False,
            },
        )
    ]


def test_default_worker_id_uses_windows_computername(monkeypatch):
    monkeypatch.delenv("WORKER_ID", raising=False)
    monkeypatch.setenv("COMPUTERNAME", "WIN-EC2")
    monkeypatch.delattr(runner.os, "uname", raising=False)

    assert runner.default_worker_id() == "WIN-EC2"


def test_save_agenda_result_reports_to_worker_api_when_job_context_exists(monkeypatch):
    captured = []
    monkeypatch.setattr(
        agenda.worker_api,
        "result",
        lambda job_id, lease_token, **payload: captured.append((job_id, lease_token, payload)),
    )
    agenda.save_agenda_result(
        {"db_id": 42},
        {"missing": []},
        job_id="job-42",
        lease_token="00000000-0000-0000-0000-000000000042",
    )

    assert captured == [
        (
            "job-42",
            "00000000-0000-0000-0000-000000000042",
            {
                "crmstudentid": 42,
                "weekly_agenda": {"missing": []},
                "status": "agenda_synced",
            },
        )
    ]


def test_save_agenda_result_passes_the_worker_lease(monkeypatch):
    captured = []
    monkeypatch.setattr(
        agenda.worker_api,
        "result",
        lambda job_id, lease_token, **payload: captured.append((job_id, lease_token, payload)),
    )

    agenda.save_agenda_result(
        {"db_id": 42},
        {"missing": []},
        job_id="job-42",
        lease_token="00000000-0000-0000-0000-000000000042",
    )

    assert captured[0][1] == "00000000-0000-0000-0000-000000000042"


def test_worker_agenda_job_uses_agenda_collector(monkeypatch):
    calls = []

    monkeypatch.setattr(
        runner.worker_api,
        "claim_job",
        lambda: {
            "job_id": "job-42",
            "kind": "agenda",
            "lease_token": "00000000-0000-0000-0000-000000000042",
            "lease_expires_at": "2030-01-01T00:05:00Z",
        },
    )
    monkeypatch.setattr(
        runner.worker_api,
        "event",
        lambda job_id, lease_token, code, crmstudentid=None: calls.append(
            ("event", job_id, lease_token, code, crmstudentid)
        ),
    )
    monkeypatch.setattr(
        runner.worker_api,
        "job_context",
        lambda job_id, lease_token: {"students": [{"crmstudentid": 42, "track_agenda": True}]},
    )
    monkeypatch.setattr(
        runner.worker_api,
        "heartbeat",
        lambda job_id, lease_token, **summary: calls.append(("heartbeat", job_id, lease_token, summary)),
    )
    monkeypatch.setattr(
        runner.worker_api,
        "complete",
        lambda job_id, lease_token, **summary: calls.append(("complete", job_id, lease_token, summary)),
    )
    monkeypatch.setattr(
        runner.worker_api,
        "fail",
        lambda job_id, lease_token, code: calls.append(("fail", job_id, lease_token, code)),
    )
    async def fake_collect_agendas(students, job_id=None, lease_token=None):
        calls.append(("agenda", job_id, lease_token, students))
        return {"attempted": 1, "success": 1, "errors": 0}

    monkeypatch.setattr(agenda, "collect_agendas", fake_collect_agendas)

    assert asyncio.run(runner.run_worker_once()) is True

    assert calls[0] == (
        "event",
        "job-42",
        "00000000-0000-0000-0000-000000000042",
        "job_started",
        None,
    )
    assert calls[1] == (
        "heartbeat",
        "job-42",
        "00000000-0000-0000-0000-000000000042",
        {"kind": "agenda", "total": 1, "attempted": 0, "success": 0, "errors": 0},
    )
    assert calls[2][0:2] == ("agenda", "job-42")
    assert calls[2][3][0]["db_id"] == 42
    assert calls[2][3][0]["job_id"] == "job-42"
    assert calls[2][3][0]["lease_token"] == "00000000-0000-0000-0000-000000000042"
    assert calls[3] == (
        "complete",
        "job-42",
        "00000000-0000-0000-0000-000000000042",
        {"kind": "agenda", "total": 1, "attempted": 1, "success": 1, "errors": 0},
    )


def test_worker_abandons_lease_after_ambiguous_result_delivery(monkeypatch):
    calls = []
    lease_token = "00000000-0000-0000-0000-000000000042"
    monkeypatch.setattr(
        runner.worker_api,
        "claim_job",
        lambda: {
            "job_id": "job-42",
            "kind": "agenda",
            "lease_token": lease_token,
            "lease_expires_at": "2030-01-01T00:05:00Z",
        },
    )
    monkeypatch.setattr(runner.worker_api, "event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner.worker_api,
        "job_context",
        lambda *_args, **_kwargs: {"students": [{"crmstudentid": 42, "track_agenda": True}]},
    )
    monkeypatch.setattr(runner.worker_api, "heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner.worker_api, "complete", lambda *_args, **_kwargs: calls.append("complete"))
    monkeypatch.setattr(runner.worker_api, "fail", lambda *_args, **_kwargs: calls.append("fail"))

    async def ambiguous_collect(*_args, **_kwargs):
        raise runner.worker_api.ResultDeliveryAmbiguous(503)

    monkeypatch.setattr(agenda, "collect_agendas", ambiguous_collect)

    assert asyncio.run(runner.run_worker_once()) is False
    assert calls == []


class _FakeBrowser:
    async def close(self):
        return None


class _FakePlaywright:
    class _Chromium:
        async def launch(self, **_kwargs):
            return _FakeBrowser()

    chromium = _Chromium()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def test_reported_bad_login_is_not_followed_by_a_generic_failed_result(monkeypatch):
    calls = []
    lease_token = "00000000-0000-0000-0000-000000000042"
    monkeypatch.setattr(
        runner.worker_api,
        "claim_job",
        lambda: {
            "job_id": "job-42",
            "kind": "grade",
            "lease_token": lease_token,
            "lease_expires_at": "2030-01-01T00:05:00Z",
        },
    )
    monkeypatch.setattr(runner.worker_api, "event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner.worker_api,
        "job_context",
        lambda *_args, **_kwargs: {"students": [{"crmstudentid": 42, "track_agenda": False}]},
    )
    monkeypatch.setattr(runner.worker_api, "heartbeat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner.worker_api, "complete", lambda *_args, **_kwargs: calls.append("complete"))
    monkeypatch.setattr(runner.worker_api, "fail", lambda *_args, **_kwargs: calls.append("fail"))
    monkeypatch.setattr(
        runner.worker_api,
        "result",
        lambda _job_id, _lease, **payload: calls.append(payload["status"]),
    )
    monkeypatch.setattr(runner, "async_playwright", lambda: _FakePlaywright())

    async def reported_bad_login(_browser, student):
        runner.mark_bad_login(student)
        raise runner.ReportedBadLogin()

    monkeypatch.setattr(runner, "scrape_one", reported_bad_login)

    assert asyncio.run(runner.run_worker_once()) is True
    assert calls == ["bad_login", "complete"]


def test_renewal_error_abandons_the_lease_without_terminal_mutation(monkeypatch):
    calls = []
    lease_token = "00000000-0000-0000-0000-000000000042"
    monkeypatch.setattr(
        runner.worker_api,
        "claim_job",
        lambda: {
            "job_id": "job-42",
            "kind": "agenda",
            "lease_token": lease_token,
            "lease_expires_at": "2030-01-01T00:05:00Z",
        },
    )
    monkeypatch.setattr(runner.worker_api, "event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner.worker_api,
        "job_context",
        lambda *_args, **_kwargs: {"students": [{"crmstudentid": 42, "track_agenda": True}]},
    )
    heartbeat_calls = 0
    renewal_heartbeat_seen = threading.Event()

    def heartbeat(*_args, **_kwargs):
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        if heartbeat_calls > 1:
            renewal_heartbeat_seen.set()
            raise runner.worker_api.WorkerApiError(409)

    monkeypatch.setattr(runner.worker_api, "heartbeat", heartbeat)
    monkeypatch.setattr(runner.worker_api, "lease_renewal_interval", lambda *_args, **_kwargs: 0.001)
    monkeypatch.setattr(runner.worker_api, "complete", lambda *_args, **_kwargs: calls.append("complete"))
    monkeypatch.setattr(runner.worker_api, "fail", lambda *_args, **_kwargs: calls.append("fail"))

    async def slow_agenda(*_args, **_kwargs):
        await asyncio.to_thread(renewal_heartbeat_seen.wait, 1)
        return {"attempted": 1, "success": 1, "errors": 0}

    monkeypatch.setattr(agenda, "collect_agendas", slow_agenda)

    result = asyncio.run(runner.run_worker_once())
    assert heartbeat_calls > 1, heartbeat_calls
    assert result is False
    assert calls == []
