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
        "known_course_titles": ["MARKETING 1", "ENGLISH 11"],
        "auth_type": "gps_pictograph",
        "auth_images": ["cat", "moon"],
        "grade_status": "never",
        "passwordgood": None,
    }


def test_context_mapping_preserves_legacy_scraper_shape_without_logging(capsys) -> None:
    student = runner.student_from_context(_context())

    assert student["db_id"] == 7
    assert student["login_url"] == "https://portal.example/login"
    assert student["id"] == "ada-user"
    assert student["alt_login_url"] == "https://classroom.google.com"
    assert student["alt_id"] == "ada-alt"
    assert student["auth_images"] == ["cat", "moon"]
    assert student["known_course_titles"] == ["MARKETING 1", "ENGLISH 11"]
    assert capsys.readouterr().out == ""


def test_diagnostic_failure_pauses_before_browser_state_is_closed(
    monkeypatch, capsys
) -> None:
    events: list[str] = []

    class Page:
        def set_default_timeout(self, _timeout):
            return None

        def set_default_navigation_timeout(self, _timeout):
            return None

        async def pause(self):
            events.append("pause")

        async def close(self):
            events.append("page.close")

    class Context:
        async def new_page(self):
            return Page()

        async def close(self):
            events.append("context.close")

    class Browser:
        async def new_context(self):
            return Context()

    class Engine:
        sid = "test-user"
        pw = "test-password"

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, *, first_name=None):
            raise ValueError("original diagnostic error")

    monkeypatch.setattr(runner, "get_portal", lambda _portal: Engine)
    monkeypatch.setattr(runner.random, "uniform", lambda _start, _end: 0)
    student = {
        "db_id": 7,
        "portal": "canvas",
        "login_url": "https://portal.example/login",
        "id": "test-user",
        "password": "test-password",
    }

    with pytest.raises(ValueError, match="original diagnostic error"):
        asyncio.run(runner.scrape_one(Browser(), student, diagnostic=True))

    assert events == ["pause", "page.close", "context.close"]
    assert "ValueError: original diagnostic error" in capsys.readouterr().err


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

    monkeypatch.setattr(runner, "MAX_CONCURRENT_GRADE_WORKERS", 1, raising=False)
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
        "channel": "grade",
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


def test_fatal_boundary_failure_marks_the_job_failed_and_propagates(
    monkeypatch,
) -> None:
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


def test_grade_job_limits_scrapes_and_serializes_result_posts(monkeypatch) -> None:
    import threading
    import time

    active_scrapes = 0
    peak_scrapes = 0
    active_posts = 0
    peak_posts = 0
    post_counter_lock = threading.Lock()
    posted_student_ids: list[int] = []

    class Client:
        def post_result(self, **kwargs):
            nonlocal active_posts, peak_posts
            with post_counter_lock:
                active_posts += 1
                peak_posts = max(peak_posts, active_posts)
            time.sleep(0.01)
            posted_student_ids.append(kwargs["crmstudentid"])
            with post_counter_lock:
                active_posts -= 1
            return {"applied": True, "duplicate": False}

    async def scrape(_browser, student):
        nonlocal active_scrapes, peak_scrapes
        active_scrapes += 1
        peak_scrapes = max(peak_scrapes, active_scrapes)
        await asyncio.sleep(0.01)
        active_scrapes -= 1
        return {
            "db_id": student["db_id"],
            "parsed_grades": {"Math": 95},
        }

    monkeypatch.setenv("GRADE_SCRAPER_WORKERS", "8")
    monkeypatch.setattr(runner, "MAX_CONCURRENT_GRADE_WORKERS", 2, raising=False)
    monkeypatch.setattr(runner, "scrape_one", scrape)
    students = [
        runner._student_from_context(_context(student_id))
        for student_id in range(7, 11)
    ]
    progress = runner._new_progress(len(students))

    failure = asyncio.run(
        runner._process_grade_students(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            students,
            progress,
            asyncio.Event(),
        )
    )

    assert failure is None
    assert peak_scrapes == 2
    assert peak_posts == 1
    assert sorted(posted_student_ids) == [7, 8, 9, 10]
    assert progress == {"total": 4, "attempted": 4, "success": 4, "errors": 0}


def test_grade_result_failure_cancels_active_and_waiting_workers(monkeypatch) -> None:
    started: list[int] = []
    cancelled: list[int] = []
    keep_running = asyncio.Event()

    class Client:
        def post_result(self, **_kwargs):
            raise GradeDbUnavailable("safe")

    async def scrape(_browser, student):
        student_id = student["db_id"]
        started.append(student_id)
        if student_id == 7:
            await asyncio.sleep(0.01)
            return {"db_id": student_id, "parsed_grades": {"Math": 95}}
        try:
            await keep_running.wait()
        except asyncio.CancelledError:
            cancelled.append(student_id)
            raise
        raise AssertionError("blocked scrape unexpectedly resumed")

    monkeypatch.setattr(runner, "MAX_CONCURRENT_GRADE_WORKERS", 2, raising=False)
    monkeypatch.setattr(runner, "scrape_one", scrape)
    students = [
        runner._student_from_context(_context(student_id))
        for student_id in range(7, 10)
    ]
    progress = runner._new_progress(len(students))

    failure = asyncio.run(
        runner._process_grade_students(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            students,
            progress,
            asyncio.Event(),
        )
    )

    assert failure == "neon_unavailable"
    assert sorted(started) == [7, 8]
    assert cancelled == [8]
    assert progress == {"total": 3, "attempted": 0, "success": 0, "errors": 0}
