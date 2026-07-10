from __future__ import annotations

import asyncio

import pytest

from scraper import agenda


class _FakeContext:
    def set_default_timeout(self, _timeout):
        return None

    def set_default_navigation_timeout(self, _timeout):
        return None

    async def close(self):
        return None


class _FakeBrowser:
    async def new_context(self):
        return _FakeContext()

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


def test_empty_agenda_is_a_successful_synchronized_result(monkeypatch):
    results = []
    monkeypatch.setattr(agenda, "async_playwright", lambda: _FakePlaywright())
    monkeypatch.setattr(agenda.worker_api, "event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agenda.worker_api,
        "result",
        lambda job_id, lease, **payload: results.append((job_id, lease, payload)),
    )

    async def empty_agenda(_context, student, _target):
        return {}, student

    monkeypatch.setattr(agenda, "fetch_agenda", empty_agenda)

    summary = asyncio.run(
        agenda.collect_agendas(
            [{"db_id": 42, "track_agenda": True}],
            job_id="job-42",
            lease_token="00000000-0000-0000-0000-000000000042",
        )
    )

    assert summary == {"attempted": 1, "success": 1, "errors": 0}
    assert results[0][2] == {
        "crmstudentid": 42,
        "status": "agenda_synced",
        "weekly_agenda": {},
    }


def test_portal_failure_is_reported_without_error_details(monkeypatch):
    results = []
    monkeypatch.setattr(agenda, "async_playwright", lambda: _FakePlaywright())
    monkeypatch.setattr(agenda.worker_api, "event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agenda.worker_api,
        "result",
        lambda job_id, lease, **payload: results.append((job_id, lease, payload)),
    )

    async def failed_agenda(_context, _student, _target):
        raise RuntimeError("sensitive portal detail")

    monkeypatch.setattr(agenda, "fetch_agenda", failed_agenda)

    summary = asyncio.run(
        agenda.collect_agendas(
            [{"db_id": 42, "track_agenda": True}],
            job_id="job-42",
            lease_token="00000000-0000-0000-0000-000000000042",
        )
    )

    assert summary == {"attempted": 1, "success": 0, "errors": 1}
    assert results[0][2] == {
        "crmstudentid": 42,
        "status": "failed",
        "failure_code": "portal_failure",
    }


def test_agenda_result_has_no_direct_database_fallback():
    with pytest.raises(ValueError, match="leased worker job"):
        agenda.save_agenda_result({"db_id": 42}, {})
