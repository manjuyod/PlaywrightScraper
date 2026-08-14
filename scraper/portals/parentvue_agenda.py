from __future__ import annotations

from datetime import date, datetime

from bs4 import BeautifulSoup, Tag

from scraper.agenda_contract import AgendaRecord


class ParentVueAgendaError(RuntimeError):
    def __init__(self, code: str = "parentvue_agenda_parse_failed") -> None:
        self.code = code
        super().__init__(code)


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
    section = row.find_parent(class_=["gb-class-section", "gb-class-row"])
    if isinstance(section, Tag):
        value = section.get("data-course-title")
        if isinstance(value, str) and value.strip():
            return value.strip()
        title = _text(section.select_one(".course-title"))
        if title:
            return title
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
    recognizable = bool(rows) or bool(
        soup.select_one(".gb-class-section, .gb-class-row, a[href*='Gradebook'], a[href*='GradeBook']")
    ) or "upcoming assignments" in soup.get_text(" ", strip=True).casefold()
    if not recognizable:
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
    return records
