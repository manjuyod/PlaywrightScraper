from __future__ import annotations

import asyncio

import pytest

from scraper import runner
from scraper.db_cli import GradeDbUnavailable


def _context(student_id: int = 7) -> dict:
    return {
        "crmstudentid": student_id,
        "franchiseid": 19,
        "firstname": "Ada",
        "lastname": "Lovelace",
        "grade": 10,
        "portal1": "https://portal.example/login",
        "p1username": "ada-user",
        "p1password": "primary-secret",
        "portal2": "https://classroom.google.com",
        "p2username": "ada-alt",
        "p2password": "alternate-secret",
        "portal": "gps",
        "track_agenda": True,
        "auth_type": "gps_pictograph",
        "auth_images": ["cat", "moon"],
        "status": "never",
        "passwordgood": None,
    }


def test_context_mapping_preserves_legacy_scraper_shape_without_logging(capsys) -> None:
    student = runner._student_from_context(_context())

    assert student["db_id"] == 7
    assert student["id"] == "ada-user"
    assert student["alt_id"] == "ada-alt"
    assert student["auth_images"] == ["cat", "moon"]
    assert capsys.readouterr().out == ""


def test_each_success_is_posted_immediately(monkeypatch) -> None:
    posts: list[dict] = []

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {"applied": True, "duplicate": False}

    async def scrape(_browser, student):
        assert posts == []
        return {
            "db_id": student["db_id"],
            "id": student["id"],
            "parsed_grades": {"Math": 95},
        }

    monkeypatch.setattr(runner, "scrape_one", scrape)
    progress = runner._new_progress(1)

    result = asyncio.run(
        runner._process_grade_students(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            [runner._student_from_context(_context())],
            progress,
            asyncio.Event(),
        )
    )

    assert result is None
    assert posts[0]["outcome"]["kind"] == "grade_success"
    assert posts[0]["outcome"]["parsed_grades"]["Math"] == 95
    assert progress == {"total": 1, "attempted": 1, "success": 1, "errors": 0}


def test_neon_failure_stops_scheduling_new_students(monkeypatch) -> None:
    scraped: list[int] = []

    class Client:
        def post_result(self, **_kwargs):
            raise GradeDbUnavailable("safe")

    async def scrape(_browser, student):
        scraped.append(student["db_id"])
        return {"db_id": student["db_id"], "parsed_grades": {"week": {}}}

    monkeypatch.setattr(runner, "scrape_one", scrape)
    students = [
        runner._student_from_context(_context(7)),
        runner._student_from_context(_context(8)),
    ]

    failure = asyncio.run(
        runner._process_grade_students(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            students,
            runner._new_progress(2),
            asyncio.Event(),
        )
    )

    assert failure == "neon_unavailable"
    assert scraped == [7]


def test_login_errors_post_only_a_sanitized_failure_code(monkeypatch) -> None:
    posts: list[dict] = []

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {"applied": True, "duplicate": False}

    async def scrape(_browser, _student):
        raise runner.LoginError("contains primary-secret")

    monkeypatch.setattr(runner, "scrape_one", scrape)
    asyncio.run(
        runner._process_grade_students(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            [runner._student_from_context(_context())],
            runner._new_progress(1),
            asyncio.Event(),
        )
    )

    assert posts[0]["outcome"] == {
        "kind": "failure",
        "code": "bad_login",
        "passwordgood": False,
    }
    assert "primary-secret" not in str(posts)


def test_heartbeat_failure_sets_the_stop_scheduling_signal(monkeypatch) -> None:
    class Client:
        def heartbeat(self, **_kwargs):
            raise GradeDbUnavailable("safe")

    async def scenario():
        stop = asyncio.Event()
        failed = asyncio.Event()
        await runner._heartbeat_loop(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            runner._new_progress(1),
            stop,
            failed,
        )
        return failed.is_set()

    monkeypatch.setattr(runner, "HEARTBEAT_INTERVAL_SECONDS", 0.001)
    assert asyncio.run(scenario()) is True


def test_fatal_boundary_failure_marks_the_job_failed_and_propagates(monkeypatch) -> None:
    failed = []
    notifications = []

    class Client:
        def start_job(self, **_kwargs):
            return {
                "job_id": "job",
                "lease_token": "lease",
                "students": [_context()],
            }

        def fail_job(self, **kwargs):
            failed.append(kwargs)

    class Browser:
        async def close(self):
            return None

    class Chromium:
        async def launch(self, **_kwargs):
            return Browser()

    class Playwright:
        chromium = Chromium()

    class PlaywrightContext:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *_args):
            return None

    async def process(*_args, **_kwargs):
        return "neon_unavailable"

    async def notify(severity, message):
        notifications.append((severity, message))

    monkeypatch.setattr(runner, "GradeDbClient", Client)
    monkeypatch.setattr(runner, "async_playwright", PlaywrightContext)
    monkeypatch.setattr(runner, "_process_grade_students", process)
    monkeypatch.setattr(runner, "_send_slack_notification", notify)

    with pytest.raises(RuntimeError, match="neon_unavailable"):
        asyncio.run(runner.main(franchise_id=19))

    assert failed[0]["code"] == "neon_unavailable"
    assert notifications == [
        (
            runner.Severity.Crit,
            "Grade scraping stopped because of a fatal error.\n"
            "Failure code: neon_unavailable\n"
            "Exception type: RunnerFatalError",
        )
    ]


def test_startup_failure_sends_sanitized_fatal_notification(monkeypatch) -> None:
    notifications = []

    class Client:
        def start_job(self, **_kwargs):
            raise RuntimeError("database error containing primary-secret")

    async def notify(severity, message):
        notifications.append((severity, message))

    monkeypatch.setattr(runner, "GradeDbClient", Client)
    monkeypatch.setattr(runner, "_send_slack_notification", notify)

    with pytest.raises(RuntimeError, match="primary-secret"):
        asyncio.run(runner.main(franchise_id=19))

    assert len(notifications) == 1
    severity, message = notifications[0]
    assert severity is runner.Severity.Crit
    assert "unhandled_exception" in message
    assert "RuntimeError" in message
    assert "primary-secret" not in message
