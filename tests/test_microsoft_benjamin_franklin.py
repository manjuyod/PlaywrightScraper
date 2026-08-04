from __future__ import annotations

import asyncio
from typing import cast

from playwright.async_api import Page

from scraper.portals.microsoft_benjamin_franklin import Microsoft


class FakePage:
    url = "https://benjaminfranklincs.powerschool.com/home"

    def __init__(self) -> None:
        self.visited_url: str | None = None

    async def goto(self, url: str, **_kwargs: object) -> None:
        self.visited_url = url

    async def wait_for_timeout(self, _timeout: int) -> None:
        return None

    async def wait_for_selector(self, selector: str, **_kwargs: object) -> None:
        if selector == "iframe#main-workspace":
            raise RuntimeError("legacy frame absent")

    async def wait_for_load_state(self, _state: str) -> None:
        return None

    async def content(self) -> str:
        return ""

    def frame(self, **_kwargs: object) -> None:
        return None


def _engine(page: FakePage, *, alt_portal_url: str | None = None) -> Microsoft:
    return Microsoft(
        cast(Page, page),
        "student",
        "password",
        "https://login.example",
        alt_portal_url=alt_portal_url,
    )


def test_fetch_grades_derives_gradebook_from_authenticated_portal_origin() -> None:
    page = FakePage()

    result = asyncio.run(_engine(page).fetch_grades())

    assert page.visited_url == (
        "https://benjaminfranklincs.powerschool.com/apps/portal/parent/grades"
    )
    assert result == {}


def test_fetch_grades_prefers_configured_alternate_portal_url() -> None:
    page = FakePage()
    gradebook_url = "https://grades.example/parent"

    asyncio.run(_engine(page, alt_portal_url=gradebook_url).fetch_grades())

    assert page.visited_url == gradebook_url


def test_quarter_grade_parser_returns_normalized_percentages() -> None:
    html = """
    <div class="collapsible-card grades__card">
      <tl-grading-section-header><a>Algebra I</a></tl-grading-section-header>
      <tl-grading-task-list>
        <li>
          <span class="ng-star-inserted">Quarter Grade</span>
          <tl-grading-score><b>A</b><b>(93.5%)</b></tl-grading-score>
        </li>
      </tl-grading-task-list>
    </div>
    <div class="collapsible-card grades__card">
      <tl-grading-section-header><h4>English</h4></tl-grading-section-header>
      <tl-grading-task-list>
        <li>
          <span class="ng-star-inserted">Quarter Grade</span>
          <tl-grading-score><b>B+</b></tl-grading-score>
        </li>
      </tl-grading-task-list>
    </div>
    """

    assert _engine(FakePage())._parse_quarter_grades(html) == {
        "ALGEBRA I": 93.5,
        "ENGLISH": 89.0,
    }
