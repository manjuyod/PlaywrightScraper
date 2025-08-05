"""
Infinite Campus scraper for Gilbert Public Schools (parent portal).

This engine logs into the Gilbert, AZ Infinite Campus parent portal,
optionally selects a specific student (based on first name), fetches
semester grades, and supports logging out.  It accommodates both the
modern Angular component layout (`<app-student-summary-button>`) and
older anchor‑based layouts for choosing a student.  Use this engine
with the engine key ``infinite_campus_parent_gilbert``.

Usage example::

    from scraper.portals import get_portal
    Engine = get_portal("infinite_campus_parent_gilbert")
    scraper = Engine(page, username, password, student_name="Nikolas")
    await scraper.login()       # will auto‑select Nikolas
    grades = await scraper.fetch_grades()
    await scraper.logout()

The ``student_name`` passed at construction (via the base
``PortalEngine``) is used automatically; you may also override
selection via ``login(first_name=...)``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from playwright.async_api import Page

from .base import PortalEngine
from . import register_portal

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


@register_portal("infinite_campus_parent_gilbert")
class InfiniteCampus(PortalEngine):
    """Scraper implementation for the Gilbert, AZ Infinite Campus parent portal."""

    LOGIN: str = "https://gilbertaz.infinitecampus.org/campus/gilbert.jsp"
    GRADEBOOK: str = (
        "https://gilbertaz.infinitecampus.org/campus/nav-wrapper/parent/portal/parent/grades?appName=gilbert"
    )
    LOGOFF: str = (
        "https://gilbertaz.infinitecampus.org/campus/portal/parents/gilbert.jsp?status=logoff"
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Log into the Infinite Campus portal.

        Args:
            first_name: Optional override for the student’s first name.  If
                omitted, ``self.student_name`` provided at instantiation is
                used when selecting a student on the home page.
        """
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.fill("input#username", self.sid)
        await self.page.fill("input#password", self.pw)
        await self.page.wait_for_timeout(200)
        await self.page.click('button[type="submit"]')
        await self.page.wait_for_url(lambda url: "home" in url, timeout=15_000)
        await self.page.wait_for_timeout(5_000)
        # Use explicit first_name if provided; otherwise use student_name from the base class
        target_name: Optional[str] = first_name or getattr(self, "student_name", None)
        if target_name:
            await self._select_student_by_first_name(target_name)
        # Save the page HTML for debugging (optional)
        with open("gilbert_parent_portal.html", "w", encoding="utf-8") as f:
            f.write(await self.page.content())

    async def _select_student_by_first_name(self, first_name: str) -> None:
        """Select a student on the home page by clicking the appropriate card.

        This method supports both modern (`app-student-summary-button`) and
        older anchor‑based layouts.  It attempts to locate a student
        whose displayed name contains ``first_name`` (case‑insensitive) and
        then clicks the corresponding summary button or link to load that
        student’s profile (causing the URL to include ``personID=``).

        Args:
            first_name: The first name to search for within the student’s
                displayed name.

        Raises:
            RuntimeError: If no matching student button or link is found.
        """
        # Wait for either summary buttons or anchor links to appear
        await self.page.wait_for_selector(
            "app-student-summary-button, a[href*='personID=']",
            timeout=15_000,
        )

        # --- Modern layout: app-student-summary-button ---
        summary_buttons = self.page.locator("app-student-summary-button")
        summary_count = await summary_buttons.count()
        for idx in range(summary_count):
            btn = summary_buttons.nth(idx)
            # Each summary button contains an element with class "studentSummary__student-name"
            name_locator = btn.locator(".studentSummary__student-name")
            if await name_locator.count() > 0:
                try:
                    text = (await name_locator.inner_text()).strip()
                except Exception:
                    continue
                # Do a substring match rather than prefix match
                if first_name.lower() in text.lower():
                    # Click the button's inner clickable element if it exists; otherwise click the component itself
                    click_target = btn.locator(".studentSummary__button")
                    if await click_target.count() > 0:
                        await click_target.click()
                    else:
                        await btn.click()
                    # Wait until the URL updates to include personID (indicates student is selected)
                    await self.page.wait_for_url(
                        lambda url: "personID=" in url, timeout=10_000
                    )
                    return

        # --- Older layout: anchor tags with personID query ---
        student_links = self.page.locator("a[href*='personID=']")
        count = await student_links.count()
        for idx in range(count):
            link = student_links.nth(idx)
            try:
                text = (await link.inner_text()).strip()
            except Exception:
                continue
            # Substring match on the anchor's text
            if first_name.lower() in text.lower():
                href = await link.get_attribute("href")
                if href:
                    full_url = urljoin("https://gilbertaz.infinitecampus.org", href)
                    await self.page.goto(full_url, wait_until="domcontentloaded")
                    await self.page.wait_for_load_state("networkidle")
                    return
                else:
                    await link.click()
                    await self.page.wait_for_url(
                        lambda url: "personID=" in url, timeout=10_000
                    )
                    return

        # --- Final fallback: parse the static HTML (rarely needed) ---
        html = await self.page.content()
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            if "personID=" not in anchor["href"]:
                continue
            text = anchor.get_text(strip=True)
            if first_name.lower() in text.lower():
                full_url = urljoin("https://gilbertaz.infinitecampus.org", anchor["href"])
                await self.page.goto(full_url, wait_until="domcontentloaded")
                await self.page.wait_for_load_state("networkidle")
                return
        raise RuntimeError(
            f"Student with first name '{first_name}' not found on home page"
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Fetch semester grades for the currently selected student."""
        await self.page.goto(self.GRADEBOOK, wait_until="domcontentloaded")
        # Allow some time for dynamic content to load
        await self.page.wait_for_timeout(3_000)
        # Wait for the gradebook iframe and confirm it points to /apps/portal/parent/grades
        await self.page.wait_for_selector("iframe#main-workspace", timeout=15_000)
        await self.page.wait_for_function(
            """() => {
                const f = document.querySelector('iframe#main-workspace');
                return f && f.src && f.src.includes('/apps/portal/parent/grades');
            }""",
            timeout=15_000,
        )
        frame = self.page.frame(url=lambda u: "/apps/portal/parent/grades" in u)
        if not frame:
            raise RuntimeError("Grade iframe never loaded")
        await frame.wait_for_load_state("networkidle")
        # Extract the HTML from the gradebook iframe
        html_dump = await frame.content()
        parsed = self._parse_semester_grades(html_dump)
        return {
            "parsed_grades": parsed,
        }

    def _parse_semester_grades(self, html: str) -> Dict[str, Any]:
        """Extract semester grades into a dictionary keyed by course name."""
        soup = BeautifulSoup(html, "html.parser")
        courses: Dict[str, Any] = {}
        for card in soup.select("div.collapsible-card.grades__card"):
            course_name_tag = card.select_one("h4 a")
            if not course_name_tag:
                continue
            course_name = course_name_tag.get_text(strip=True)
            grade_value: Optional[Any] = None
            letter_grade: Optional[str] = None
            percentage: Optional[float] = None
            # Find the list item specifically for "Semester Grade"
            semester_li = None
            for li in card.select("li"):
                if "Semester Grade" in li.get_text():
                    semester_li = li
                    break
            if semester_li:
                score_container = semester_li.select_one(
                    "tl-grading-score .grading-score"
                )
                if score_container:
                    # Extract letter grade and percentage if present
                    letter_grade_tag = score_container.find("div", recursive=False)
                    if letter_grade_tag:
                        letter_grade = letter_grade_tag.get_text(strip=True)
                    percentage_tag = score_container.find(
                        "div",
                        string=lambda text: text and "%" in text,
                    )
                    if percentage_tag:
                        try:
                            percent_text = (
                                percentage_tag.get_text(strip=True).strip("()%")
                            )
                            percentage = float(percent_text)
                        except (ValueError, TypeError):
                            pass
            # Prioritize percentage over letter grade
            if percentage is not None:
                grade_value = percentage
            elif letter_grade is not None:
                grade_value = letter_grade
            courses[course_name] = grade_value
        return courses

    async def logout(self) -> None:
        """Log out of the Infinite Campus portal and close the page."""
        await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
        await self.page.close()
