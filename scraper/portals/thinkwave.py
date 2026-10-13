from __future__ import annotations

from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import TimeoutError as PlaywrightTimeout
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from typing_extensions import override

from .base import PortalEngine, UniversalLoginConfig
from .utils import canonicalize_course_title, canonicalize_grade


_GRADES_PATH = "/secure/final_grades/"


class Thinkwave(PortalEngine):
    portal_key = "thinkwave"
    url_patterns = ("thinkwave.com",)
    login_config = UniversalLoginConfig(
        username_selector="#id_username",
        password_selector="#id_password",
    )

    async def validate_login(self) -> None:
        await self.raise_login_error_if(
            urlsplit(self.page.url).path.startswith("/secure/login/")
        )

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        origin = urlsplit(self.login_url)
        await self.page.goto(
            f"{origin.scheme}://{origin.netloc}{_GRADES_PATH}",
            wait_until="domcontentloaded",
        )
        await self.raise_login_error_if(
            urlsplit(self.page.url).path.startswith("/secure/login/")
        )
        await self.page.locator("#mainView .grid-grade").wait_for(state="visible")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    @override
    async def fetch_grades(self) -> dict[str, float]:
        self.logger.info("portal.fetch.started")
        await self.page.locator("#mainView .grid-grade").wait_for(state="visible")
        parsed = self.parse_grades_html(await self.page.content())
        self.logger.info("portal.fetch.completed", extra={"course_count": len(parsed)})
        return parsed

    @staticmethod
    def parse_grades_html(html: str) -> dict[str, float]:
        soup = BeautifulSoup(html, "html.parser")
        parsed: dict[str, float] = {}
        for row in soup.select("#mainView .grid-grade > .grid-body > .grid-item"):
            title = row.select_one(".grid-item-wrap > .row > .col-md-4 .col-md-6 > b")
            grade = row.select_one(".grid-item-wrap > .row > .col-md-8 .grid-item-list .col-6.opacity-70")
            if not isinstance(title, Tag) or not isinstance(grade, Tag):
                continue
            course = canonicalize_course_title(title.get_text(" ", strip=True))
            average = canonicalize_grade(grade.get_text(" ", strip=True))
            if course and average is not None:
                parsed[course] = average
        return parsed
