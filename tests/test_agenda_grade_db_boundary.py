from __future__ import annotations

import asyncio
import logging

import pytest

from scraper import agenda
from scraper.portals import get_portal
from scraper.portals.infinite_campus import InfiniteCampus
from scraper.config.logging import ContextFilter
from scraper.db_cli import GradeDbUnavailable
from scraper.runner import _new_progress


def _json_value_nodes(value: object) -> int:
    if isinstance(value, dict):
        return 1 + sum(_json_value_nodes(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(_json_value_nodes(item) for item in value)
    return 1


def _student(student_id: int) -> dict:
    return {
        "db_id": student_id,
        "student_name": f"Student {student_id}",
        "portal": "gps",
        "login_url": "https://canvas.example/login",
        "id": f"user-{student_id}",
        "password": "primary-secret",
        "alt_login_url": "https://parentvue.example/Login_Parent_PXP.aspx",
        "alt_id": f"alt-user-{student_id}",
        "alt_password": "alternate-secret",
        "auth_images": [],
    }


class FakePage:
    def __init__(self, number: int, context: "FakeContext") -> None:
        self.number = number
        self.context = context
        self.close_calls = 0

    @property
    def closed(self) -> bool:
        return self.close_calls > 0

    async def close(self) -> None:
        self.close_calls += 1


class FakeContext:
    def __init__(self, browser: "FakeBrowser", number: int) -> None:
        self.browser = browser
        self.number = number
        self.pages: list[FakePage] = []
        self.close_calls = 0
        self.default_timeout: int | None = None
        self.default_navigation_timeout: int | None = None
        self.identity: str | None = None

    def set_default_timeout(self, timeout: int) -> None:
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: int) -> None:
        self.default_navigation_timeout = timeout

    async def new_page(self) -> FakePage:
        page = FakePage(len(self.browser.pages) + 1, self)
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.close_calls += 1


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []
        self._legacy_context: FakeContext | None = None

    @property
    def pages(self) -> list[FakePage]:
        return [page for context in self.contexts for page in context.pages]

    async def new_context(self) -> FakeContext:
        context = FakeContext(self, len(self.contexts) + 1)
        self.contexts.append(context)
        return context

    async def new_page(self) -> FakePage:
        if self._legacy_context is None:
            self._legacy_context = await self.new_context()
        return await self._legacy_context.new_page()


@pytest.mark.parametrize("cleanup_target", ["page", "context"])
@pytest.mark.parametrize("body_fails", [False, True])
def test_cleanup_cancellation_preserves_established_slot_outcome(
    monkeypatch, cleanup_target: str, body_fails: bool
) -> None:
    """Cancellation during cleanup cannot replace a success or body failure."""

    class CancellingPage(FakePage):
        async def close(self) -> None:
            await super().close()
            if cleanup_target == "page":
                raise asyncio.CancelledError

    class CancellingContext(FakeContext):
        async def new_page(self) -> FakePage:
            page = CancellingPage(len(self.browser.pages) + 1, self)
            self.pages.append(page)
            return page

        async def close(self) -> None:
            await super().close()
            if cleanup_target == "context":
                raise asyncio.CancelledError

    class CancellingBrowser(FakeBrowser):
        async def new_context(self) -> FakeContext:
            context = CancellingContext(self, len(self.contexts) + 1)
            self.contexts.append(context)
            return context

    class Engine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            if body_fails:
                raise RuntimeError("body failure")
            return []

    async def scenario() -> dict[str, object]:
        browser = CancellingBrowser()
        slot = agenda.resolve_agenda_slots(_student(7))[0]
        if body_fails:
            with pytest.raises(RuntimeError, match="^body failure$"):
                await agenda._collect_slot(browser, _student(7), slot)
        else:
            assert await agenda._collect_slot(browser, _student(7), slot) == {}

        record = logging.makeLogRecord({})
        ContextFilter().filter(record)
        assert not hasattr(record, "slot")
        assert not hasattr(record, "portal")
        return {"page": browser.pages[0], "context": browser.contexts[0]}

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    cleanup = asyncio.run(scenario())

    assert cleanup["page"].close_calls == 1
    assert cleanup["context"].close_calls == 1


def test_resolve_agenda_slots_preserves_source_order_over_legacy_portal() -> None:
    slots = agenda.resolve_agenda_slots(_student(7))

    assert [(slot.key, slot.portal) for slot in slots] == [
        ("agenda1", "canvas"),
        ("agenda2", "parentvue"),
    ]
    assert slots[0].username == "user-7"
    assert slots[1].username == "alt-user-7"


def test_infinite_campus_is_registered_as_agenda_capable_in_runner_slots() -> None:
    assert get_portal("infinite_campus") is InfiniteCampus
    assert InfiniteCampus.agenda_capable is True


def test_infinite_campus_slot_stays_first_and_keeps_credentials(monkeypatch) -> None:
    constructs: list[tuple[str, str, str, dict[str, object]]] = []

    class InfiniteEngine:
        agenda_capable = True

        def __init__(self, _page, username, password, login_url, **kwargs):
            constructs.append((username, password, login_url, kwargs))

        async def login(self, first_name=None):
            assert first_name == "Student 7"

        async def get_agenda(self):
            return []

    class InactiveEngine:
        agenda_capable = False

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            return []

    def get_portal_override(portal):
        return InfiniteEngine if portal == "infinite_campus" else InactiveEngine

    monkeypatch.setattr(agenda, "get_portal", get_portal_override)
    student = _student(7)
    student.update(
        login_url="https://ic.example/campus/portal",
        id="user-7",
        password="primary-secret",
        alt_login_url="https://parentvue.example/Login_Parent_PXP.aspx",
        alt_id="alt-user-7",
        alt_password="alternate-secret",
    )
    browser = FakeBrowser()

    bundle, returned_student = asyncio.run(agenda.fetch_agenda(browser, student))

    assert returned_student is student
    assert bundle["agenda1"]["portal"] == "infinite_campus"
    assert bundle["agenda2"]["portal"] == "parentvue"
    assert constructs == [
        (
            "user-7",
            "primary-secret",
            "https://ic.example/campus/portal",
            {
                "alt_portal_url": "https://parentvue.example/Login_Parent_PXP.aspx",
                "alt_student_id": "alt-user-7",
                "alt_password": "alternate-secret",
                "student_name": "Student 7",
            },
        )
    ]


def test_resolve_agenda_slots_normalizes_blank_credentials() -> None:
    student = _student(7)
    student.update(
        login_url="  ",
        id="\t",
        password="",
        alt_login_url=None,
        alt_id=None,
        alt_password="   ",
    )

    first, second = agenda.resolve_agenda_slots(student)

    assert (first.portal, first.login_url, first.username, first.password) == (
        None,
        None,
        None,
        None,
    )
    assert (second.portal, second.login_url, second.username, second.password) == (
        None,
        None,
        None,
        None,
    )


def test_two_canvas_slots_use_distinct_pages_and_keep_duplicate_assignments(
    monkeypatch,
) -> None:
    constructors: list[tuple[FakePage, str, str, str, dict]] = []
    record = {
        "course": "English 11",
        "title": "Reading response",
        "dueDate": "2026-08-16",
        "dueTime": None,
        "status": "due",
    }

    class Engine:
        agenda_capable = True

        def __init__(self, page, username, password, login_url, **kwargs):
            constructors.append((page, username, password, login_url, kwargs))

        async def login(self, first_name=None):
            assert first_name == "Student 7"

        async def get_agenda(self):
            return [record]

    monkeypatch.setattr(agenda, "get_portal", lambda portal: Engine)
    student = _student(7)
    student["alt_login_url"] = "https://district.instructure.com/login"
    browser = FakeBrowser()

    bundle, returned_student = asyncio.run(agenda.fetch_agenda(browser, student))

    assert returned_student is student
    assert bundle["agenda1"]["weeks"] == bundle["agenda2"]["weeks"]
    assert bundle["agenda1"]["weeks"]["2026-08-10"]["English 11"]["due"] == [
        {
            "title": "Reading response",
            "dueDate": "2026-08-16",
            "dueTime": None,
        }
    ]
    assert [call[0].number for call in constructors] == [1, 2]
    assert constructors[0][0] is not constructors[1][0]
    assert constructors[0][1:4] == (
        "user-7",
        "primary-secret",
        "https://canvas.example/login",
    )
    assert constructors[1][1:4] == (
        "alt-user-7",
        "alternate-secret",
        "https://district.instructure.com/login",
    )
    assert [call[4] for call in constructors] == [
        {
            "alt_portal_url": "https://district.instructure.com/login",
            "alt_student_id": "alt-user-7",
            "alt_password": "alternate-secret",
            "student_name": "Student 7",
        },
        {
            "alt_portal_url": "https://canvas.example/login",
            "alt_student_id": "user-7",
            "alt_password": "primary-secret",
            "student_name": "Student 7",
        },
    ]
    assert all(page.close_calls == 1 for page in browser.pages)


def test_fetch_agenda_canonicalizes_courses_against_known_titles(monkeypatch) -> None:
    class Engine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            return [
                {
                    "course": "Period 4, MKTG 1",
                    "title": "Campaign brief",
                    "dueDate": "2026-08-18",
                    "dueTime": None,
                    "status": "due",
                }
            ]

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    student = _student(7)
    student["alt_login_url"] = None
    student["alt_id"] = None
    student["alt_password"] = None
    student["known_course_titles"] = ["MARKETING 1"]

    bundle, _ = asyncio.run(agenda.fetch_agenda(FakeBrowser(), student))

    assert list(bundle["agenda1"]["weeks"]["2026-08-17"]) == ["MARKETING 1"]


def test_two_slots_are_independently_bounded_below_rust_result_limit(
    monkeypatch,
) -> None:
    """Would fail if one busy slot can overflow or consume the other slot's budget."""

    class Engine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            return [
                {
                    "sourceId": f"assignment-{index:03d}",
                    "course": "Busy Course",
                    "title": f"Work {index:03d}",
                    "dueDate": "2026-08-16",
                    "dueTime": None,
                    "status": "due",
                }
                for index in range(124)
            ]

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)

    bundle, _ = asyncio.run(agenda.fetch_agenda(FakeBrowser(), _student(7)))

    for slot in ("agenda1", "agenda2"):
        rows = bundle[slot]["weeks"]["2026-08-10"]["Busy Course"]["due"]
        assert len(rows) == 122
        assert rows[0]["title"] == "Work 000"
        assert rows[-1]["title"] == "Work 121"
    assert bundle["agenda1"]["weeks"] == bundle["agenda2"]["weeks"]
    assert _json_value_nodes(bundle) == 993


def test_concurrent_same_origin_slots_use_isolated_contexts(monkeypatch) -> None:
    """Would fail if same-origin slot logins can overwrite shared context state."""
    ready = asyncio.Event()
    arrivals: list[str] = []
    observed: dict[str, str | None] = {}

    class Engine:
        agenda_capable = True

        def __init__(self, page, username, *_args, **_kwargs):
            self.page = page
            self.username = username

        async def login(self, first_name=None):
            self.page.context.identity = self.username
            arrivals.append(self.username)
            if len(arrivals) == 2:
                ready.set()
            await ready.wait()

        async def get_agenda(self):
            observed[self.username] = self.page.context.identity
            return []

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    student = _student(7)
    student["alt_login_url"] = "https://canvas.example/alternate-login"
    browser = FakeBrowser()

    asyncio.run(agenda.fetch_agenda(browser, student))

    assert observed == {"user-7": "user-7", "alt-user-7": "alt-user-7"}
    assert len(browser.contexts) == 2
    assert all(context.default_timeout == 15_000 for context in browser.contexts)
    assert all(
        context.default_navigation_timeout == 15_000
        for context in browser.contexts
    )
    assert all(context.close_calls == 1 for context in browser.contexts)
    assert all(page.close_calls == 1 for page in browser.pages)


def test_google_slot_uses_opposite_gps_credentials_and_copied_auth_images(
    monkeypatch,
) -> None:
    """Would fail if Google uses its own slot as GPS fallback or shares auth images."""
    constructed: list[tuple[str, str, str, dict]] = []

    class GpsEngine:
        agenda_capable = False

    class GoogleEngine:
        agenda_capable = True

        def __init__(self, _page, username, password, login_url, **kwargs):
            constructed.append((username, password, login_url, kwargs))
            kwargs["auth_images"].append("mutated-by-engine")

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            return []

    def get_portal(portal: str):
        return GpsEngine if portal == "gps" else GoogleEngine

    monkeypatch.setattr(agenda, "get_portal", get_portal)
    student = _student(7)
    student.update(
        login_url="https://gpsportal.example/login",
        id="gps-user",
        password="gps-password",
        alt_login_url="https://classroom.google.com",
        alt_id="google-user",
        alt_password="google-password",
        auth_images=["circle", "triangle", "star"],
    )

    bundle, _ = asyncio.run(agenda.fetch_agenda(FakeBrowser(), student))

    assert bundle["agenda2"]["weeks"] == {}
    assert constructed == [
        (
            "google-user",
            "google-password",
            "https://classroom.google.com",
            {
                "alt_portal_url": "https://gpsportal.example/login",
                "alt_student_id": "gps-user",
                "alt_password": "gps-password",
                "student_name": "Student 7",
                "auth_images": ["circle", "triangle", "star", "mutated-by-engine"],
            },
        )
    ]
    assert student["auth_images"] == ["circle", "triangle", "star"]
    assert constructed[0][3]["auth_images"] is not student["auth_images"]


@pytest.mark.parametrize("cleanup_target", ["page", "context"])
def test_agenda_success_survives_cleanup_failure(monkeypatch, cleanup_target: str) -> None:
    """Would fail if cleanup replaces a successfully collected agenda."""

    class CleanupFailingPage(FakePage):
        async def close(self) -> None:
            await super().close()
            if cleanup_target == "page":
                raise RuntimeError("cleanup failure")

    class CleanupFailingContext(FakeContext):
        async def new_page(self) -> FakePage:
            page = CleanupFailingPage(len(self.browser.pages) + 1, self)
            self.pages.append(page)
            return page

        async def close(self) -> None:
            await super().close()
            if cleanup_target == "context":
                raise RuntimeError("cleanup failure")

    class CleanupFailingBrowser(FakeBrowser):
        async def new_context(self) -> FakeContext:
            context = CleanupFailingContext(self, len(self.contexts) + 1)
            self.contexts.append(context)
            return context

    class Engine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            return []

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    student = _student(7)
    student.update(alt_login_url=None, alt_id=None, alt_password=None)

    bundle, _ = asyncio.run(agenda.fetch_agenda(CleanupFailingBrowser(), student))

    assert bundle["agenda1"]["weeks"] == {}


def test_agenda_slot_failure_state_survives_cleanup_failure(monkeypatch) -> None:

    class CleanupFailingPage(FakePage):
        async def close(self) -> None:
            await super().close()
            raise RuntimeError("cleanup failure")

    class CleanupFailingContext(FakeContext):
        async def new_page(self) -> FakePage:
            page = CleanupFailingPage(len(self.browser.pages) + 1, self)
            self.pages.append(page)
            return page

        async def close(self) -> None:
            await super().close()
            raise RuntimeError("cleanup failure")

    class CleanupFailingBrowser(FakeBrowser):
        async def new_context(self) -> FakeContext:
            context = CleanupFailingContext(self, len(self.contexts) + 1)
            self.contexts.append(context)
            return context

    class FailingEngine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            raise RuntimeError("collection failure")

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: FailingEngine)
    student = _student(7)
    student.update(alt_login_url=None, alt_id=None, alt_password=None)

    result, _ = asyncio.run(agenda.fetch_agenda(CleanupFailingBrowser(), student))

    assert result.failures == {"agenda1": "scrape_failed"}


def test_agenda_login_failure_is_assigned_to_its_slot(monkeypatch) -> None:
    class Engine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            raise agenda.LoginError("portal login rejected")

        async def get_agenda(self):
            raise AssertionError("agenda collection should not start")

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    student = _student(7)
    student.update(alt_login_url=None, alt_id=None, alt_password=None)

    result, _ = asyncio.run(agenda.fetch_agenda(FakeBrowser(), student))

    assert result.failures == {"agenda1": "bad_login"}


def test_concurrent_same_origin_students_use_isolated_contexts(monkeypatch) -> None:
    """Would fail if concurrent students share cookies or local-storage identity."""
    ready = asyncio.Event()
    arrivals: list[str] = []
    observed: dict[str, str | None] = {}
    posts: list[dict] = []

    class Engine:
        agenda_capable = True

        def __init__(self, page, username, *_args, **_kwargs):
            self.page = page
            self.username = username

        async def login(self, first_name=None):
            self.page.context.identity = self.username
            arrivals.append(self.username)
            if len(arrivals) == 2:
                ready.set()
            await ready.wait()

        async def get_agenda(self):
            observed[self.username] = self.page.context.identity
            return []

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {"applied": True, "duplicate": False}

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    students = [_student(7), _student(8)]
    for student in students:
        student.update(alt_login_url=None, alt_id=None, alt_password=None)
    browser = FakeBrowser()

    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            browser,
            students,
            _new_progress(2),
            asyncio.Event(),
        )
    )

    assert failure is None
    assert len(posts) == 2
    assert observed == {"user-7": "user-7", "user-8": "user-8"}
    assert len(browser.contexts) == 2
    assert all(context.close_calls == 1 for context in browser.contexts)
    assert all(page.close_calls == 1 for page in browser.pages)


def test_agenda_job_limits_active_portal_workers(monkeypatch) -> None:
    active = 0
    peak_active = 0
    posts = []

    class Engine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            nonlocal active, peak_active
            active += 1
            peak_active = max(peak_active, active)
            await asyncio.sleep(0)

        async def get_agenda(self):
            nonlocal active
            await asyncio.sleep(0)
            active -= 1
            return []

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {"applied": True, "duplicate": False}

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    students = [_student(student_id) for student_id in range(1, 5)]
    for student in students:
        student.update(alt_login_url=None, alt_id=None, alt_password=None)

    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            FakeBrowser(),
            students,
            _new_progress(len(students)),
            asyncio.Event(),
        )
    )

    assert failure is None
    assert peak_active == min(agenda.MAX_CONCURRENT_AGENDA_WORKERS, len(students))
    assert len(posts) == len(students)


@pytest.mark.parametrize(
    ("portal", "url"),
    [
        ("canvas", "https://canvas.example/login"),
        ("parentvue", "https://parentvue.example/Login_Parent_PXP.aspx"),
        ("google_classroom", "https://classroom.google.com"),
    ],
)
@pytest.mark.parametrize("slot_number", [1, 2])
def test_capable_portals_dispatch_from_either_slot(
    monkeypatch, portal: str, url: str, slot_number: int
) -> None:
    constructed: list[str] = []

    def engine_for(key: str):
        class Engine:
            agenda_capable = True

            def __init__(self, _page, _username, _password, _login_url, **_kwargs):
                constructed.append(key)

            async def login(self, first_name=None):
                pass

            async def get_agenda(self):
                return []

        return Engine

    engines = {
        key: engine_for(key) for key in ("canvas", "parentvue", "google_classroom")
    }
    monkeypatch.setattr(agenda, "get_portal", engines.__getitem__)
    student = _student(7)
    student.update(
        login_url=None,
        id=None,
        password=None,
        alt_login_url=None,
        alt_id=None,
        alt_password=None,
    )
    if slot_number == 1:
        student.update(login_url=url, id="primary-user", password="primary-pass")
    else:
        student.update(alt_login_url=url, alt_id="alt-user", alt_password="alt-pass")

    bundle, _ = asyncio.run(agenda.fetch_agenda(FakeBrowser(), student))

    assert constructed == [portal]
    assert bundle[f"agenda{slot_number}"] == {"portal": portal, "weeks": {}}


def test_unsupported_and_unidentified_slots_remain_in_empty_bundle() -> None:
    student = _student(7)
    student.update(
        login_url="https://district.powerschool.example/login",
        alt_login_url="https://unknown.example/login",
    )
    browser = FakeBrowser()

    bundle, _ = asyncio.run(agenda.fetch_agenda(browser, student))

    assert bundle == {
        "agenda1": {"portal": "powerschool", "weeks": {}},
        "agenda2": {"portal": None, "weeks": {}},
    }
    assert browser.contexts == []
    assert browser.pages == []


def test_unsupported_configured_slot_posts_its_failure_state() -> None:
    posts = []
    student = _student(7)
    student.update(
        login_url="https://district.powerschool.example/login",
        alt_login_url=None,
        alt_id=None,
        alt_password=None,
    )
    browser = FakeBrowser()

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {"applied": True, "duplicate": False}

    progress = _new_progress(1)
    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            browser,
            [student],
            progress,
            asyncio.Event(),
        )
    )

    assert failure is None
    assert len(posts) == 1
    assert posts[0]["outcome"] == {
        "kind": "failure",
        "channel": "primary_agenda",
        "code": "unsupported_portal",
    }
    assert browser.contexts == []
    assert browser.pages == []
    assert progress == {"total": 1, "attempted": 1, "success": 0, "errors": 1}


def test_slot_failure_preserves_the_other_slot_and_posts_only_safe_state(monkeypatch, caplog) -> None:
    posts = []

    class SuccessEngine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            return [
                {
                    "course": "Sentinel Course",
                    "title": "Sentinel Assignment",
                    "dueDate": "2026-08-16",
                    "dueTime": None,
                    "status": "due",
                }
            ]

    class FailureEngine(SuccessEngine):
        async def get_agenda(self):
            raise RuntimeError("sentinel-cookie sentinel-token")

    def get_portal(key):
        return SuccessEngine if key == "canvas" else FailureEngine

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {"applied": True, "duplicate": False}

    monkeypatch.setattr(agenda, "get_portal", get_portal)
    student = _student(7)
    student.update(
        id="sentinel-user",
        password="sentinel-password",
        alt_login_url=(
            "https://parentvue.example/Login_Parent_PXP.aspx?value=sentinel-query"
        ),
    )
    browser = FakeBrowser()

    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            browser,
            [student],
            _new_progress(1),
            asyncio.Event(),
        )
    )

    assert failure is None
    assert len(posts) == 2
    outcomes = [post["outcome"] for post in posts]
    assert any(outcome["kind"] == "primary_agenda_success" for outcome in outcomes)
    assert {
        "kind": "failure",
        "channel": "secondary_agenda",
        "code": "scrape_failed",
    } in outcomes
    assert len(browser.pages) == 2
    assert all(page.close_calls == 1 for page in browser.pages)
    assert len(browser.contexts) == 2
    assert all(context.close_calls == 1 for context in browser.contexts)
    exposed = repr(agenda.resolve_agenda_slots(student)) + str(posts) + caplog.text
    for secret in (
        "sentinel-user",
        "sentinel-password",
        "sentinel-query",
        "sentinel-cookie",
        "sentinel-token",
    ):
        assert secret not in exposed


def test_completed_slot_is_posted_while_other_slot_is_still_running(monkeypatch) -> None:
    primary_posted = asyncio.Event()
    browser_closed = asyncio.Event()
    posts = []

    class Engine:
        agenda_capable = True

        def __init__(self, _page, username, *_args, **_kwargs):
            self.username = username

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            if self.username == "user-7":
                return []
            await browser_closed.wait()
            raise RuntimeError("browser closed")

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            if kwargs["outcome"]["kind"] == "primary_agenda_success":
                primary_posted.set()
            return {"applied": True, "duplicate": False}

    async def scenario() -> str | None:
        run = asyncio.create_task(
            agenda._collect_and_post_agendas(
                Client(),
                {"job_id": "job", "lease_token": "lease"},
                FakeBrowser(),
                [_student(7)],
                _new_progress(1),
                asyncio.Event(),
            )
        )
        await asyncio.wait_for(primary_posted.wait(), timeout=1)
        assert not run.done()
        assert [post["outcome"]["kind"] for post in posts] == [
            "primary_agenda_success"
        ]
        browser_closed.set()
        return await run

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)

    assert asyncio.run(scenario()) is None
    assert [post["outcome"] for post in posts] == [
        {
            "kind": "primary_agenda_success",
            "agenda": {"portal": "canvas", "weeks": {}},
        },
        {
            "kind": "failure",
            "channel": "secondary_agenda",
            "code": "scrape_failed",
        },
    ]


def test_neon_failure_closes_started_slot_contexts_and_pages_once(monkeypatch) -> None:
    """Would fail if database cancellation leaks a started slot session."""
    pending_started = asyncio.Event()
    pending_cancelled = asyncio.Event()

    class Engine:
        agenda_capable = True

        def __init__(self, _page, username, *_args, **_kwargs):
            self.username = username

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            if self.username == "user-7":
                await pending_started.wait()
                return []
            pending_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pending_cancelled.set()
                raise

    class Client:
        def post_result(self, **_kwargs):
            raise GradeDbUnavailable("safe")

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    students = [_student(7), _student(8)]
    for student in students:
        student.update(alt_login_url=None, alt_id=None, alt_password=None)
    browser = FakeBrowser()

    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            browser,
            students,
            _new_progress(2),
            asyncio.Event(),
        )
    )

    assert failure == "neon_unavailable"
    assert pending_cancelled.is_set()
    assert len(browser.contexts) == 2
    assert all(context.close_calls == 1 for context in browser.contexts)
    assert all(page.close_calls == 1 for page in browser.pages)


def test_lease_failure_closes_started_slot_context_and_page_once(monkeypatch) -> None:
    """Would fail if lease cancellation leaks a started slot session."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Engine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            pass

        async def get_agenda(self):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    async def scenario(browser: FakeBrowser) -> str | None:
        lease_failed = asyncio.Event()

        async def fail_lease() -> None:
            await started.wait()
            lease_failed.set()

        lease_task = asyncio.create_task(fail_lease())
        result = await agenda._collect_and_post_agendas(
            object(),
            {"job_id": "job", "lease_token": "lease"},
            browser,
            [
                {
                    **_student(7),
                    "alt_login_url": None,
                    "alt_id": None,
                    "alt_password": None,
                }
            ],
            _new_progress(1),
            lease_failed,
        )
        await lease_task
        return result

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    browser = FakeBrowser()

    assert asyncio.run(scenario(browser)) == "lease_renewal_failed"
    assert cancelled.is_set()
    assert len(browser.contexts) == 1
    assert browser.contexts[0].close_calls == 1
    assert browser.pages[0].close_calls == 1


def test_agenda_neon_failure_cancels_pending_collection(monkeypatch) -> None:
    cancelled = asyncio.Event()

    async def fetch(_context, student, **_kwargs):
        if student["db_id"] == 7:
            return agenda.AgendaFetchResult(
                bundle={
                    "agenda1": {"portal": "canvas", "weeks": {}},
                    "agenda2": {"portal": "parentvue", "weeks": {}},
                },
                attempted_slots=("agenda1", "agenda2"),
                failures={},
            ), student
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    class Client:
        def post_result(self, **_kwargs):
            raise GradeDbUnavailable("safe")

    monkeypatch.setattr(agenda, "fetch_agenda", fetch)
    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            [_student(7), _student(8)],
            _new_progress(2),
            asyncio.Event(),
        )
    )

    assert failure == "neon_unavailable"
    assert cancelled.is_set()


def test_lease_failure_cancels_outstanding_collection(monkeypatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fetch(_context, student, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def scenario():
        lease_failed = asyncio.Event()

        async def fail_lease():
            await started.wait()
            lease_failed.set()

        lease_task = asyncio.create_task(fail_lease())
        result = await agenda._collect_and_post_agendas(
            object(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            [_student(7)],
            _new_progress(1),
            lease_failed,
        )
        await lease_task
        return result

    monkeypatch.setattr(agenda, "fetch_agenda", fetch)

    assert asyncio.run(scenario()) == "lease_renewal_failed"
    assert cancelled.is_set()


def test_heartbeat_failure_prevents_agenda_tasks_from_starting(monkeypatch) -> None:
    started = []

    async def fetch(_context, student, **_kwargs):
        started.append(student["db_id"])
        return {}, student

    monkeypatch.setattr(agenda, "fetch_agenda", fetch)
    lease_failed = asyncio.Event()
    lease_failed.set()

    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            object(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            [_student(7)],
            _new_progress(1),
            lease_failed,
        )
    )

    assert failure == "lease_renewal_failed"
    assert started == []


def test_browser_cleanup_after_collection_does_not_fail_agenda_job(monkeypatch) -> None:
    completed = []
    failed = []

    class Client:
        def start_job(self, **_kwargs):
            return {
                "job_id": "job",
                "lease_token": "lease",
                "students": [{"crmstudentid": 7}],
            }

        def complete_job(self, **kwargs):
            completed.append(kwargs)
            return {"ok": True}

        def fail_job(self, **kwargs):
            failed.append(kwargs)

    class Browser:
        async def close(self):
            raise RuntimeError("browser was closed by the user")

    class Playwright:
        class Chromium:
            async def launch(self, **_kwargs):
                return Browser()

        chromium = Chromium()

    class PlaywrightContext:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *_args):
            raise RuntimeError("playwright cleanup after browser close")

    async def collect(*args, **_kwargs):
        progress = args[4]
        progress.update(attempted=1, success=0, errors=1)
        return None

    monkeypatch.setattr(agenda, "GradeDbClient", Client)
    monkeypatch.setattr(agenda, "async_playwright", PlaywrightContext)
    monkeypatch.setattr(agenda, "student_from_context", lambda _row: _student(7))
    monkeypatch.setattr(agenda, "_collect_and_post_agendas", collect)

    result = asyncio.run(agenda.main(franchise_id=19, student_id=None))

    assert result == {"total": 1, "attempted": 1, "success": 0, "errors": 1}
    assert len(completed) == 1
    assert failed == []
