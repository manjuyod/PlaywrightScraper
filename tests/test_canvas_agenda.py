from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from scraper.agenda_contract import normalize_agenda
from scraper.portals import canvas
from scraper.portals.canvas import CanvasEngine
from scraper.portals.canvas_agenda import CanvasAgendaError, collect_canvas_agenda


COURSES_PAGE_1 = [{"id": 11, "name": "English 11"}]
COURSES_PAGE_2 = [{"id": 22, "name": "Algebra II"}]
MISSING_PAGE = [
    {
        "id": 701,
        "course_id": 11,
        "name": "Late reading",
        "due_at": "2026-08-11T06:30:00Z",
    },
    {
        "id": 999,
        "course_id": 11,
        "name": "Broken date",
        "due_at": "not-a-date",
    },
]
PLANNER_PAGE = [
    {
        "plannable_type": "assignment",
        "course_id": 11,
        "plannable": {
            "id": 701,
            "title": "Late reading",
            "due_at": "2026-08-11T06:30:00Z",
        },
    },
    {
        "plannable_type": "assignment",
        "course_id": 22,
        "plannable": {
            "id": 702,
            "title": "Practice set",
            "due_at": "2026-08-18T07:00:00Z",
        },
    },
    {
        "plannable_type": "calendar_event",
        "course_id": 22,
        "plannable": {
            "id": 703,
            "title": "Pep rally",
            "start_at": "2026-08-20T18:00:00Z",
        },
    },
]


class FakeResponse:
    def __init__(self, payload: object, *, ok: bool = True, link: str | None = None) -> None:
        self.ok = ok
        self.headers = {"link": link} if link else {}
        self._payload = payload
        self.disposed = False

    async def json(self) -> object:
        return self._payload

    async def dispose(self) -> None:
        self.disposed = True


class FakeRequest:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    async def get(self, url: str) -> FakeResponse:
        self.urls.append(url)
        return self.responses[url]


class FakePage:
    def __init__(self, request: FakeRequest, timezone: str = "America/Phoenix") -> None:
        self.context = SimpleNamespace(request=request)
        self.timezone = timezone

    async def evaluate(self, _: str) -> str:
        return self.timezone


def test_collects_paginated_local_time_agenda_and_preserves_assignment_identity() -> None:
    """Would fail if pagination, origin checks, local conversion, or source IDs regress."""
    origin = "https://canvas.example"
    courses_1 = f"{origin}/api/v1/courses?per_page=100&enrollment_state=active"
    courses_2 = f"{origin}/api/v1/courses?page=2"
    missing = f"{origin}/api/v1/users/self/missing_submissions?per_page=100"
    planner = (
        f"{origin}/api/v1/planner/items?per_page=100"
        "&start_date=2026-08-13T00%3A00%3A00-07%3A00"
        "&end_date=2027-08-13T00%3A00%3A00-07%3A00"
    )
    responses = {
        courses_1: FakeResponse(COURSES_PAGE_1, link=f"<{courses_2}>; rel=\"next\""),
        courses_2: FakeResponse(COURSES_PAGE_2),
        missing: FakeResponse(MISSING_PAGE),
        planner: FakeResponse(PLANNER_PAGE),
    }
    request = FakeRequest(responses)

    records = asyncio.run(collect_canvas_agenda(FakePage(request), origin, today=date(2026, 8, 13)))

    assert request.urls == [courses_1, courses_2, missing, planner]
    assert all(urlparse(url).scheme == "https" and urlparse(url).netloc == "canvas.example" for url in request.urls)
    assert [parse_qs(urlparse(url).query)["per_page"] for url in (courses_1, missing, planner)] == [
        ["100"], ["100"], ["100"]
    ]
    assert records == [
        {
            "course": "English 11",
            "title": "Late reading",
            "dueDate": "2026-08-10",
            "dueTime": "23:30",
            "status": "missing",
            "sourceId": "canvas:assignment:701",
        },
        {
            "course": "English 11",
            "title": "Late reading",
            "dueDate": "2026-08-10",
            "dueTime": "23:30",
            "status": "due",
            "sourceId": "canvas:assignment:701",
        },
        {
            "course": "Algebra II",
            "title": "Practice set",
            "dueDate": "2026-08-18",
            "dueTime": "00:00",
            "status": "due",
            "sourceId": "canvas:assignment:702",
        },
    ]
    assert normalize_agenda(records)["2026-08-10"]["English 11"]["missing"] == [
        {"title": "Late reading", "dueDate": "2026-08-10", "dueTime": "23:30"}
    ]
    assert CanvasEngine.agenda_capable is True


@pytest.mark.parametrize("payload", [None, {"not": "a list"}])
def test_rejects_malformed_top_level_payload_without_response_content(payload: object) -> None:
    """Would fail if a bad API payload leaks data or is silently treated as empty."""
    origin = "https://canvas.example"
    courses = f"{origin}/api/v1/courses?per_page=100&enrollment_state=active"
    response = FakeResponse(payload)
    request = FakeRequest({courses: response})

    with pytest.raises(CanvasAgendaError, match="^canvas_agenda_request_failed$") as error:
        asyncio.run(collect_canvas_agenda(FakePage(request), origin))

    assert str(payload) not in str(error.value)
    assert response.disposed


def test_rejects_http_error_without_response_content() -> None:
    """Would fail if failed HTTP requests expose raw bodies or continue collection."""
    origin = "https://canvas.example"
    courses = f"{origin}/api/v1/courses?per_page=100&enrollment_state=active"
    response = FakeResponse({"sensitive": "body"}, ok=False)

    with pytest.raises(CanvasAgendaError, match="^canvas_agenda_request_failed$"):
        asyncio.run(collect_canvas_agenda(FakePage(FakeRequest({courses: response})), origin))

    assert response.disposed


def test_canvas_engine_delegates_agenda_collection_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Would fail if Canvas falls back to DOM scraping or uses the wrong origin."""
    received: list[tuple[object, str]] = []
    expected = [{"course": "English 11", "title": "Reading", "dueDate": "2026-08-13", "dueTime": None, "status": "due"}]

    async def collect(page: object, origin: str) -> list[object]:
        received.append((page, origin))
        return expected

    monkeypatch.setattr(canvas, "collect_canvas_agenda", collect)
    page = object()

    assert asyncio.run(CanvasEngine(page, "Student.P1Username", "password", "https://canvas.example/login/canvas").get_agenda()) == expected
    assert received == [(page, "https://canvas.example")]
