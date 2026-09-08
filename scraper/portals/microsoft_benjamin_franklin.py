from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup
from typing_extensions import override
from .base import PortalEngine
from .utils import log_retry
from .utils import canonicalize_course_title, canonicalize_grade
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


class Microsoft(PortalEngine):
    """Portal scraper for Microsoft portals.

    The class uses Playwright to automate login and extract quarter grades
    for each course.
    """

    portal_key = "microsoft_benjamin_franklin"
    url_patterns = ("benjaminfranklincs",)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=log_retry,
    )
    @override
    async def login(self, first_name: str | None = None) -> None:
        """Authenticate the user on the CCSD parent portal.

        Args:
            first_name: An optional first name for selecting a specific
                student profile after login.  The CCSD parent portal
                currently does not expose multiple profiles per login,
                so this argument is ignored, but it is accepted for
                compatibility with the ``PortalEngine`` interface.
        """
        _ = first_name
        self.logger.info("portal.login.started")
        await self.page.goto(self.login_url, wait_until="domcontentloaded")
        await self.microsoft_login()
        # Wait until the URL contains "home" indicating successful login
        await self.page.wait_for_url(lambda url: "home" in url, timeout=15_000)
        # Wait for network to be idle to ensure the home page is fully loaded
        await self.page.wait_for_load_state("networkidle")
        self.logger.info("portal.login.completed")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=log_retry,
    )
    @override
    async def fetch_grades(self) -> dict[str, float]:
        """Navigate to the gradebook and return a dict of parsed grades."""
        self.logger.info("portal.fetch.started")
        gradebook_url = self.alt_portal_url or urljoin(
            self.page.url, "/apps/portal/parent/grades"
        )
        self.logger.debug(
            "portal.navigation.gradebook",
            extra={
                "navigation_source": (
                    "configured_alt_url" if self.alt_portal_url else "portal_origin"
                )
            },
        )
        await self.page.goto(gradebook_url, wait_until="domcontentloaded")
        # Allow some time for dynamic content to load
        await self.page.wait_for_timeout(3_000)
        html_dump: str | None = None
        frame = None
        # Attempt to find the legacy iframe
        try:
            await self.page.wait_for_selector("iframe#main-workspace", timeout=10_000)
            frame = self.page.frame(
                url=lambda u: "/apps/portal/parent/grades" in u if u else False
            )
        except Exception:
            self.logger.debug("portal.fetch.legacy_frame_missing")
            frame = None
        if frame:
            self.logger.debug("portal.fetch.legacy_frame_selected")
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
        self.logger.info(
            "portal.fetch.completed", extra={"course_count": len(parsed)}
        )
        return parsed

    # Grade Parser Function
    def _parse_quarter_grades(self, html: str) -> dict[str, float]:
        """Extract the current quarter percentage for each course."""
        soup = BeautifulSoup(html, "html.parser")
        courses: dict[str, float] = {}
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
            quarter_grade: float | None = None
            for li in task_list.find_all("li"):
                grade_type = li.find("span", class_="ng-star-inserted")
                if not grade_type or "Quarter Grade" not in grade_type.text:
                    continue
                score_span = li.find("tl-grading-score")
                if not score_span:
                    continue
                letter_grade: str | None = None
                letter_b = score_span.find("b")
                if letter_b:
                    letter_grade = letter_b.text.strip()
                for b in score_span.find_all("b"):
                    txt = b.text.strip()
                    if txt.startswith("(") and "%" in txt:
                        quarter_grade = canonicalize_grade(txt)
                        break
                if quarter_grade is None and letter_grade:
                    quarter_grade = canonicalize_grade(letter_grade)
                break
            if quarter_grade is not None:
                courses[canonicalize_course_title(course_name)] = quarter_grade
        return courses
