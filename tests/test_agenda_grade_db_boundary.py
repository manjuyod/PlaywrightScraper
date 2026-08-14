from __future__ import annotations

import asyncio

import pytest

from scraper import agenda
from scraper.db_cli import GradeDbUnavailable
from scraper.runner import _new_progress


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
    def __init__(self, number: int) -> None:
        self.number = number
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage(len(self.pages) + 1)
        self.pages.append(page)
        return page


def test_resolve_agenda_slots_preserves_source_order_over_legacy_portal() -> None:
    slots = agenda.resolve_agenda_slots(_student(7))

    assert [(slot.key, slot.portal) for slot in slots] == [
        ("agenda1", "canvas"),
        ("agenda2", "parentvue"),
    ]
    assert slots[0].username == "user-7"
    assert slots[1].username == "alt-user-7"


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
    context = FakeContext()

    bundle, returned_student = asyncio.run(agenda.fetch_agenda(context, student))

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
        {"student_name": "Student 7"},
        {"student_name": "Student 7"},
    ]
    assert all(page.closed for page in context.pages)


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

    bundle, _ = asyncio.run(agenda.fetch_agenda(FakeContext(), student))

    assert constructed == [portal]
    assert bundle[f"agenda{slot_number}"] == {"portal": portal, "weeks": {}}


def test_unsupported_and_unidentified_slots_remain_in_empty_bundle() -> None:
    student = _student(7)
    student.update(
        login_url="https://district.powerschool.example/login",
        alt_login_url="https://unknown.example/login",
    )
    context = FakeContext()

    bundle, _ = asyncio.run(agenda.fetch_agenda(context, student))

    assert bundle == {
        "agenda1": {"portal": "powerschool", "weeks": {}},
        "agenda2": {"portal": None, "weeks": {}},
    }
    assert context.pages == []


def test_no_worker_slots_post_exactly_one_empty_success() -> None:
    posts = []
    student = _student(7)
    student.update(
        login_url="https://district.powerschool.example/login",
        alt_login_url=None,
        alt_id=None,
        alt_password=None,
    )
    empty_bundle = {
        "agenda1": {"portal": "powerschool", "weeks": {}},
        "agenda2": {"portal": None, "weeks": {}},
    }
    context = FakeContext()

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {"applied": True, "duplicate": False}

    progress = _new_progress(1)
    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            context,
            [student],
            progress,
            asyncio.Event(),
        )
    )

    assert failure is None
    assert len(posts) == 1
    assert posts[0]["outcome"] == {
        "kind": "agenda_success",
        "weekly_agenda": empty_bundle,
    }
    assert context.pages == []
    assert progress == {"total": 1, "attempted": 1, "success": 1, "errors": 0}


def test_slot_failure_posts_only_safe_atomic_failure(monkeypatch, caplog) -> None:
    sentinels = [
        "sentinel-user",
        "sentinel-password",
        "Sentinel Course",
        "Sentinel Assignment",
        "sentinel-query",
        "sentinel-cookie",
        "sentinel-token",
    ]
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
    context = FakeContext()

    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            context,
            [student],
            _new_progress(1),
            asyncio.Event(),
        )
    )

    assert failure is None
    assert len(posts) == 1
    assert posts[0]["outcome"] == {
        "kind": "failure",
        "code": "agenda2_parentvue_failed",
        "passwordgood": None,
    }
    assert len(context.pages) == 2
    assert all(page.closed for page in context.pages)
    exposed = repr(agenda.resolve_agenda_slots(student)) + str(posts) + caplog.text
    assert all(sentinel not in exposed for sentinel in sentinels)


def test_agenda_neon_failure_cancels_pending_collection(monkeypatch) -> None:
    cancelled = asyncio.Event()

    async def fetch(_context, student):
        if student["db_id"] == 7:
            return {
                "agenda1": {"portal": "canvas", "weeks": {}},
                "agenda2": {"portal": "parentvue", "weeks": {}},
            }, student
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

    async def fetch(_context, student):
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

    async def fetch(_context, student):
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
