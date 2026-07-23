from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup  # type: ignore[import-untyped]
from bs4.element import Tag
from playwright.async_api import Frame, TimeoutError as PlaywrightTimeout
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from typing_extensions import override

from . import register_portal
from .base import PortalEngine
from .utils import (
    canonicalize_course_title,
    canonicalize_grade,
    exists,
    universal_login_flow,
    wait_after_nav,
)


_INVALID_LOGIN_TEXT = "Your attempt to log in was unsuccessful."
_ASSIGNMENTS_PATH = "/HomeAccess/Content/Student/Assignments.aspx"


@register_portal("homeaccess")
class HomeAccess(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    @override
    async def login(self, first_name: str | None = None) -> None:
        _ = first_name
        self.logger.info("portal.login.started")
        try:
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                "#LogOnDetails_UserName",
                "#LogOnDetails_Password",
            )

            login_failed = await exists(
                self.page.get_by_text(_INVALID_LOGIN_TEXT, exact=False)
            )
            await self.raise_login_error_if(
                login_failed or "/Account/LogOn" in self.page.url,
                "HomeAccess login did not leave the logon page",
            )

            _ = await self.page.goto(
                self._classwork_url(), wait_until="domcontentloaded"
            )
            await wait_after_nav(
                self.page,
                pattern=lambda url: "/HomeAccess/Classes/Classwork" in url if url else False,
                wait_until="domcontentloaded",
                wait_after_load=1000,
            )

            frame = await self._get_classwork_frame()
            await self.raise_login_error_if(
                frame is None,
                "HomeAccess classwork iframe was not available after login",
            )
            self.logger.info("portal.login.succeeded")
        except Exception:
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    @override
    async def fetch_grades(self) -> dict[str, object]:
        self.logger.info("portal.fetch.started")
        frame = await self._get_classwork_frame()
        if frame is None:
            raise self.LoginError("HomeAccess classwork iframe not found")

        html = await frame.content()
        parsed = self.parse_classwork_html(html)
        self.logger.info(
            "portal.fetch.completed", extra={"course_count": len(parsed)}
        )
        return {"parsed_grades": parsed}

    def _classwork_url(self) -> str:
        parsed = urlsplit(self.login_url)
        return f"{parsed.scheme}://{parsed.netloc}/HomeAccess/Classes/Classwork"

    async def _get_classwork_frame(self, timeout_ms: int = 5000) -> Frame | None:
        attempts = max(1, timeout_ms // 500)
        for _ in range(attempts):
            frame = self.page.frame(name="sg-legacy-iframe")
            if frame is not None:
                return frame

            frame = self.page.frame(
                url=lambda url: _ASSIGNMENTS_PATH in url if url else False
            )
            if frame is not None:
                return frame

            await self.page.wait_for_timeout(500)
        return None

    @classmethod
    def parse_classwork_html(cls, html: str) -> dict[str, float]:
        soup = BeautifulSoup(html, "html.parser")
        parsed: dict[str, float] = {}

        for card in soup.select("div.AssignmentClass"):
            title_elem = card.select_one("a.sg-header-heading")
            if title_elem is None:
                continue

            title = cls._normalize_course_title(title_elem.get_text(" ", strip=True))
            grade = cls._extract_average(card)
            if title and grade is not None:
                parsed[title] = grade

        return parsed

    @staticmethod
    def _normalize_course_title(title: str) -> str:
        stripped = re.sub(r"^\s*\d[\d\s]*\s*-\s*[\w]+\s+", "", title).strip()
        return canonicalize_course_title(stripped)

    @staticmethod
    def _extract_average(card: Tag) -> float | None:
        for elem in card.select("span.sg-header-heading"):
            text = elem.get_text(" ", strip=True)
            match = re.search(r"MP Average\s*([0-9]+(?:\.[0-9]+)?)%", text, re.I)
            if match:
                return canonicalize_grade(match.group(1))

        card_text = card.get_text(" ", strip=True)
        fallback = re.search(
            r"Course overall average is:.*?=\s*([0-9]+(?:\.[0-9]+)?)%",
            card_text,
            re.I,
        )
        if fallback:
            return canonicalize_grade(fallback.group(1))
        return None
