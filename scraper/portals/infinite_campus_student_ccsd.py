"""
Updated Infinite Campus student portal scraper for Clark County School District (CCSD).

This module is based on ``scraper/portals/infinite_campus_student_ccsd.py`` from
the PlaywrightScraper repository.  The original implementation assumed
that grades were always delivered via an ``iframe#main-workspace``.
When this iframe is absent the original scraper would return an empty
result (sometimes showing ``about:blank``).  The updated version adds
a fallback that waits for grade cards rendered in the main document and
parses them accordingly.  Timeouts have been extended to improve
reliability on slower connections.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional

from bs4 import BeautifulSoup  # type: ignore
from playwright.async_api import Page  # type: ignore
from .base import PortalEngine
from . import register_portal  # helper we'll create in __init__.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


@register_portal("infinite_campus_student_ccsd")
class InfiniteCampus(PortalEngine):
    """Student portal scraper for CCSD's Infinite Campus."""

    LOGIN = "https://campus.ccsd.net/campus/portal/students/clark.jsp"
    GRADEBOOK = (
        "https://campus.ccsd.net/campus/nav-wrapper/student/portal/student/grades?appName=clark"
    )
    LOGOFF = "https://campus.ccsd.net/campus/portal/students/clark.jsp?status=logoff"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the student on the CCSD student portal.

        Args:
            first_name: Ignored for student portals.  Included to match
                the ``PortalEngine`` interface which allows callers to
                specify a student name when multiple profiles are
                available (not applicable for student logins).
        """
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.fill("input#username", self.sid)
        await self.page.fill("input#password", self.pw)
        # short debounce
        await self.page.wait_for_timeout(200)
        # submit the form via Enter key
        await self.page.locator('.form-group input[name="password"]').press("Enter")
        # wait for redirection to home page
        await self.page.wait_for_url(lambda url: "home" in url, timeout=15_000)
        await self.page.wait_for_load_state("networkidle")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> dict:
        """Navigate to the gradebook and return parsed quarter grades."""
        await self.page.goto(self.GRADEBOOK, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(3_000)
        html_dump: Optional[str] = None
        frame = None
        try:
            await self.page.wait_for_selector("iframe#main-workspace", timeout=10_000)
            frame = self.page.frame(
                url=lambda u: "/apps/portal/student/grades" in u if u else False
            )
        except Exception:
            frame = None
        if frame:
            await frame.wait_for_load_state("networkidle")
            html_dump = await frame.content()
        else:
            await self.page.wait_for_selector(
                "div.collapsible-card.grades__card, div.collapsible-card, div.card",
                timeout=30_000,
            )
            await self.page.wait_for_load_state("networkidle")
            html_dump = await self.page.content()
        parsed = self._parse_quarter_grades(html_dump or "")
        return {"parsed_grades": parsed}

    # Grade Parser Function
    def _parse_quarter_grades(self, html: str) -> List[Dict[str, Any]]:
        """Extract quarter grades (letter + percentage) from grade-page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        courses: List[Dict[str, Any]] = []
        for card in soup.select("div.collapsible-card.grades__card"):
            header = card.find("tl-grading-section-header")
            if not header:
                continue
            name_tag = header.find("a") or header.find("h4")
            if not name_tag:
                continue
            course_name = name_tag.get_text(strip=True)
            task_list = card.find("tl-grading-task-list")
            if not task_list:
                continue
            quarter_grade = None
            for li in task_list.find_all("li"):
                grade_type = li.find("span", class_="ng-star-inserted")
                if not grade_type or "Quarter Grade" not in grade_type.text:
                    continue
                score_span = li.find("tl-grading-score")
                if not score_span:
                    continue
                grade_data: Dict[str, Any] = {"type": grade_type.text.strip()}
                letter_b = score_span.find("b")
                if letter_b:
                    grade_data["letter_grade"] = letter_b.text.strip()
                for b in score_span.find_all("b"):
                    txt = b.text.strip()
                    if txt.startswith("(") and "%" in txt:
                        try:
                            grade_data["percentage"] = float(txt.strip("()%"))
                        except ValueError:
                            grade_data["percentage_raw"] = txt
                        break
                quarter_grade = grade_data
                break
            if quarter_grade:
                courses.append({"course_name": course_name, "quarter_grade": quarter_grade})
        return courses