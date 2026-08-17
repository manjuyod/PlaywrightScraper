from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from playwright.async_api import Page

from scraper.agenda_contract import AgendaRecord

from .parentvue_agenda import (
    ParentVueAgendaError,
    parse_parentvue_course_assignments,
    parse_parentvue_overview,
)


_COURSE_ROWS = "div.gb-class-header.gb-class-row:visible"
_COURSE_READY = (
    ".pxp-course-content .item-container:visible, "
    ".pxp-course-content .no-data:visible"
)
_OVERVIEW_READY = (
    "#gb-assignments tr.gb-upcoming-assignment:visible, "
    "div.gb-class-header.gb-class-row:visible, "
    "#gb-assignments .no-data:visible"
)
_READINESS_TIMEOUT_MS = 90_000
_OVERVIEW_SETTLE_MS = 3_000


@dataclass(frozen=True)
class ParentVueCourse:
    index: int
    title: str


async def _visible_current_courses(page: Page) -> list[ParentVueCourse]:
    rows = page.locator(_COURSE_ROWS)
    courses: list[ParentVueCourse] = []
    for index in range(await rows.count()):
        title = " ".join(
            (await rows.nth(index).locator("button.course-title").inner_text()).split()
        )
        if not title:
            raise ParentVueAgendaError()
        courses.append(ParentVueCourse(index=index, title=title))
    return courses


async def _wait_for_course_detail(page: Page) -> None:
    await page.wait_for_selector(_COURSE_READY, timeout=_READINESS_TIMEOUT_MS)


async def _wait_for_overview(page: Page, expected_courses: int | None):
    await page.wait_for_selector(_OVERVIEW_READY, timeout=_READINESS_TIMEOUT_MS)
    await page.wait_for_timeout(_OVERVIEW_SETTLE_MS)
    rows = page.locator(_COURSE_ROWS)
    if expected_courses is not None and await rows.count() != expected_courses:
        raise ParentVueAgendaError()
    return rows


async def collect_parentvue_course_agenda(
    page: Page,
    *,
    reference: datetime | None = None,
) -> list[AgendaRecord]:
    effective_reference = reference or datetime.now()
    await _wait_for_overview(page, None)
    records = parse_parentvue_overview(
        await page.content(),
        reference=effective_reference,
    )
    courses = await _visible_current_courses(page)
    current_rows = page.locator(_COURSE_ROWS)
    for course in courses:
        row = current_rows.nth(course.index)
        button = row.locator("button.course-title")
        current_title = " ".join((await button.inner_text()).split())
        if current_title != course.title:
            raise ParentVueAgendaError()
        await button.click()
        await _wait_for_course_detail(page)
        records.extend(
            parse_parentvue_course_assignments(
                await page.content(),
                course=course.title,
                reference=effective_reference,
            )
        )
        await page.get_by_role("button", name="All Classes", exact=True).click()
        current_rows = await _wait_for_overview(page, len(courses))
    return records
