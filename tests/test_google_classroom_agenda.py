from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from scraper.portals import google_classroom
from scraper.portals.google_classroom import GoogleClassroom


ASSIGNED_HTML = """
<ul>
  <li><a href="/c/123/a/stream-10/details">Details</a>
    <div data-course-id="123" data-stream-item-id="stream-10">
      <div class="y9bEQb"><p>Practice set</p><p class="tWeh6">Algebra II</p></div>
      <p class="pOf0gc">Due Aug 18</p>
    </div>
  </li>
</ul>
"""

MISSING_HTML = """
<ul>
  <li><a href="/c/456/a/stream-9/details">Details</a>
    <div data-course-id="456" data-stream-item-id="stream-9">
      <div class="y9bEQb"><p>Reading response</p><p class="tWeh6">English 11</p></div>
      <p class="pOf0gc">Due Aug 16, 11:59 PM</p>
    </div>
  </li>
</ul>
"""


class FakeControl:
    def __init__(self, page: "FakePage", name: str) -> None:
        self.page = page
        self.name = name

    async def click(self) -> None:
        self.page.clicks.append(self.name)
        if self.name in ("Assigned", "Missing"):
            self.page.current_tab = self.name

    async def count(self) -> int:
        return 1


class FakePage:
    def __init__(self) -> None:
        self.clicks: list[str] = []
        self.current_tab = "Assigned"

    async def wait_for_selector(self, _: str, timeout: int) -> None:
        _ = timeout

    def get_by_role(self, role: str, name: str) -> FakeControl:
        _ = role
        return FakeControl(self, name)

    def locator(self, _: str) -> FakeControl:
        return FakeControl(self, "Main Menu")

    async def content(self) -> str:
        return ASSIGNED_HTML if self.current_tab == "Assigned" else MISSING_HTML


def test_parser_normalizes_sanitized_assigned_and_missing_records() -> None:
    """Would fail if source IDs, due dates/times, or statuses stop being normalized."""
    reference = datetime(2026, 8, 13, 12, 0)

    records = google_classroom._parse_classroom_agenda(
        MISSING_HTML, "missing", reference=reference
    ) + google_classroom._parse_classroom_agenda(ASSIGNED_HTML, "due", reference=reference)

    assert records == [
        {
            "sourceId": "google_classroom:stream-9",
            "course": "English 11",
            "title": "Reading response",
            "dueDate": "2026-08-16",
            "dueTime": "23:59",
            "status": "missing",
        },
        {
            "sourceId": "google_classroom:stream-10",
            "course": "Algebra II",
            "title": "Practice set",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
        },
    ]


def test_get_agenda_collects_assigned_then_missing_tabs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Would fail if either To-do tab is skipped or missing cannot override due centrally."""
    page = FakePage()
    waits: list[str] = []

    async def wait_for_navigation(_: object, *, pattern: str, **__: object) -> None:
        waits.append(pattern)

    async def control_exists(_: object) -> bool:
        return True

    monkeypatch.setattr(google_classroom, "wait_after_nav", wait_for_navigation)
    monkeypatch.setattr(google_classroom, "exists", control_exists)

    records = asyncio.run(
        GoogleClassroom(page, "student", "password", "https://classroom.google.com").get_agenda()
    )

    assert GoogleClassroom.agenda_capable is True
    assert page.clicks == ["To-do", "Assigned", "Missing"]
    assert waits == ["**/a/not-turned-in/**", "**/a/not-turned-in/**", "**/a/missing/**"]
    assert [record["sourceId"] for record in records] == [
        "google_classroom:stream-10",
        "google_classroom:stream-9",
    ]


def test_get_agenda_raises_safe_code_when_navigation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Would fail if a navigation error returns a silent partial agenda."""
    async def navigation_failure(*_: object, **__: object) -> None:
        raise RuntimeError("sensitive navigation details")

    async def control_exists(_: object) -> bool:
        return True

    monkeypatch.setattr(google_classroom, "wait_after_nav", navigation_failure)
    monkeypatch.setattr(google_classroom, "exists", control_exists)

    error_type = getattr(google_classroom, "GoogleClassroomAgendaError")
    with pytest.raises(error_type, match="^google_classroom_agenda_failed$"):
        asyncio.run(
            GoogleClassroom(FakePage(), "student", "password", "https://classroom.google.com").get_agenda()
        )


def test_get_agenda_raises_safe_code_when_parser_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Would fail if a parser error leaks content or returns an incomplete result."""
    async def wait_for_navigation(*_: object, **__: object) -> None:
        return None

    async def control_exists(_: object) -> bool:
        return True

    def parser_failure(*_: object, **__: object) -> list[object]:
        raise ValueError("raw page contents")

    monkeypatch.setattr(google_classroom, "wait_after_nav", wait_for_navigation)
    monkeypatch.setattr(google_classroom, "exists", control_exists)
    monkeypatch.setattr(google_classroom, "_parse_classroom_agenda", parser_failure)

    error_type = getattr(google_classroom, "GoogleClassroomAgendaError")
    with pytest.raises(error_type, match="^google_classroom_agenda_failed$") as error:
        asyncio.run(
            GoogleClassroom(FakePage(), "student", "password", "https://classroom.google.com").get_agenda()
        )

    assert "raw page contents" not in str(error.value)
