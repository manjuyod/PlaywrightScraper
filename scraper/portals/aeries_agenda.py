from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import cast
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from playwright.async_api import Page

from scraper.agenda_contract import AgendaRecord, AgendaStatus
from scraper.assignment_metadata import normalize_assignment_category


class AeriesAgendaError(RuntimeError):
    def __init__(self, code: str = "aeries_agenda_failed") -> None:
        self.code: str = code
        super().__init__(code)


_GRADEBOOK_SELECTOR = "#ctl00_MainContent_subGBS_dlGN"
_ASSIGNMENT_ROOT = "[id$='assignmentsView']"
_ASSIGNMENT_CARD = ".Card"
_TITLE = ".TextHeading"
_COMPLETE_SCORE = "[id$='completeData']"
_CATEGORY = ".TextSubSectionCategory"
_DATE = re.compile(r"\bDue Date:\s*(\d{1,2}/\d{1,2}/\d{4})\b", re.IGNORECASE)
_TIME = re.compile(r"\bDue Time:\s*(\d{1,2}:\d{2}\s*[AP]M)\b", re.IGNORECASE)
_GRADING_COMPLETE = re.compile(
    r"\bGrading Complete:\s*(True|False)\b", re.IGNORECASE
)
_PERCENT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%")
_POINTS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
_ASSIGNMENT_NUMBER = re.compile(r"^\s*\d+\s*-\s*")
_GRADEBOOK_NUMBER = re.compile(r"^\s*\d+\s*-\s*")
_TERM_SUFFIX = re.compile(
    r"\s*-\s*(?:trimester|semester|quarter|term)\b.*$", re.IGNORECASE
)
_MAX_GRADEBOOKS = 64

_FETCH_GRADEBOOKS = """
async () => {
    const select = document.querySelector('#ctl00_MainContent_subGBS_dlGN');
    if (!(select instanceof HTMLSelectElement) || !select.form) {
        throw new Error('aeries gradebook selector missing');
    }
    const options = [...select.options];
    if (options.length === 0 || options.length > 64) {
        throw new Error('aeries gradebook options invalid');
    }
    const values = options.map(option => option.value);
    if (values.some(value => !value) || new Set(values).size !== values.length) {
        throw new Error('aeries gradebook option identity invalid');
    }
    const form = select.form;
    const entries = [...new FormData(form).entries()];
    return await Promise.all(options.map(async option => {
        const body = new URLSearchParams(entries);
        body.set('__EVENTTARGET', select.name);
        body.set('__EVENTARGUMENT', '');
        body.set('__ASYNCPOST', 'true');
        body.set(select.name, option.value);
        const response = await fetch(form.action, {
            method: 'POST',
            body,
            credentials: 'same-origin',
            headers: {
                'X-MicrosoftAjax': 'Delta=true',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });
        if (!response.ok) {
            throw new Error('aeries gradebook request failed');
        }
        return {
            course: (option.textContent || '').trim(),
            html: await response.text()
        };
    }));
}
"""


def _text(element: Tag | None) -> str:
    return " ".join(element.get_text(" ", strip=True).split()) if element else ""


def _course_title(raw: str) -> str:
    title = _GRADEBOOK_NUMBER.sub("", " ".join(raw.split()))
    return _TERM_SUFFIX.sub("", title).strip(" -")


def _score_percentage(card: Tag) -> float | None:
    raw = _text(card.select_one(_COMPLETE_SCORE))
    percentages = list(_PERCENT.finditer(raw))
    if percentages:
        return float(percentages[-1].group(1))
    points = list(_POINTS.finditer(raw))
    if not points:
        return None
    earned = float(points[-1].group(1))
    possible = float(points[-1].group(2))
    return earned / possible * 100 if possible > 0 else None


def _status(
    *, due_date: date, grading_complete: bool, percentage: float | None, today: date
) -> AgendaStatus | None:
    if due_date < today and (not grading_complete or percentage == 0):
        return "missing"
    if percentage is not None and percentage < 80:
        return "low_score"
    if grading_complete or percentage is not None:
        return None
    return "due"


def parse_aeries_gradebook(
    html: str, *, course: str, reference: date | datetime | None = None
) -> list[AgendaRecord]:
    normalized_course = _course_title(course)
    if not normalized_course:
        raise AeriesAgendaError()
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(_ASSIGNMENT_ROOT)
    if root is None:
        raise AeriesAgendaError()
    today = (
        reference.date()
        if isinstance(reference, datetime)
        else reference
        if isinstance(reference, date)
        else date.today()
    )
    records: list[AgendaRecord] = []
    for card in root.select(_ASSIGNMENT_CARD):
        title = _ASSIGNMENT_NUMBER.sub("", _text(card.select_one(_TITLE))).strip()
        card_text = _text(card)
        due_match = _DATE.search(card_text)
        complete_match = _GRADING_COMPLETE.search(card_text)
        if not title or due_match is None or complete_match is None:
            raise AeriesAgendaError()
        try:
            due_date = datetime.strptime(due_match.group(1), "%m/%d/%Y").date()
        except ValueError:
            raise AeriesAgendaError() from None
        time_match = _TIME.search(card_text)
        try:
            due_time = (
                datetime.strptime(time_match.group(1).upper(), "%I:%M %p").strftime(
                    "%H:%M"
                )
                if time_match is not None
                else None
            )
        except ValueError:
            raise AeriesAgendaError() from None
        status = _status(
            due_date=due_date,
            grading_complete=complete_match.group(1).casefold() == "true",
            percentage=_score_percentage(card),
            today=today,
        )
        if status is None:
            continue
        record: AgendaRecord = {
            "course": normalized_course,
            "title": title,
            "dueDate": due_date.isoformat(),
            "dueTime": due_time,
            "status": status,
        }
        category = normalize_assignment_category(_text(card.select_one(_CATEGORY)))
        if category is not None:
            record["category"] = category
        records.append(record)
    return records


async def collect_aeries_agenda(page: Page, *, login_url: str) -> list[AgendaRecord]:
    current = urlsplit(page.url)
    configured = urlsplit(login_url)
    if (
        current.scheme != "https"
        or configured.scheme != "https"
        or current.hostname != configured.hostname
    ):
        raise AeriesAgendaError("aeries_agenda_origin_unverified")
    details_url = urljoin(page.url, "GradebookDetails.aspx")
    _ = await page.goto(details_url, wait_until="domcontentloaded", timeout=30_000)
    final = urlsplit(page.url)
    if final.hostname != configured.hostname or not final.path.endswith(
        "/GradebookDetails.aspx"
    ):
        raise AeriesAgendaError("aeries_agenda_origin_unverified")
    selector = page.locator(_GRADEBOOK_SELECTOR)
    await selector.wait_for(state="visible", timeout=30_000)
    try:
        payloads = cast(object, await page.evaluate(_FETCH_GRADEBOOKS))
    except Exception:
        raise AeriesAgendaError("aeries_agenda_request_failed") from None
    if not isinstance(payloads, list):
        raise AeriesAgendaError()
    payload_list = cast(list[object], payloads)
    if not 0 < len(payload_list) <= _MAX_GRADEBOOKS:
        raise AeriesAgendaError()
    records: list[AgendaRecord] = []
    for raw_payload in payload_list:
        if not isinstance(raw_payload, Mapping):
            raise AeriesAgendaError()
        payload = cast(Mapping[object, object], raw_payload)
        course = payload.get("course")
        html = payload.get("html")
        if not isinstance(course, str) or not isinstance(html, str):
            raise AeriesAgendaError()
        records.extend(parse_aeries_gradebook(html, course=course))
    return records
