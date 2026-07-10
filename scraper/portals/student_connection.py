from __future__ import annotations
from typing import Any, Dict, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeout
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .base import PortalEngine
from . import register_portal  # helper we'll create in __init__.py
from .utils import (
    canonicalize_course_title,
    canonicalize_grade,
    exists,
    universal_login_flow,
    wait_after_nav,
)

@register_portal("student_connection")
class StudentConnection(PortalEngine):
    """Portal scraper for Student Connection."""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the StudentConnection portal."""
        username_selector = "input[name='Pin']"
        password_selector = "input[name='Password']"
        try:
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                password_selector,
            )

            try:
                login_not_found = await exists(self.page.get_by_text("Login Not Found", exact=False))
                print("Login found? ", not login_not_found)
                await self.raise_login_error_if(login_not_found)
            except PlaywrightTimeout: # not a failed login if this times out
                pass
            # Wait until the URL contains 'PortalMainPage' indicating successful login, then wait for network idle
            await wait_after_nav(self.page, pattern=lambda url: "PortalMainPage" in url, wait_after_load=2000)




        except Exception as e:
            print(e)
            raise
        finally:
            await self.page.context.tracing.stop()
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """
        Scrape the Pulse table on PortalMainPage and return:
        {"parsed_grades": {"COURSE NAME": 93.4, ...}}
        """
        try:
            pulse = True  # default to using the pulse table
            # parsed: None | Dict[str, Any] = None
            # Brief pause to allow initial widgets to paint
            await self.page.wait_for_timeout(800)

            # Try to ensure Pulse section is visible/expanded
            try:
                img_pulse = self.page.locator("#img_Pulse")
                if await img_pulse.count() > 0:
                    expanded = await img_pulse.get_attribute("aria-expanded")
                    # Some builds use 'true'/'false', others omit; click if clearly collapsed
                    if expanded is not None and expanded.lower() in ("false", "collapsed"):
                        await img_pulse.click()
                        await self.page.wait_for_timeout(400)
                else:
                    pulse = False
            except Exception as e:
                print(e)
                pass  # Not fatal—continue and rely on table presence

            if pulse:
                parsed = await self.collect_from_pulse()
            else:
                print('no pulse, fallback to assignments table')
                parsed = await self.collect_from_assignments()

            # if not parsed:
                # html = await self.page.content()
                # print("Parsed 0 rows. First 4k of HTML follows:")
                # print(html[:4000])
            print(f"[SC] Grades parsed: {parsed}")
            return {"parsed_grades": parsed}
        except Exception as e:
            print(e)
            raise
        finally:
            pass
# HELPERS
    async def collect_from_assignments(self) -> Dict[str, Any]:
        soup = await self.get_soup()
        assignments = soup.find('tr', id='trSP1_Assignments')
        courses_table = assignments.find('div', id='SP_Assignments')
        courses = courses_table.find_all('table')
        parsed: Dict[str, Any] = {}
        for course in courses:
            # title
            course_name = course.find('caption') # get course name from the caption
            course_name = course_name.text.strip()
            # title formatting
            try:
                course_name = course_name[:course_name.index('(')]
            except ValueError:
                pass
            if "Per" in course_name:
                course_name = course_name[9:]
            # grade
            course.find('td').find('b').decompose() # get rid of the extra text in this element
            grade = course.find('td').text.strip() # LIKE [A, 98] or [A]
            grade_content = grade.split('(')
            letter_grade_idx = 0
            percent_grade_idx = 1
            if len(grade_content) < 2:
                grade = grade_content[letter_grade_idx]
            else:
                grade = grade_content[percent_grade_idx]

            percent_grade = canonicalize_grade(grade)
            truncate_on = ": "
            course_name = canonicalize_course_title(course_name, truncate_on=truncate_on, truncate_before=True)
            parsed[course_name] = percent_grade
        return parsed

    async def collect_from_pulse(self):
        # Wait for the Pulse table to exist in the DOM. If not, click left-menu "Pulse".
        try:
            await self.page.locator("#SP-Pulse").wait_for(state="attached", timeout=10_000)
        except PlaywrightTimeout:
            try:
                menu_pulse = self.page.locator("tr#Pulse, td.td2_action:has-text('Pulse')")
                if await menu_pulse.count() > 0:
                    await menu_pulse.first.click()
                    await self.page.wait_for_timeout(500)
                    await self.page.locator("#SP-Pulse").wait_for(state="attached", timeout=7_000)
            except Exception:
                pass
        # Wait until tbody has at least one row with cells (guards against hydration lag)
        try:
            await self.page.wait_for_function(
                """(sel) => {
                    const t = document.querySelector(sel);
                    if (!t) return false;
                    const body = t.tBodies && t.tBodies[0];
                    if (!body || !body.rows || body.rows.length === 0) return false;
                    return body.rows[0].cells && body.rows[0].cells.length > 0;
                }""",
                arg="#SP-Pulse",
                timeout=4_000,
            )
        except PlaywrightTimeout:
            return {}

        # Map the header indices so we don’t rely on column order.
        header_cells = self.page.locator("#SP-Pulse thead th")
        header_count = await header_cells.count()
        header_texts: list[str] = []
        for i in range(header_count):
            try:
                t = await header_cells.nth(i).text_content()
                header_texts.append((t or "").strip())
            except Exception:
                header_texts.append("")

        def col_idx(name: str) -> int | None:
            lname = name.lower()
            for j, h in enumerate(header_texts):
                if h.lower() == lname:
                    return j
            return None

        idx_class = col_idx("Class")
        idx_pct = col_idx("Pct")
        idx_letter = col_idx("CurrentGrade")

        if idx_class is None or (idx_pct is None and idx_letter is None):
            return {}

        # Extract rows
        rows = self.page.locator("#SP-Pulse tbody tr")
        n = await rows.count()
        parsed: Dict[str, Any] = {}

        for r in range(n):
            cells = rows.nth(r).locator("td")
            ccount = await cells.count()
            if ccount == 0:
                continue

            async def safe_text(j: int | None) -> str:
                if j is None or j < 0 or j >= ccount:
                    return ""
                try:
                    text = await cells.nth(j).text_content()
                    return (text or "").strip()
                except Exception as e:
                    print(e)
                    return ""

            course = (await safe_text(idx_class)).upper()
            pct_s = await safe_text(idx_pct) if idx_pct is not None else ""
            letter = await safe_text(idx_letter) if idx_letter is not None else ""

            # Normalize percentage: "82.0%" → 82.0
            if pct_s:
                grade = canonicalize_grade(pct_s)
            elif letter:
                grade = canonicalize_grade(letter)
            else:
                continue

            truncate_on = ": "
            course = canonicalize_course_title(course, truncate_on=truncate_on, truncate_before=True)
            if course:
                parsed[course] = grade

        return parsed
