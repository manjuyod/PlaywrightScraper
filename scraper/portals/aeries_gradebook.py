"""Tustin's current Aeries gradebook and its verified legacy URL transition."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from playwright.async_api import Page

from .base import GradeMap
from .utils import canonicalize_course_title


_TUSTIN_LEGACY = "parentnet.tustin.k12.ca.us"
_TUSTIN_CURRENT = "tustinusd.aeries.net"
_SUMMARY = "[id$='subGBS_tblEverything']"
_INACTIVE = re.compile(r"\b(?:Dropped|Inactive)\b", re.IGNORECASE)
_PERCENTAGE = re.compile(r"\(?\s*(\d+(?:\.\d+)?)\s*%?\s*\)?")


def is_tustin_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in {
        _TUSTIN_LEGACY, _TUSTIN_CURRENT
    }


def is_trusted_aeries_url(current_url: str, login_url: str) -> bool:
    """Require the configured HTTPS origin, with one observed district alias."""
    try:
        current, configured = urlsplit(current_url), urlsplit(login_url)
        if any(
            value.scheme != "https" or not value.hostname or value.username or value.password
            for value in (current, configured)
        ):
            return False
        current_port, configured_port = current.port or 443, configured.port or 443
    except ValueError:
        return False
    if (current.hostname, current_port) == (configured.hostname, configured_port):
        return True
    return (
        configured.hostname == _TUSTIN_LEGACY
        and current.hostname == _TUSTIN_CURRENT
        and current_port == configured_port == 443
    )


def _summary_grade(title: Tag | None, score: Tag | None) -> tuple[str, float] | None:
    if title is None or score is None:
        return None
    name = canonicalize_course_title(title.get_text(" ", strip=True).rstrip(" *"))
    number = _PERCENTAGE.fullmatch(score.get_text(" ", strip=True))
    if not name or number is None:
        return None
    return name, float(number.group(1))


def parse_tustin_grade_summary(html: str) -> GradeMap:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(_SUMMARY)
    if root is None:
        raise RuntimeError("aeries_gradebook_summary_missing")
    grades: GradeMap = {}
    rows = root.select("tr[id$='_trGBKItem']")
    for row in rows:
        cells = row.find_all("td", recursive=False)
        # The final two cells are the status and right border. A dropped
        # gradebook can have the same course name as its active replacement.
        if len(cells) >= 2 and _INACTIVE.search(cells[-2].get_text(" ", strip=True)):
            continue
        grade = _summary_grade(
            row.select_one("[id$='_lbtnCourseTitle']"),
            row.select_one("[id$='_cellTablePoint']"),
        )
        if grade is not None:
            grades[grade[0]] = grade[1]
    if rows:
        return grades
    # Aeries normally includes both views in the DOM, even when cards are
    # selected. Support a card-only response without reading both copies.
    for card in root.select(".Card"):
        if _INACTIVE.search(card.get_text(" ", strip=True)):
            continue
        grade = _summary_grade(
            card.select_one("[id$='_lbtnCardCourseTitle']"),
            card.select_one("[id$='_cellCardAll']"),
        )
        if grade is not None:
            grades[grade[0]] = grade[1]
    return grades


async def collect_tustin_grades(page: Page, *, login_url: str) -> GradeMap:
    if not is_trusted_aeries_url(page.url, login_url):
        raise RuntimeError("aeries_gradebook_origin_unverified")
    await page.goto(
        urljoin(page.url, "GradebookSummary.aspx"),
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    if not is_trusted_aeries_url(page.url, login_url) or not urlsplit(page.url).path.endswith(
        "/GradebookSummary.aspx"
    ):
        raise RuntimeError("aeries_gradebook_origin_unverified")
    await page.locator(_SUMMARY).wait_for(state="attached", timeout=30_000)
    return parse_tustin_grade_summary(await page.content())
