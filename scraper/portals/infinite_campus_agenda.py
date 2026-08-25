from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
import re

from bs4 import BeautifulSoup, Tag
from playwright.async_api import Frame, Page

from scraper.agenda_contract import AgendaRecord


class InfiniteCampusAgendaError(RuntimeError):
    def __init__(self, code: str = "infinite_campus_agenda_failed") -> None:
        self.code: str = code
        super().__init__(code)


_WORKSPACE_FRAME = "main-workspace"
_COURSE_CARDS = "div.collapsible-card.grades__card:visible"
_COURSE_LINK = "h4 a"
_COURSE_GRADES_ROOT = "tl-grading-task-list"
_COURSE_GRADES_ENTRY = (
    'tl-grading-task-list:visible, a:visible:text-is("Grades"), '
    'button:visible:text-is("Grades")'
)
_CATEGORY_TOGGLES = "button.divider__header[aria-controls]"
_ASSIGNMENT_ROWS = ".selcat-assignment-row"
_ASSIGNMENT_TITLE = ".assignment__largeScreen--cell-assignmentName h6 a"
_ASSIGNMENT_DUE = ".assignment__largeScreen--cell-courseDueDate"
_ASSIGNMENT_SCORE = ".assignment-score__scores--largeScreen"
_ASSIGNMENT_FLAGS = "tl-curriculum-flags .label"
_READINESS_TIMEOUT_MS = 30_000

_DUE_DATE = re.compile(r"\bdue\s*:\s*(\d{1,2}/\d{1,2}/\d{4})\b", re.IGNORECASE)
_PERCENT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%")
_POINTS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)(?![\d.])")
_EXCLUDED_SCORE_STATES = frozenset(
    {"excused", "pass/fail", "exempt", "notgraded", "ungraded"}
)


def _text(element: Tag | None) -> str:
    return " ".join(element.get_text(" ", strip=True).split()) if element else ""


def _workspace(page: Page) -> Frame:
    frame = page.frame(_WORKSPACE_FRAME)
    if frame is None:
        raise InfiniteCampusAgendaError()
    return frame


def _score_percentage(raw: str) -> float | None:
    percentages = list(_PERCENT.finditer(raw))
    if len(percentages) == 1:
        return float(percentages[0].group(1))

    points = list(_POINTS.finditer(raw))
    if len(points) != 1:
        return None
    earned, possible = float(points[0].group(1)), float(points[0].group(2))
    return earned / possible * 100 if possible > 0 else None


def _excluded_score_state(raw: str) -> bool:
    without_scores = _PERCENT.sub("", _POINTS.sub("", raw))
    normalized = re.sub(r"[^a-z0-9/]+", "", without_scores.casefold())
    return normalized in _EXCLUDED_SCORE_STATES


def _assignment_due_date(row: Tag) -> date:
    raw = _text(row.select_one(_ASSIGNMENT_DUE))
    match = _DUE_DATE.search(raw)
    if match is None:
        raise InfiniteCampusAgendaError()
    try:
        return datetime.strptime(match.group(1), "%m/%d/%Y").date()
    except ValueError:
        raise InfiniteCampusAgendaError() from None


def _assignment_flags(row: Tag) -> frozenset[str]:
    return frozenset(
        _text(flag).casefold() for flag in row.select(_ASSIGNMENT_FLAGS) if _text(flag)
    )


def parse_infinite_campus_course_grades(
    html: str,
    *,
    course: str,
    reference: datetime | date | None = None,
) -> list[AgendaRecord]:
    """Parse one expanded IC class Grades view without opening assignments."""
    normalized_course = " ".join(course.split())
    if not normalized_course:
        raise InfiniteCampusAgendaError()

    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(_COURSE_GRADES_ROOT)
    if root is None:
        raise InfiniteCampusAgendaError()
    rows = soup.select(_ASSIGNMENT_ROWS)
    reference_date = (
        reference.date()
        if isinstance(reference, datetime)
        else reference
        if isinstance(reference, date)
        else date.today()
    )

    records: list[AgendaRecord] = []
    for row in rows:
        title = _text(row.select_one(_ASSIGNMENT_TITLE))
        if not title:
            raise InfiniteCampusAgendaError()
        score_text = _text(row.select_one(_ASSIGNMENT_SCORE))
        flags = _assignment_flags(row)
        percentage = _score_percentage(score_text)
        excluded = _excluded_score_state(score_text)

        if "missing" in flags:
            status = "missing"
        elif not excluded and percentage is not None and percentage < 80:
            status = "low_score"
        elif percentage is not None or excluded:
            continue
        elif "turned in" in flags:
            continue
        else:
            status = "due"

        due_date = _assignment_due_date(row)
        if status == "due" and due_date < reference_date:
            continue

        records.append(
            {
                "course": normalized_course,
                "title": title,
                "dueDate": due_date.isoformat(),
                "dueTime": None,
                "status": status,
            }
        )
    return records


async def _visible_course_titles(frame: Frame) -> list[str]:
    cards = frame.locator(_COURSE_CARDS)
    await cards.first.wait_for(state="visible", timeout=_READINESS_TIMEOUT_MS)
    titles: list[str] = []
    for index in range(await cards.count()):
        title = " ".join(
            (await cards.nth(index).locator(_COURSE_LINK).first.inner_text()).split()
        )
        if not title:
            raise InfiniteCampusAgendaError()
        titles.append(title)
    if not titles:
        raise InfiniteCampusAgendaError()
    return titles


async def _open_course(frame: Frame, index: int, title: str) -> None:
    cards = frame.locator(_COURSE_CARDS)
    if index >= await cards.count():
        raise InfiniteCampusAgendaError()
    link = cards.nth(index).locator(_COURSE_LINK).first
    candidate = " ".join((await link.inner_text()).split())
    if candidate != title:
        raise InfiniteCampusAgendaError()
    await link.click()


async def _wait_for_course_page(page: Page) -> Frame:
    workspace = page.frame_locator('iframe[name="main-workspace"]')
    await workspace.locator(_COURSE_CARDS).first.wait_for(
        state="hidden",
        timeout=_READINESS_TIMEOUT_MS,
    )
    frame = page.frame(_WORKSPACE_FRAME)
    if frame is None:
        raise InfiniteCampusAgendaError()
    return frame


async def _open_course_grades(frame: Frame) -> Frame:
    root = frame.locator(f"{_COURSE_GRADES_ROOT}:visible")
    await frame.locator(_COURSE_GRADES_ENTRY).first.wait_for(
        state="visible",
        timeout=_READINESS_TIMEOUT_MS,
    )
    if await root.count() == 0:
        grades_links = frame.get_by_role("link", name="Grades", exact=True)
        grades_buttons = frame.get_by_role("button", name="Grades", exact=True)
        visible_links = [
            grades_links.nth(index)
            for index in range(await grades_links.count())
            if await grades_links.nth(index).is_visible()
        ]
        visible_buttons = [
            grades_buttons.nth(index)
            for index in range(await grades_buttons.count())
            if await grades_buttons.nth(index).is_visible()
        ]
        controls = visible_links + visible_buttons
        if len(controls) == 1:
            await controls[0].click()
        else:
            raise InfiniteCampusAgendaError()
    await root.first.wait_for(state="visible", timeout=_READINESS_TIMEOUT_MS)
    return frame


async def _expand_assignment_categories(frame: Frame) -> None:
    toggles = frame.locator(_CATEGORY_TOGGLES)
    for index in range(await toggles.count()):
        toggle = toggles.nth(index)
        if await toggle.get_attribute("aria-expanded") != "true":
            target_id = await toggle.get_attribute("aria-controls")
            if not target_id:
                raise InfiniteCampusAgendaError()
            await toggle.click()
            await frame.locator(f'[id="{target_id}"]').wait_for(
                state="visible", timeout=_READINESS_TIMEOUT_MS
            )


async def collect_infinite_campus_agenda(
    page: Page,
    *,
    return_to_grades: Callable[[], Awaitable[None]],
    reference: datetime | date | None = None,
) -> list[AgendaRecord]:
    """Collect all IC agenda rows with one bulk parse per current class."""
    frame = _workspace(page)
    courses = await _visible_course_titles(frame)
    records: list[AgendaRecord] = []

    for index, course in enumerate(courses):
        frame = _workspace(page)
        await _open_course(frame, index, course)
        frame = await _open_course_grades(await _wait_for_course_page(page))
        await _expand_assignment_categories(frame)
        records.extend(
            parse_infinite_campus_course_grades(
                await frame.content(),
                course=course,
                reference=reference,
            )
        )
        if index + 1 < len(courses):
            await return_to_grades()
            frame = _workspace(page)
            if await _visible_course_titles(frame) != courses:
                raise InfiniteCampusAgendaError()

    return records
