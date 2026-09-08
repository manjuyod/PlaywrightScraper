from __future__ import annotations

import asyncio
from datetime import datetime
import importlib

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeout

from scraper.portals.parentvue_agenda import ParentVueAgendaError


OVERVIEW_HTML = '''<div id="gb-assignments">
  <section>
    <h2 class="title">Upcoming Assignments</h2>
    <div class="gb-student-assignments-grid"><table><tbody>
      <tr class="gb-upcoming-assignment" data-guid="upcoming-1"><td>
        <div><a>Upcoming review</a></div>
        <div>Overview Course</div>
        <div>Due Date: 08/18/2026</div>
      </td></tr>
    </tbody></table></div>
  </section>
  <div class="gb-class-header gb-class-row"><button class="course-title">First Course</button></div>
  <div class="gb-class-header gb-class-row"><button class="course-title">Second Course</button></div>
</div>'''

LOW_COURSE_HTML = '''<div class="pxp-course-content">
  <div class="item-container">
    <div class="item-text-main">Low quiz</div>
    <div class="item-text-special">Aug 14</div>
    <div class="item-text-special">72%</div>
  </div>
</div>'''

MISSING_COURSE_HTML = '''<div class="pxp-course-content">
  <div class="item-container missing">
    <div class="item-text-main">Missing worksheet</div>
    <div class="item-text-special">Aug 15</div>
    <div class="item-text-small">Missing</div>
  </div>
</div>'''

EMPTY_COURSE_HTML = '''<div class="pxp-course-content">
  <div class="no-data">No assignments</div>
</div>'''

BLANK_COURSE_HTML = '<div class="pxp-course-content"></div>'

MALFORMED_COURSE_HTML = '''<div class="pxp-course-content">
  <div class="item-container"><div class="item-text-main">Undated</div></div>
</div>'''


class FakeCourseButton:
    def __init__(self, page: "FakeCoursePage", index: int, generation: int) -> None:
        self.page = page
        self.index = index
        self.generation = generation

    def _require_fresh(self) -> None:
        if self.generation != self.page.generation:
            raise AssertionError("stale course locator reused after navigation")

    async def inner_text(self) -> str:
        self._require_fresh()
        return self.page.course_titles[self.index]

    async def click(self) -> None:
        self._require_fresh()
        if self.page.view != "overview":
            raise AssertionError("course opened outside overview")
        if self.page.course_open:
            raise AssertionError("courses opened concurrently")
        self.page.course_open = True
        self.page.actions.append(f"open-course:{self.index}")
        self.page.view = f"course:{self.index}"
        self.page.generation += 1


class FakeCourseRow:
    def __init__(self, page: "FakeCoursePage", index: int, generation: int) -> None:
        self.page = page
        self.index = index
        self.generation = generation

    def locator(self, selector: str) -> FakeCourseButton:
        assert selector == "button.course-title"
        return FakeCourseButton(self.page, self.index, self.generation)


class FakeCourseRows:
    def __init__(self, page: "FakeCoursePage") -> None:
        self.page = page
        self.generation = page.generation

    async def count(self) -> int:
        if self.page.view != "overview":
            return 0
        return len(self.page.course_titles)

    def nth(self, index: int) -> FakeCourseRow:
        return FakeCourseRow(self.page, index, self.generation)


class FakeAllClassesButton:
    def __init__(self, page: "FakeCoursePage") -> None:
        self.page = page

    async def click(self) -> None:
        if not self.page.view.startswith("course:"):
            raise AssertionError("All Classes used outside a course")
        self.page.actions.append("all-classes")
        self.page.view = "overview"
        self.page.course_open = False
        self.page.generation += 1


class FakeEmptyLocator:
    async def count(self) -> int:
        return 0


class FakeCoursePage:
    def __init__(self, course_html: list[str]) -> None:
        self.course_html = course_html
        self.course_titles = ["First Course", "Second Course"][: len(course_html)]
        self.actions: list[str] = []
        self.view = "overview"
        self.generation = 0
        self.course_open = False
        self.default_timeout = 15_000

    def locator(self, selector: str):
        if selector == "div.gb-class-header.gb-class-row:visible":
            self.actions.append("locate-courses")
            return FakeCourseRows(self)
        return FakeEmptyLocator()

    def get_by_role(self, role: str, *, name: str, exact: bool):
        assert (role, name, exact) == ("button", "All Classes", True)
        return FakeAllClassesButton(self)

    async def content(self) -> str:
        if self.view == "overview":
            self.actions.append("capture-overview")
            return OVERVIEW_HTML
        index = int(self.view.split(":", 1)[1])
        self.actions.append(f"capture-course:{index}")
        return self.course_html[index]

    async def wait_for_selector(self, _selector: str, *, timeout: int) -> None:
        assert timeout in (3_000, 90_000)
        if timeout == 3_000 and self.view.startswith("course:"):
            index = int(self.view.split(":", 1)[1])
            html = self.course_html[index]
            if "item-container" not in html and "no-data" not in html:
                raise PlaywrightTimeout("no assignments rendered")

    async def wait_for_timeout(self, timeout: int) -> None:
        assert timeout == 3_000
        self.actions.append("settle-overview")


def _collect(page: FakeCoursePage):
    scrub = importlib.import_module("scraper.portals.parentvue_course_scrub")
    return asyncio.run(
        scrub.collect_parentvue_course_agenda(
            page,
            reference=datetime(2026, 8, 16, 12, 0),
        )
    )


def test_collects_overview_and_courses_in_strict_sequence() -> None:
    """Would fail if course navigation is concurrent, stale, or skips a return."""
    page = FakeCoursePage([LOW_COURSE_HTML, MISSING_COURSE_HTML])

    records = _collect(page)

    assert [record["status"] for record in records] == [
        "due",
        "low_score",
        "missing",
    ]
    assert page.actions == [
        "settle-overview",
        "locate-courses",
        "capture-overview",
        "locate-courses",
        "locate-courses",
        "open-course:0",
        "capture-course:0",
        "all-classes",
        "settle-overview",
        "locate-courses",
        "open-course:1",
        "capture-course:1",
        "all-classes",
        "settle-overview",
        "locate-courses",
    ]
    assert page.course_open is False


@pytest.mark.parametrize("course_html", [EMPTY_COURSE_HTML, BLANK_COURSE_HTML])
def test_empty_course_preserves_other_complete_records(course_html: str) -> None:
    page = FakeCoursePage([course_html])

    records = _collect(page)

    assert [record["title"] for record in records] == ["Upcoming review"]
    assert page.actions.count("open-course:0") == 1


def test_one_malformed_course_fails_without_returning_partial_output() -> None:
    """Would fail if the collector publishes records before every course validates."""
    page = FakeCoursePage([LOW_COURSE_HTML, MALFORMED_COURSE_HTML])

    with pytest.raises(ParentVueAgendaError):
        _collect(page)

    assert page.actions[:8] == [
        "settle-overview",
        "locate-courses",
        "capture-overview",
        "locate-courses",
        "locate-courses",
        "open-course:0",
        "capture-course:0",
        "all-classes",
    ]
    assert "capture-course:1" in page.actions
