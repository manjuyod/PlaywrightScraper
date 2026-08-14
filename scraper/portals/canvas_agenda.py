from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from playwright.async_api import Page

from scraper.agenda_contract import AgendaRecord


_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


class CanvasAgendaError(RuntimeError):
    def __init__(self, code: str = "canvas_agenda_request_failed") -> None:
        self.code = code
        super().__init__(code)


def _next_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = _NEXT_LINK.search(link_header)
    return match.group(1) if match else None


def _local_due(raw_due: object, timezone: ZoneInfo) -> tuple[str, str] | None:
    if not isinstance(raw_due, str) or not raw_due.strip():
        return None
    try:
        normalized = raw_due.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
        local = parsed.astimezone(timezone)
    except (TypeError, ValueError):
        return None
    return local.date().isoformat(), local.strftime("%H:%M")


def _same_origin(url: str, origin: str) -> bool:
    candidate = urlparse(url)
    expected = urlparse(origin)
    return candidate.scheme == expected.scheme and candidate.netloc == expected.netloc


async def _fetch_pages(page: Page, origin: str, url: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    next_url: str | None = url
    while next_url:
        current_url = urljoin(origin, next_url)
        if not _same_origin(current_url, origin):
            raise CanvasAgendaError()
        response = None
        try:
            response = await page.context.request.get(current_url)
            if not response.ok:
                raise CanvasAgendaError()
            payload = await response.json()
            if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
                raise CanvasAgendaError()
            results.extend(payload)
            next_url = _next_url(response.headers.get("link"))
        except CanvasAgendaError:
            raise
        except Exception as error:
            raise CanvasAgendaError() from error
        finally:
            if response is not None:
                try:
                    await response.dispose()
                except Exception:
                    pass
    return results


async def _canvas_timezone(page: Page) -> ZoneInfo:
    try:
        name = await page.evaluate(
            "window.ENV && window.ENV.TIMEZONE || Intl.DateTimeFormat().resolvedOptions().timeZone"
        )
        return ZoneInfo(name) if isinstance(name, str) else ZoneInfo("UTC")
    except Exception:
        return ZoneInfo("UTC")


def _course_name(row: dict[str, Any], courses: dict[str, str]) -> str | None:
    course_id = row.get("course_id")
    if course_id is not None:
        known = courses.get(str(course_id))
        if known:
            return known
    context_name = row.get("context_name")
    return context_name.strip() if isinstance(context_name, str) and context_name.strip() else None


def _record(
    *, course: str | None, title: object, raw_due: object, status: str,
    assignment_id: object, timezone: ZoneInfo,
) -> AgendaRecord | None:
    due = _local_due(raw_due, timezone)
    if not course or not isinstance(title, str) or not title.strip() or due is None:
        return None
    record: AgendaRecord = {
        "course": course,
        "title": title.strip(),
        "dueDate": due[0],
        "dueTime": due[1],
        "status": status,  # type: ignore[typeddict-item]
    }
    if assignment_id is not None:
        record["sourceId"] = f"canvas:assignment:{assignment_id}"
    return record


async def collect_canvas_agenda(
    page: Page, origin: str, *, today: date | None = None
) -> list[AgendaRecord]:
    timezone = await _canvas_timezone(page)
    local_today = today or datetime.now(timezone).date()
    end_day = local_today + timedelta(days=365)
    courses_url = f"{origin}/api/v1/courses?{urlencode({'per_page': 100, 'enrollment_state': 'active'})}"
    missing_url = f"{origin}/api/v1/users/self/missing_submissions?{urlencode({'per_page': 100})}"
    start = datetime.combine(local_today, time.min, timezone).isoformat()
    end = datetime.combine(end_day, time.min, timezone).isoformat()
    planner_url = f"{origin}/api/v1/planner/items?{urlencode({'per_page': 100, 'start_date': start, 'end_date': end})}"

    course_rows = await _fetch_pages(page, origin, courses_url)
    courses = {
        str(row["id"]): row["name"].strip()
        for row in course_rows
        if row.get("id") is not None and isinstance(row.get("name"), str) and row["name"].strip()
    }
    missing_rows = await _fetch_pages(page, origin, missing_url)
    planner_rows = await _fetch_pages(page, origin, planner_url)

    records: list[AgendaRecord] = []
    for row in missing_rows:
        record = _record(
            course=_course_name(row, courses), title=row.get("name"), raw_due=row.get("due_at"),
            status="missing", assignment_id=row.get("id"), timezone=timezone,
        )
        if record:
            records.append(record)

    for row in planner_rows:
        if row.get("plannable_type") != "assignment":
            continue
        plannable = row.get("plannable")
        if not isinstance(plannable, dict):
            continue
        record = _record(
            course=_course_name(row, courses), title=plannable.get("title"), raw_due=plannable.get("due_at"),
            status="due", assignment_id=plannable.get("id"), timezone=timezone,
        )
        if record is None:
            continue
        due_day = date.fromisoformat(record["dueDate"])
        if due_day < local_today or due_day > end_day:
            continue
        records.append(record)
    return records
