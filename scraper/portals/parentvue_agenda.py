from __future__ import annotations

from datetime import date, datetime
import re

from bs4 import BeautifulSoup, Tag

from scraper.agenda_contract import AgendaRecord


class ParentVueAgendaError(RuntimeError):
    def __init__(self, code: str = "parentvue_agenda_parse_failed") -> None:
        self.code = code
        super().__init__(code)


_PERCENT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")
_POINTS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
_DUE_PREFIX = re.compile(r"^\s*due\s+date\s*:\s*", re.IGNORECASE)
_ACADEMIC_YEAR_START_MONTH = 7


def _text(element: Tag | None) -> str | None:
    if element is None:
        return None
    value = element.get_text(" ", strip=True)
    return value or None


def _date_and_time(value: str | None) -> tuple[str, str | None] | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed_date = date.fromisoformat(value)
        return parsed_date.isoformat(), None
    except ValueError:
        pass
    try:
        parsed_datetime = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed_datetime.date().isoformat(), parsed_datetime.strftime("%H:%M")
    except ValueError:
        pass
    for pattern in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(value, pattern)
            return parsed.date().isoformat(), (
                parsed.strftime("%H:%M") if "%I" in pattern else None
            )
        except ValueError:
            continue
    return None


def _course(row: Tag) -> str | None:
    value = row.get("data-course-title")
    if isinstance(value, str) and value.strip():
        return value.strip()
    ancestor = row.parent
    while isinstance(ancestor, Tag):
        classes = ancestor.get("class", [])
        is_class_container = isinstance(classes, list) and any(
            str(item) in {"gb-class-section", "gb-class-row"} for item in classes
        )
        if not is_class_container:
            ancestor = ancestor.parent
            continue
        value = ancestor.get("data-course-title")
        if isinstance(value, str) and value.strip():
            return value.strip()
        title = _text(ancestor.select_one(".course-title"))
        if title:
            return title
        ancestor = ancestor.parent
    return _text(row.select_one('[data-label="Course"], [data-label="Course Title"]'))


def _is_upcoming(row: Tag) -> bool:
    section = row.find_parent("section")
    while isinstance(section, Tag):
        heading = _text(section.select_one("h1, h2, h3, h4, h5, h6"))
        if heading and heading.casefold() == "upcoming assignments":
            return True
        section = section.find_parent("section")
    return False


def _is_missing(row: Tag) -> bool:
    classes = row.get("class", [])
    if isinstance(classes, list) and any(str(item).casefold() == "missing" for item in classes):
        return True
    status = row.get("data-status")
    if isinstance(status, str) and status.strip().casefold() == "missing":
        return True
    return any(
        not _is_hidden(marker) and (_text(marker) or "").casefold() == "missing"
        for marker in row.select(".status, [data-status]")
    )


def _style_declarations(element: Tag) -> dict[str, str]:
    style = element.get("style")
    if not isinstance(style, str):
        return {}
    declarations: dict[str, str] = {}
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        normalized_value = value.strip().casefold()
        normalized_value = normalized_value.removesuffix("!important").rstrip()
        declarations[name.strip().casefold()] = normalized_value
    return declarations


def _is_hidden(element: Tag) -> bool:
    current: Tag | None = element
    visibility: str | None = None
    while isinstance(current, Tag):
        aria_hidden = current.get("aria-hidden")
        if current.has_attr("hidden") or (
            isinstance(aria_hidden, str)
            and aria_hidden.strip().casefold() == "true"
        ):
            return True
        declarations = _style_declarations(current)
        if declarations.get("display") == "none":
            return True
        if visibility is None and "visibility" in declarations:
            visibility = declarations["visibility"]
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return visibility == "hidden"


def _assignment_rows(soup: BeautifulSoup) -> list[Tag]:
    candidates = list(soup.select(".assignment-row, .gb-assignment-row"))
    candidates.extend(
        row for row in soup.select("tr")
        if row.select_one('.assignment-title, .assignment-name, [data-label="Assignment"]')
    )
    rows: list[Tag] = []
    seen: set[int] = set()
    for row in candidates:
        if id(row) not in seen:
            rows.append(row)
            seen.add(id(row))
    return rows


def parse_parentvue_agenda(html: str) -> list[AgendaRecord]:
    soup = BeautifulSoup(html, "html.parser")
    rows = _assignment_rows(soup)
    visible_no_data = any(
        not _is_hidden(marker) for marker in soup.select("#gb-assignments .no-data")
    )
    recognizable = bool(rows) or bool(soup.select_one("#gb-assignments")) or bool(
        soup.select_one(".gb-class-section, .gb-class-row, a[href*='Gradebook'], a[href*='GradeBook']")
    ) or "upcoming assignments" in soup.get_text(" ", strip=True).casefold()
    if not recognizable:
        raise ParentVueAgendaError()
    if visible_no_data:
        if rows:
            raise ParentVueAgendaError()
        return []
    if not rows:
        raise ParentVueAgendaError()

    records: list[AgendaRecord] = []
    for row in rows:
        if _is_hidden(row):
            continue
        title = _text(row.select_one('.assignment-title, .assignment-name, [data-label="Assignment"]'))
        due_element = row.select_one('time[datetime], .due-date, [data-label="Due Date"]')
        raw_due = due_element.get("datetime") if isinstance(due_element, Tag) else None
        due = _date_and_time(raw_due if isinstance(raw_due, str) else _text(due_element))
        course = _course(row)
        status = "missing" if _is_missing(row) else "due" if _is_upcoming(row) else None
        if not course or not title or due is None or status is None:
            continue
        record: AgendaRecord = {
            "course": course,
            "title": title,
            "dueDate": due[0],
            "dueTime": due[1],
            "status": status,
        }
        assignment_id = row.get("data-assignment-id")
        if isinstance(assignment_id, str) and assignment_id.strip():
            record["sourceId"] = f"parentvue:{assignment_id.strip()}"
        records.append(record)
    if rows and not records:
        raise ParentVueAgendaError()
    return records


def _academic_year_date(
    value: str | None,
    *,
    reference: datetime,
) -> tuple[str, str | None] | None:
    if not value:
        return None
    normalized = _DUE_PREFIX.sub("", value).strip()
    parsed = _date_and_time(normalized)
    if parsed is not None:
        return parsed
    for pattern in ("%b %d", "%B %d"):
        try:
            month_day = datetime.strptime(normalized, pattern)
        except ValueError:
            continue
        academic_start = (
            reference.year
            if reference.month >= _ACADEMIC_YEAR_START_MONTH
            else reference.year - 1
        )
        year = (
            academic_start
            if month_day.month >= _ACADEMIC_YEAR_START_MONTH
            else academic_start + 1
        )
        return date(year, month_day.month, month_day.day).isoformat(), None
    return None


def _live_upcoming_record(
    row: Tag,
    *,
    reference: datetime,
) -> AgendaRecord:
    cell = row.select_one("td")
    if cell is None:
        raise ParentVueAgendaError()
    visible_children = [
        child
        for child in cell.find_all(recursive=False)
        if isinstance(child, Tag) and not _is_hidden(child)
    ]
    title = _text(row.select_one("a"))
    course = _text(visible_children[1]) if len(visible_children) > 1 else None
    due_text = _text(visible_children[2]) if len(visible_children) > 2 else None
    due = _academic_year_date(due_text, reference=reference)
    if not title or not course or due is None:
        raise ParentVueAgendaError()
    record: AgendaRecord = {
        "course": course,
        "title": title,
        "dueDate": due[0],
        "dueTime": due[1],
        "status": "due",
    }
    source_id = row.get("data-guid")
    if isinstance(source_id, str) and source_id.strip():
        record["sourceId"] = f"parentvue:{source_id.strip()}"
    return record


def parse_parentvue_overview(
    html: str,
    *,
    reference: datetime,
) -> list[AgendaRecord]:
    soup = BeautifulSoup(html, "html.parser")
    live_rows = [
        row
        for row in soup.select("#gb-assignments tr.gb-upcoming-assignment")
        if not _is_hidden(row)
    ]
    visible_no_data = any(
        not _is_hidden(marker) for marker in soup.select("#gb-assignments .no-data")
    )
    if live_rows:
        if visible_no_data:
            raise ParentVueAgendaError()
        return [
            _live_upcoming_record(row, reference=reference) for row in live_rows
        ]
    try:
        return parse_parentvue_agenda(html)
    except ParentVueAgendaError:
        visible_courses = any(
            not _is_hidden(row)
            for row in soup.select("div.gb-class-header.gb-class-row")
        )
        if soup.select_one("#gb-assignments") is not None and visible_courses:
            return []
        raise


def _explicitly_missing(item: Tag) -> bool:
    classes = item.get("class", [])
    if isinstance(classes, list) and any(
        str(value).strip().casefold() == "missing" for value in classes
    ):
        return True
    status = item.get("data-status")
    if isinstance(status, str) and status.strip().casefold() == "missing":
        return True
    return any(
        not _is_hidden(marker) and (_text(marker) or "").strip().casefold() == "missing"
        for marker in item.select(
            ".item-text-small, .item-text-special, .status, [data-status]"
        )
    )


def _assignment_percentage(item: Tag) -> float | None:
    texts = [
        text
        for element in item.select(".item-text-special, .item-text-small")
        if not _is_hidden(element)
        for text in [(_text(element) or "").strip()]
        if text
    ]
    for text in texts:
        match = _PERCENT.search(text)
        if match is not None:
            return float(match.group(1))
    for text in texts:
        if text.count("/") != 1:
            continue
        match = _POINTS.search(text)
        if match is None:
            continue
        earned, possible = (float(match.group(1)), float(match.group(2)))
        if possible > 0:
            return earned / possible * 100
    return None


def _course_item_date(
    item: Tag,
    *,
    reference: datetime,
) -> tuple[str, str | None] | None:
    for element in item.select(".item-text-special, .item-text-small"):
        if _is_hidden(element):
            continue
        parsed = _academic_year_date(_text(element), reference=reference)
        if parsed is not None:
            return parsed
    return None


def parse_parentvue_course_assignments(
    html: str,
    *,
    course: str,
    reference: datetime,
) -> list[AgendaRecord]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".pxp-course-content")
    if root is None:
        raise ParentVueAgendaError()
    candidates = list(root.select(".item-container"))
    visible_items = [item for item in candidates if not _is_hidden(item)]
    visible_no_data = any(
        not _is_hidden(marker) for marker in root.select(".no-data")
    )
    if visible_no_data:
        if visible_items:
            raise ParentVueAgendaError()
        return []
    if not candidates or not visible_items:
        raise ParentVueAgendaError()

    records: list[AgendaRecord] = []
    for item in visible_items:
        title = _text(item.select_one(".item-text-main"))
        due = _course_item_date(item, reference=reference)
        if not title or due is None:
            raise ParentVueAgendaError()
        missing = _explicitly_missing(item)
        score_text = " ".join(
            (_text(element) or "")
            for element in item.select(".item-text-special, .item-text-small")
            if not _is_hidden(element)
        ).casefold()
        excluded = any(
            marker in score_text
            for marker in ("excused", "not graded", "ungraded", "pass/fail")
        )
        percentage = _assignment_percentage(item)
        status = (
            "missing"
            if missing
            else "low_score"
            if not excluded and percentage is not None and percentage < 80
            else None
        )
        if status is None:
            continue
        record: AgendaRecord = {
            "course": course.strip(),
            "title": title,
            "dueDate": due[0],
            "dueTime": due[1],
            "status": status,
        }
        source = item.get("data-guid")
        if not isinstance(source, str):
            source_element = item.select_one("[data-guid]")
            source = source_element.get("data-guid") if source_element else None
        if isinstance(source, str) and source.strip():
            record["sourceId"] = f"parentvue:{source.strip()}"
        records.append(record)
    return records
