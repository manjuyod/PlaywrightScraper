from __future__ import annotations

from typing import List, Dict, Any, Optional

from bs4 import BeautifulSoup  # type: ignore
from playwright.async_api import Page  # type: ignore
from .base import PortalEngine
from . import register_portal  # helper we'll create in __init__.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

#TODO: Parse and implement functionality
# Will the same parsing work for all infinite campus?

@register_portal("infinite_campus_parent_asuprep")
class InfiniteCampus(PortalEngine):
    """Parent portal scraper for ASU Preps's Infinite Campus.

    The class uses Playwright to automate login and extract quarter grades
    for each course.  Grades are returned as a list of course/grade
    dictionaries under the ``parsed_grades`` key.
    """

    LOGIN = "https://asuprepaz.infinitecampus.org/campus/portal/parents/asuprep.jsp"
    GRADEBOOK = (
        ""
    )
    LOGOFF = ""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the CCSD parent portal.

        Args:
            first_name: An optional first name for selecting a specific
                student profile after login.  The CCSD parent portal
                currently does not expose multiple profiles per login,
                so this argument is ignored, but it is accepted for
                compatibility with the ``PortalEngine`` interface.
        """
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        # Fill username and password
        await self.page.fill("input#username", self.sid)
        await self.page.fill("input#password", self.pw)
        # Short pause to ensure fields are recognized
        await self.page.wait_for_timeout(200)
        # Press Enter in password field to submit the form
        await self.page.locator('.form-group input[name="password"]').press("Enter")
        # Wait until the URL contains "home" indicating successful login
        await self.page.wait_for_url(lambda url: "home" in url, timeout=15_000)
        # Wait for network to be idle to ensure the home page is fully loaded
        await self.page.wait_for_load_state("networkidle")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> dict:
        """Navigate to the gradebook and return a dict of parsed grades."""
        await self.page.goto(self.GRADEBOOK, wait_until="domcontentloaded")
        # Allow some time for dynamic content to load
        await self.page.wait_for_timeout(3_000)
        html_dump: Optional[str] = None
        frame = None
        # Attempt to find the legacy iframe
        try:
            await self.page.wait_for_selector("iframe#main-workspace", timeout=10_000)
            frame = self.page.frame(
                url=lambda u: "/apps/portal/parent/grades" in u if u else False
            )
        except Exception:
            frame = None
        if frame:
            # Wait for network idle inside the iframe and capture its content
            await frame.wait_for_load_state("networkidle")
            html_dump = await frame.content()
        else:
            # No iframe present – grades are in the top‑level page.  Wait for
            # grade cards to appear and for the network to be idle before
            # collecting the HTML.
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
        # course cards
        for card in soup.select("div.collapsible-card.grades__card"):
            header = card.find("tl-grading-section-header")
            if not header:
                continue
            # course name (link or h4 fallback)
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
                # first → letter grade
                letter_b = score_span.find("b")
                if letter_b:
                    grade_data["letter_grade"] = letter_b.text.strip()
                # any (xx.x%) → percentage
                for b in score_span.find_all("b"):
                    txt = b.text.strip()
                    if txt.startswith("(") and "%" in txt:
                        try:
                            grade_data["percentage"] = float(txt.strip("()%"))
                        except ValueError:
                            grade_data["percentage_raw"] = txt
                        break
                quarter_grade = grade_data
                break  # only one per course
            if quarter_grade:
                courses.append({"course_name": course_name, "quarter_grade": quarter_grade})
        return courses