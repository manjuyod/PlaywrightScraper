from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scraper import agenda
from scraper.config.logging import ContextFilter, bind_log_context, reset_log_context


def raw_student(number=1, **changes):
    row = {
        "crmstudentid": number, "franchiseid": 19,
        "firstname": "Synthetic", "lastname": "Student",
        "portal1": "https://canvas.example/login",
        "p1username": " primary user ", "p1password": " primary secret ",
        "portal2": None, "p2username": None, "p2password": None,
        "portal": "powerschool", "track_agenda": False, "auth_images": [],
    }
    row.update(changes)
    return row


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("primary,secondary,expected", [
    ("https://canvas.example/login", None, True),
    ("https://unknown.example/login", "https://canvas.example/login", True),
    ("https://powerschool.example/login", "https://canvas.example/login", True),
    ("https://canvas.example/login", "https://canvas.example/login", True),
    ("https://powerschool.example/login", None, False),
    ("https://unknown.example/login", None, False),
])
def test_capability_uses_slot_urls_not_legacy_metadata(flag, primary, secondary, expected):
    student = agenda.student_from_context(raw_student(
        portal1=primary, portal2=secondary,
        p2username="secondary user" if secondary else None,
        p2password="secondary secret" if secondary else None,
        track_agenda=flag,
    ))
    original = deepcopy(student)
    assert agenda.is_agenda_eligible(student) is expected
    assert student == original


@pytest.mark.parametrize("field", ["login_url", "id", "password"])
@pytest.mark.parametrize("blank", [None, "", " \t "])
def test_incomplete_slot_cannot_qualify(field, blank):
    student = agenda.student_from_context(raw_student())
    student[field] = blank
    assert not agenda.is_agenda_eligible(student)


def test_partial_secondary_does_not_suppress_primary():
    student = agenda.student_from_context(raw_student(
        portal2="https://canvas.example/login", p2username="secondary", p2password=" ",
    ))
    assert agenda.is_agenda_eligible(student)


def test_missing_engine_does_not_suppress_other_slot(monkeypatch):
    student = agenda.student_from_context(raw_student(
        portal2="https://parentvue.example/Login_Parent_PXP.aspx",
        p2username="secondary", p2password="secondary secret",
    ))
    real_get_portal = agenda.get_portal

    def lookup(key):
        if key == "canvas":
            raise ValueError("missing synthetic engine")
        return real_get_portal(key)

    monkeypatch.setattr(agenda, "get_portal", lookup)
    assert agenda.is_agenda_eligible(student)


def test_new_engine_capability_applies_on_next_evaluation(monkeypatch):
    engine = SimpleNamespace(agenda_capable=False)
    monkeypatch.setattr(agenda, "get_portal", lambda key: engine)
    student = agenda.student_from_context(raw_student())
    assert not agenda.is_agenda_eligible(student)
    engine.agenda_capable = True
    assert agenda.is_agenda_eligible(student)


@pytest.mark.parametrize("phase", ["conversion", "url", "capability"])
def test_preparation_errors_are_isolated_and_redacted(monkeypatch, caplog, phase):
    rows = [raw_student(1), raw_student(2)]
    secrets = ("PRIVATE_STUDENT", "PRIVATE_URL", "PRIVATE_PASSWORD", "PRIVATE_AUTH")
    message = " ".join(secrets)
    if phase == "conversion":
        original = agenda.student_from_context

        def convert(row):
            if row["crmstudentid"] == 1:
                raise RuntimeError(message)
            return original(row)

        monkeypatch.setattr(agenda, "student_from_context", convert)
    elif phase == "url":
        rows[0]["portal1"] = "https://unknown.example/PRIVATE_URL"
        original = agenda.get_portal_key_from_url

        def resolve(url):
            if "PRIVATE_URL" in url:
                raise RuntimeError(message)
            return original(url)

        monkeypatch.setattr(agenda, "get_portal_key_from_url", resolve)
    else:
        original = agenda.get_portal
        calls = 0

        def lookup(key):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError(message)
            return original(key)

        monkeypatch.setattr(agenda, "get_portal", lookup)

    caplog.set_level(logging.INFO, logger="scraper.agenda")
    context_filter = ContextFilter()
    caplog.handler.addFilter(context_filter)
    token = bind_log_context(student_name="PRIVATE_STUDENT", crmstudentid=999999)
    try:
        students, counts = agenda._prepare_agenda_students(rows)
        agenda._log_agenda_preparation(counts)
    finally:
        reset_log_context(token)
        caplog.handler.removeFilter(context_filter)

    assert [s["db_id"] for s in students] == [2]
    assert counts == {"candidate_count": 2, "eligible_count": 1,
                      "filtered_count": 0, "preparation_error_count": 1}
    record, = [r for r in caplog.records if r.getMessage() == "agenda.preparation.completed"]
    assert record.levelno == logging.WARNING
    assert not record.exc_info
    assert not hasattr(record, "crmstudentid")
    assert not hasattr(record, "student_name")
    assert all(secret not in repr(record.__dict__) for secret in secrets)
    assert all(getattr(record, key) == value for key, value in counts.items())


@pytest.mark.parametrize("signal", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
def test_preparation_does_not_swallow_process_signals(monkeypatch, signal):
    def stop(row):
        raise signal()

    monkeypatch.setattr(agenda, "student_from_context", stop)
    with pytest.raises(signal):
        agenda._prepare_agenda_students([raw_student()])


def test_preparation_logging_failure_does_not_abort_or_leak_context(monkeypatch):
    def fail_log(*args, **kwargs):
        raise RuntimeError("synthetic handler failure")

    monkeypatch.setattr(agenda.logger, "log", fail_log)
    token = bind_log_context(crmstudentid=999999)
    try:
        agenda._log_agenda_preparation({"candidate_count": 0, "eligible_count": 0,
                                       "filtered_count": 0, "preparation_error_count": 0})
        record = logging.makeLogRecord({})
        ContextFilter().filter(record)
        assert record.crmstudentid == 999999
    finally:
        reset_log_context(token)


@pytest.fixture
def job_harness(monkeypatch):
    state = SimpleNamespace(rows=[], starts=[], heartbeats=[], completed=[], posts=[],
                            launch_count=0, collection_count=0)

    class Client:
        def start_job(self, **kwargs):
            state.starts.append(kwargs)
            return {"job_id": "synthetic-job", "lease_token": "synthetic-lease",
                    "students": deepcopy(state.rows),
                    "progress": {"total": len(state.rows), "attempted": 0,
                                 "success": 0, "errors": 0}}

        def heartbeat(self, **kwargs):
            state.heartbeats.append(deepcopy(kwargs["progress"]))
            state.loop.call_soon_threadsafe(state.heartbeat_seen.set)
            return {"ok": True}

        def complete_job(self, **kwargs):
            state.completed.append(deepcopy(kwargs["progress"]))
            return {"ok": True}

        def post_result(self, **kwargs):
            state.posts.append(kwargs)
            return {"applied": True}

        def fail_job(self, **kwargs):
            pytest.fail("Unexpected job failure in synthetic lifecycle")

    class PlaywrightContext:
        async def __aenter__(self):
            async def launch(**kwargs):
                state.launch_count += 1
                return SimpleNamespace(close=AsyncMock())
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        async def __aexit__(self, *args):
            return False

    async def collect(client, session, browser, students, progress, lease_failed):
        state.collection_count += 1
        state.loop = asyncio.get_running_loop()
        state.heartbeat_seen = asyncio.Event()
        await asyncio.wait_for(state.heartbeat_seen.wait(), timeout=2)
        for student in students:
            await asyncio.to_thread(client.post_result, crmstudentid=student["db_id"])
            agenda._advance_progress(progress, success=True)
        return None

    monkeypatch.setattr(agenda, "GradeDbClient", Client)
    monkeypatch.setattr(agenda, "async_playwright", PlaywrightContext)
    monkeypatch.setattr(agenda, "_collect_and_post_agendas", collect)
    # Wait for an actual heartbeat; shorten only the interval in this test.
    monkeypatch.setattr("scraper.runner.HEARTBEAT_INTERVAL_SECONDS", 0.001)
    return state


def test_main_filters_before_collection_and_uses_filtered_totals(job_harness, caplog):
    good = raw_student(1)
    skipped = raw_student(2, portal1="https://powerschool.example/login")
    skipped.update(primary_agenda={"portal": "canvas", "weeks": {}},
                   primary_agenda_status="synced", primary_agenda_updated_at="2026-09-01",
                   secondary_agenda={"portal": None, "weeks": {}},
                   secondary_agenda_status="not_configured",
                   secondary_agenda_updated_at="2026-09-01")
    original = deepcopy(skipped)
    job_harness.rows = [good, skipped]
    caplog.set_level(logging.INFO, logger="scraper.agenda")
    result = asyncio.run(agenda.main(19, None))
    assert result == {"total": 1, "attempted": 1, "success": 1, "errors": 0}
    assert job_harness.completed == [result]
    assert job_harness.launch_count == 1
    assert [post["crmstudentid"] for post in job_harness.posts] == [1]
    assert skipped == original
    assert job_harness.heartbeats and all(p["total"] == 1 for p in job_harness.heartbeats)
    record, = [r for r in caplog.records if r.getMessage() == "agenda.preparation.completed"]
    assert record.levelno == logging.INFO
    assert (record.candidate_count, record.eligible_count, record.filtered_count,
            record.preparation_error_count) == (2, 1, 1, 0)
    restored = deepcopy(skipped)
    restored["portal1"] = "https://canvas.example/login"
    job_harness.rows = [restored]
    second_run = asyncio.run(agenda.main(19, None))
    assert second_run["total"] == second_run["success"] == 1
    assert [post["crmstudentid"] for post in job_harness.posts] == [1, 2]


@pytest.mark.parametrize("all_errors", [False, True])
def test_main_zero_eligible_never_starts_playwright(job_harness, monkeypatch, caplog, all_errors):
    job_harness.rows = [raw_student(portal1="https://powerschool.example/login")]
    if all_errors:
        def broken(row):
            raise RuntimeError("PRIVATE_PASSWORD")
        monkeypatch.setattr(agenda, "student_from_context", broken)

    def no_playwright():
        pytest.fail("Zero eligible students must not start Playwright")

    monkeypatch.setattr(agenda, "async_playwright", no_playwright)
    caplog.set_level(logging.INFO, logger="scraper.agenda")
    result = asyncio.run(agenda.main(19, None))
    assert result == {"total": 0, "attempted": 0, "success": 0, "errors": 0}
    assert job_harness.completed == [result]
    assert not job_harness.posts and job_harness.collection_count == 0
    record, = [r for r in caplog.records if r.getMessage() == "agenda.preparation.completed"]
    assert record.preparation_error_count == int(all_errors)
    assert record.filtered_count == int(not all_errors)
    assert "PRIVATE_PASSWORD" not in caplog.text


@pytest.mark.parametrize("phase", ["conversion", "selection"])
def test_main_continues_after_one_preparation_error(job_harness, monkeypatch, phase):
    job_harness.rows = [raw_student(1), raw_student(2)]
    name = "student_from_context" if phase == "conversion" else "is_agenda_eligible"
    original = getattr(agenda, name)

    def maybe_fail(value):
        if value.get("crmstudentid", value.get("db_id")) == 1:
            raise RuntimeError("synthetic preparation failure")
        return original(value)

    monkeypatch.setattr(agenda, name, maybe_fail)
    result = asyncio.run(agenda.main(19, None))
    assert result["total"] == result["success"] == 1
    assert [post["crmstudentid"] for post in job_harness.posts] == [2]


def test_main_empty_candidate_list_completes(job_harness, monkeypatch):
    def no_playwright():
        pytest.fail("Empty candidate list must not start Playwright")
    monkeypatch.setattr(agenda, "async_playwright", no_playwright)
    result = asyncio.run(agenda.main(19, None))
    assert result == {"total": 0, "attempted": 0, "success": 0, "errors": 0}
    assert job_harness.completed == [result]


def test_cli_initializes_logging_before_preparing_candidates(monkeypatch):
    import runpy

    from scraper import db_cli
    from scraper.config import logging as logging_config

    events = []

    class Client:
        def start_job(self, **kwargs):
            events.append("start")
            assert kwargs == {"kind": "agenda", "franchise_id": 19, "student_id": None}
            return {"job_id": "synthetic-job", "lease_token": "synthetic-lease", "students": []}

        def complete_job(self, **kwargs):
            events.append("complete")
            assert kwargs["progress"] == {"total": 0, "attempted": 0, "success": 0, "errors": 0}
            return {"ok": True}

    monkeypatch.setattr(db_cli, "GradeDbClient", Client)
    monkeypatch.setattr(logging_config, "configure_logging", lambda: events.append("logging"))
    monkeypatch.setattr("sys.argv", ["agenda", "--franchise-id", "19"])
    runpy.run_path(agenda.__file__, run_name="__main__")
    assert events == ["logging", "start", "complete"]
