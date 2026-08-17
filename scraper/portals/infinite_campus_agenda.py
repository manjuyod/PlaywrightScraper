from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import TypeAlias

from bs4 import BeautifulSoup, Tag

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
_POINTS = re.compile(r"(?<![\d.])(\d+)\s*/\s*(\d+)(?![\d.])")
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
    try:
        return datetime.strptime(raw, _DATE_FORMAT)
    except ValueError as error:
        raise InfiniteCampusAgendaError() from error


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
    earned, possible = int(matches[0].group(1)), int(matches[0].group(2))
    if possible <= 0:
        return None
    return earned / possible * 100


def _excluded_score_state(raw: str) -> bool:
    normalized = re.sub(r"[\s-]+", "", raw.casefold())
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
