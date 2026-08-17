from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import re
import time
from typing import TypeAlias

from bs4 import BeautifulSoup, Tag
from playwright.async_api import Frame, Locator, Page

from scraper.agenda_contract import AgendaRecord


AssignmentKey: TypeAlias = tuple[str, str]


class InfiniteCampusAgendaError(RuntimeError):
    def __init__(self, code: str = "infinite_campus_agenda_failed") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ListedAssignment:
    ordinal: int
    key: AssignmentKey
    course: str
    title: str
    score_text: str
    missing: bool


@dataclass(frozen=True)
class AssignmentDetail:
    start_at: datetime | None
    end_at: datetime | None


_DATE_FORMAT = "%m/%d/%Y %I:%M %p"
_PERCENT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%")
_POINTS = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)(?![\d.])"
)
_EXCLUDED_SCORE_STATES = frozenset({
    "excused",
    "pass/fail",
    "exempt",
    "notgraded",
    "ungraded",
})


def _text(element: Tag | None) -> str:
    return " ".join(element.get_text(" ", strip=True).split()) if element else ""


def _key(title: str, course: str) -> AssignmentKey:
    return title.casefold(), course.casefold()


def _parse_infinite_campus_detail_date(element: Tag | None) -> datetime | None:
    if element is None:
        raise InfiniteCampusAgendaError()
    raw = _text(element)
    if not raw:
        return None
    failure: InfiniteCampusAgendaError | None = None
    try:
        return datetime.strptime(raw, _DATE_FORMAT)
    except ValueError:
        failure = InfiniteCampusAgendaError()
    if failure is not None:
        failure.__cause__ = None
        failure.__context__ = None
        raise failure
    return None


def parse_infinite_campus_list(
    html: str,
    *,
    missing_keys: frozenset[AssignmentKey],
) -> list[ListedAssignment]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(".selcat-assignment-row")
    empty_marker = soup.select_one(".assignment__empty")

    if rows:
        if empty_marker is not None and _text(empty_marker):
            raise InfiniteCampusAgendaError()
    elif empty_marker is not None:
        return []
    else:
        raise InfiniteCampusAgendaError()

    listed: list[ListedAssignment] = []
    seen_keys: set[AssignmentKey] = set()
    for ordinal, row in enumerate(rows):
        title_cell = row.select_one(".assignment__largeScreen--cell-assignmentName")
        course_cell = row.select_one(".assignment__largeScreen--cell-courseDueDate")
        score_cell = row.select_one(".assignment-score__scores")
        if title_cell is None:
            raise InfiniteCampusAgendaError()
        if course_cell is None:
            raise InfiniteCampusAgendaError()

        title = _text(title_cell)
        course = _text(course_cell)
        if not title or not course:
            raise InfiniteCampusAgendaError()
        key = _key(title, course)
        if key in seen_keys:
            raise InfiniteCampusAgendaError()
        seen_keys.add(key)
        listed.append(
            ListedAssignment(
                ordinal=ordinal,
                key=key,
                course=course,
                title=title,
                score_text=_text(score_cell),
                missing=(key in missing_keys),
            )
        )
    return listed


def parse_infinite_campus_detail(html: str) -> AssignmentDetail:
    soup = BeautifulSoup(html, "html.parser")
    start_at = _parse_infinite_campus_detail_date(
        soup.select_one(".selcat-schedule-startdate")
    )
    end_at = _parse_infinite_campus_detail_date(
        soup.select_one(".selcat-schedule-enddate")
    )
    return AssignmentDetail(start_at=start_at, end_at=end_at)


def _score_percentage(raw: str) -> float | None:
    matches = list(_PERCENT.finditer(raw))
    if len(matches) != 1:
        return None
    return float(matches[0].group(1))


def _score_points(raw: str) -> float | None:
    matches = list(_POINTS.finditer(raw))
    if len(matches) != 1:
        return None
    earned, possible = float(matches[0].group(1)), float(matches[0].group(2))
    if possible <= 0:
        return None
    return earned / possible * 100


def _excluded_score_state(raw: str) -> bool:
    fragments = sorted(
        [*(_PERCENT.finditer(raw)), *(_POINTS.finditer(raw))],
        key=lambda match: match.start(),
    )
    non_overlapping: list[re.Match[str]] = []
    end = -1
    for match in fragments:
        if match.start() >= end:
            non_overlapping.append(match)
            end = match.end()
    for match in reversed(non_overlapping):
        raw = raw[: match.start()] + raw[match.end() :]
    normalized = re.sub(r"[^a-z0-9/]+", "", raw.casefold())
    return normalized in _EXCLUDED_SCORE_STATES


def classify_infinite_campus_assignment(
    assignment: ListedAssignment,
    detail: AssignmentDetail,
    *,
    reference: datetime,
) -> AgendaRecord | None:
    percentage = _score_percentage(assignment.score_text)
    if percentage is None:
        percentage = _score_points(assignment.score_text)
    excluded = _excluded_score_state(assignment.score_text)

    if assignment.missing:
        status = "missing"
    elif not excluded and percentage is not None and percentage < 80:
        status = "low_score"
    elif percentage is not None or excluded:
        return None
    elif detail.end_at is not None and detail.end_at.date() >= reference.date():
        status = "due"
    else:
        return None

    if detail.end_at is None:
        raise InfiniteCampusAgendaError()
    return {
        "course": assignment.course,
        "title": assignment.title,
        "dueDate": detail.end_at.date().isoformat(),
        "dueTime": detail.end_at.strftime("%H:%M"),
        "status": status,
    }


_WORKSPACE_FRAME = "main-workspace"
_CANONICAL_ROWS = ".selcat-assignment-row:visible"
_TITLE_CELL = ".assignment__largeScreen--cell-assignmentName"
_COURSE_CELL = ".assignment__largeScreen--cell-courseDueDate"
_DETAIL_READY = ".selcat-schedule-startdate, .selcat-schedule-enddate"
_READINESS_TIMEOUT_MS = 30_000
_FILTER_POLL_INTERVAL_SECONDS = 0.01
_FILTER_QUIET_INTERVAL_SECONDS = 0.1
_FILTER_SETTLE_TIMEOUT_SECONDS = _READINESS_TIMEOUT_MS / 1000


def _workspace(page: Page) -> Frame:
    frame = page.frame(_WORKSPACE_FRAME)
    if frame is None:
        raise InfiniteCampusAgendaError()
    return frame


async def _open_current_term_assignments(page: Page) -> Frame:
    frame = _workspace(page)
    menu = page.locator("#menu-toggle-button")
    assignments = frame.get_by_role("link", name="Assignments", exact=True)

    if await assignments.count() == 0:
        if await menu.count() != 1:
            raise InfiniteCampusAgendaError()
        await menu.click()
        frame = _workspace(page)
        assignments = frame.get_by_role("link", name="Assignments", exact=True)

    if await assignments.count() != 1:
        raise InfiniteCampusAgendaError()
    await assignments.first.click()
    frame = _workspace(page)

    missing = frame.get_by_role("button", name="Missing", exact=True)
    current_term = frame.get_by_role("button", name="Current Term", exact=True)
    await missing.wait_for(state="visible", timeout=_READINESS_TIMEOUT_MS)
    await current_term.wait_for(state="visible", timeout=_READINESS_TIMEOUT_MS)
    if await missing.count() != 1 or await current_term.count() != 1:
        raise InfiniteCampusAgendaError()

    if await current_term.get_attribute("aria-pressed") != "true":
        await current_term.click()
    await _wait_for_filter_settle(frame, current_term, enabled=True)

    return frame


async def _set_missing(frame: Frame, enabled: bool) -> None:
    missing = frame.get_by_role("button", name="Missing", exact=True)
    if await missing.count() != 1:
        raise InfiniteCampusAgendaError()

    pressed = await missing.get_attribute("aria-pressed")
    if pressed is None:
        raise InfiniteCampusAgendaError()
    target = "true" if enabled else "false"
    if pressed != target:
        await missing.click()
    await _wait_for_filter_settle(frame, missing, enabled=enabled)


async def _visible_list_fingerprint(frame: Frame) -> tuple[str, ...]:
    rows = frame.locator(_CANONICAL_ROWS)
    if await rows.count() == 0:
        empty = frame.locator(".assignment__empty:visible")
        if await empty.count() != 0:
            return ("<empty>",)
        rows = frame.locator(_CANONICAL_ROWS)
        if await rows.count() == 0:
            raise InfiniteCampusAgendaError()
    values = await rows.evaluate_all("rows => rows.map(row => row.textContent)")
    return tuple(str(value) for value in values)


async def _wait_for_filter_settle(
    frame: Frame,
    control: Locator,
    *,
    enabled: bool,
) -> None:
    target = "true" if enabled else "false"
    deadline = time.monotonic() + _FILTER_SETTLE_TIMEOUT_SECONDS
    previous: tuple[str, ...] | None = None
    last_changed: float | None = None
    while time.monotonic() < deadline:
        pressed = await control.get_attribute("aria-pressed")
        if pressed != target:
            previous = None
            last_changed = None
            await asyncio.sleep(_FILTER_POLL_INTERVAL_SECONDS)
            continue
        fingerprint = await _visible_list_fingerprint(frame)
        now = time.monotonic()
        if fingerprint == previous:
            if last_changed is not None and now - last_changed >= _FILTER_QUIET_INTERVAL_SECONDS:
                return
        else:
            previous = fingerprint
            last_changed = now
        await asyncio.sleep(_FILTER_POLL_INTERVAL_SECONDS)
    raise InfiniteCampusAgendaError()


async def _wait_for_detail_exit(page: Page) -> Frame:
    frame = _workspace(page)
    await frame.locator(_DETAIL_READY).wait_for(
        state="hidden", timeout=_READINESS_TIMEOUT_MS
    )
    return frame


async def _visible_list_html(frame: Frame) -> str:
    rows = frame.locator(_CANONICAL_ROWS)
    if await rows.count() == 0:
        empty = frame.locator(".assignment__empty:visible")
        if await empty.count() == 0:
            raise InfiniteCampusAgendaError()
        return '<div class="assignment__empty"></div>'

    fragments = await rows.evaluate_all("rows => rows.map(row => row.outerHTML)")
    return "<div>" + "".join(fragments) + "</div>"


async def _collect_infinite_campus_agenda(
    page: Page,
    *,
    reference: datetime | None = None,
) -> list[AgendaRecord]:
    effective_reference = reference or datetime.now()
    frame = await _open_current_term_assignments(page)
    await _set_missing(frame, True)

    missing_rows = parse_infinite_campus_list(
        await _visible_list_html(frame), missing_keys=frozenset()
    )
    missing_keys = frozenset(row.key for row in missing_rows)

    await _set_missing(frame, False)

    captured = parse_infinite_campus_list(
        await _visible_list_html(frame), missing_keys=missing_keys
    )
    expected_keys = [row.key for row in captured]
    records: list[AgendaRecord] = []

    for ordinal, assignment in enumerate(captured):
        if ordinal > 0:
            current = parse_infinite_campus_list(
                await _visible_list_html(frame), missing_keys=missing_keys
            )
            if [row.key for row in current] != expected_keys:
                raise InfiniteCampusAgendaError()
        elif [row.key for row in captured] != expected_keys:
            raise InfiniteCampusAgendaError()

        row = frame.locator(_CANONICAL_ROWS).nth(assignment.ordinal)
        await row.locator(f"{_TITLE_CELL} a[href]").first.click()

        frame = _workspace(page)
        await frame.wait_for_selector(_DETAIL_READY, timeout=_READINESS_TIMEOUT_MS)

        detail = parse_infinite_campus_detail(await frame.content())
        record = classify_infinite_campus_assignment(
            assignment, detail, reference=effective_reference
        )
        if record is not None:
            records.append(record)

        back = frame.get_by_role("button", name="Back", exact=True)
        if await back.count() != 1:
            raise InfiniteCampusAgendaError()
        await back.click()

        await _wait_for_detail_exit(page)
        frame = await _open_current_term_assignments(page)
        current = parse_infinite_campus_list(
            await _visible_list_html(frame), missing_keys=missing_keys
        )
        if [row.key for row in current] != expected_keys:
            raise InfiniteCampusAgendaError()

    return records


async def collect_infinite_campus_agenda(
    page: Page,
    *,
    reference: datetime | None = None,
) -> list[AgendaRecord]:
    failure: InfiniteCampusAgendaError | None = None
    try:
        return await _collect_infinite_campus_agenda(page, reference=reference)
    except asyncio.CancelledError:
        raise
    except Exception:
        failure = InfiniteCampusAgendaError()
    if failure is not None:
        failure.__cause__ = None
        failure.__context__ = None
        raise failure
    return []
