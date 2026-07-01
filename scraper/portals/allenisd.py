from __future__ import annotations

import re
from typing import Any, Dict

from bs4 import BeautifulSoup, Tag

from . import LoginError, register_portal
from .base import PortalEngine, PlaywrightTimeout
from .utils import canonicalize_course_title, canonicalize_grade


ALLEN_START_URL = "https://portal.allenisd.org/"
ALLEN_SKYWARD_APP_NAME = "Skyward Student"
_RAPIDIDENTITY_PORTAL_PATH = "/p/portal"

_BLOCKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mfa", re.compile(r"\b(mfa|multi[- ]factor|verification code|one[- ]time|authenticator|approve sign[- ]in)\b", re.I)),
    ("security_challenge", re.compile(r"\b(security question|security challenge|challenge question)\b", re.I)),
    ("captcha", re.compile(r"\b(captcha|recaptcha)\b", re.I)),
    ("account_lockout", re.compile(r"\b(account locked|locked out|account disabled)\b", re.I)),
    ("consent_prompt", re.compile(r"\b(consent|permissions requested|accept terms)\b", re.I)),
    ("account_recovery", re.compile(r"\b(account recovery|recover your account|reset your password)\b", re.I)),
)
_GRADE_VALUE_RE = re.compile(r"\b(1[0-4]\d(?:\.\d+)?|150(?:\.0+)?|100(?:\.0+)?|[1-9]?\d(?:\.\d+)?)\s*%?\b")
_GRADE_LABEL_RE = re.compile(r"\b(current\s+grade|overall\s+grade|grade|average|avg)\b", re.I)
_COURSE_HEADER_RE = re.compile(r"\b(course|class)\b", re.I)


@register_portal("allenisd")
class AllenISD(PortalEngine):
    async def login(self, first_name: str | None = None) -> None:
        del first_name

        await self.page.goto(self._login_start_url(), wait_until="domcontentloaded", timeout=30_000)
        await self.page.wait_for_timeout(2500)
        await self._raise_if_blocked("login")

        username_field = self.page.get_by_role("textbox", name="Username")
        await username_field.click()
        await username_field.press_sequentially(self.sid, delay=85)
        await self.page.wait_for_timeout(1200)
        await self.page.get_by_role("button", name="Go").first.click()
        await self.page.wait_for_timeout(2500)
        await self._raise_if_blocked("username")

        password_field = self.page.get_by_role("textbox", name="Password")
        await password_field.click()
        await password_field.press_sequentially(self.pw, delay=95)
        await self.page.wait_for_timeout(1500)
        await password_field.press("Tab")
        await self._press_focused_go_button()

        await self.page.wait_for_url(
            lambda url: _RAPIDIDENTITY_PORTAL_PATH in url,
            timeout=30_000,
            wait_until="domcontentloaded",
        )
        await self.page.wait_for_timeout(2500)
        await self._raise_if_blocked("portal")

        async with self.page.expect_popup() as page_info:
            await self.page.get_by_text(ALLEN_SKYWARD_APP_NAME, exact=True).first.click()
        skyward_page = await page_info.value
        await skyward_page.wait_for_load_state("domcontentloaded")
        await skyward_page.wait_for_timeout(4000)
        self.page = skyward_page

    async def fetch_grades(self) -> Dict[str, Any]:
        await self.page.get_by_role("menuitem", name="Gradebook").click()
        await self.page.wait_for_load_state("domcontentloaded")
        await self.page.wait_for_timeout(2500)
        await self._raise_if_blocked("gradebook")

        await self.page.get_by_role("link", name="Display Options").click()
        await self.page.get_by_role("link", name="View All Grades").click()
        try:
            await self.page.wait_for_load_state("networkidle")
        except PlaywrightTimeout:
            await self.page.wait_for_timeout(1500)

        html = await self.page.content()
        parsed = self.parse_gradebook_html(html)
        if not parsed:
            raise LoginError("AllenISD gradebook loaded but no course grades were parsed")
        return {"parsed_grades": parsed}

    def _login_start_url(self) -> str:
        if self.login_url and "portal.allenisd.org" in self.login_url.lower():
            return ALLEN_START_URL
        return ALLEN_START_URL

    async def _press_focused_go_button(self) -> None:
        for _ in range(5):
            active_id = await self.page.evaluate("() => document.activeElement?.id || ''")
            if active_id == "authn-go-button":
                await self.page.keyboard.press("Enter")
                return
            await self.page.keyboard.press("Tab")
        await self.page.keyboard.press("Enter")

    async def _raise_if_blocked(self, stage: str) -> None:
        text = await self._body_text()
        for reason, pattern in _BLOCKER_PATTERNS:
            if pattern.search(text):
                raise LoginError(f"AllenISD {stage} blocked: {reason}")

    async def _body_text(self) -> str:
        try:
            return await self.page.locator("body").inner_text(timeout=1000)
        except Exception:
            return ""

    @classmethod
    def parse_gradebook_html(cls, html: str) -> Dict[str, float]:
        soup = BeautifulSoup(html, "html.parser")
        for elem in soup.select("script, style, noscript"):
            elem.decompose()

        parsed: Dict[str, float] = {}
        for table in soup.select("table"):
            cls._parse_header_table(table, parsed)
            cls._parse_section_table(table, parsed)
            cls._parse_generic_rows(table, parsed)
        return parsed

    @classmethod
    def _parse_header_table(cls, table: Tag, parsed: Dict[str, float]) -> None:
        rows = table.select("tr")
        if not rows:
            return

        headers = cls._row_cells(rows[0])
        course_idx = cls._first_matching_index(headers, _COURSE_HEADER_RE)
        grade_idx = cls._first_matching_index(headers, _GRADE_LABEL_RE)
        if course_idx is None or grade_idx is None:
            return

        for row in rows[1:]:
            cells = cls._row_cells(row)
            if len(cells) <= max(course_idx, grade_idx):
                continue
            cls._add_grade(parsed, cells[course_idx], cells[grade_idx])

    @classmethod
    def _parse_section_table(cls, table: Tag, parsed: Dict[str, float]) -> None:
        current_course: str | None = None
        for row in table.select("tr"):
            cells = cls._row_cells(row)
            if not cells:
                continue
            if len(cells) == 1:
                current_course = cls._normalize_course(cells[0])
                continue
            if current_course and _GRADE_LABEL_RE.search(" ".join(cells)):
                grade = cls._grade_from_cells(reversed(cells))
                if grade is not None:
                    parsed[current_course] = grade

    @classmethod
    def _parse_generic_rows(cls, table: Tag, parsed: Dict[str, float]) -> None:
        for row in table.select("tr"):
            cells = cls._row_cells(row)
            if len(cells) < 2:
                continue
            grade = cls._grade_from_cells(reversed(cells))
            course = cls._normalize_course(cells[0])
            if course and grade is not None:
                parsed.setdefault(course, grade)

    @staticmethod
    def _row_cells(row: Tag) -> list[str]:
        return [
            " ".join(cell.get_text(" ", strip=True).split())
            for cell in row.find_all(["th", "td"], recursive=False)
        ]

    @staticmethod
    def _first_matching_index(values: list[str], pattern: re.Pattern[str]) -> int | None:
        for index, value in enumerate(values):
            if pattern.search(value):
                return index
        return None

    @classmethod
    def _add_grade(cls, parsed: Dict[str, float], course_text: str, grade_text: str) -> None:
        course = cls._normalize_course(course_text)
        grade = cls._grade_from_text(grade_text)
        if course and grade is not None:
            parsed[course] = grade

    @staticmethod
    def _normalize_course(text: str) -> str | None:
        text = re.sub(r"^\s*(course|class)\s*:\s*", "", text, flags=re.I)
        text = " ".join(text.split()).strip()
        if not text or _GRADE_LABEL_RE.fullmatch(text) or _GRADE_VALUE_RE.fullmatch(text):
            return None
        if text.lower() in {"teacher", "not graded", "show assignments", "display options"}:
            return None
        return canonicalize_course_title(text)

    @classmethod
    def _grade_from_cells(cls, cells: Any) -> float | None:
        for text in cells:
            grade = cls._grade_from_text(text)
            if grade is not None:
                return grade
        return None

    @staticmethod
    def _grade_from_text(text: str) -> float | None:
        match = _GRADE_VALUE_RE.search(text)
        if not match:
            return None
        return canonicalize_grade(match.group(1))
