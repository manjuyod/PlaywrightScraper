from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import List, Dict, Any, Optional, Tuple

from bs4 import BeautifulSoup
from playwright.async_api import Page, Frame
from .base import PortalEngine, PlaywrightTimeout
from . import register_portal  # helper we'll create in __init__.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_not_exception_type


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
        try:
            # Start tracing for debugging and audit (screenshots and DOM snapshots)
            await self.page.context.tracing.start(screenshots=True, snapshots=True)
            # Navigate to login page
            await self.page.goto(self.login_url, wait_until="domcontentloaded")
            await self.page.fill("input[name='Pin']", self.sid)
            await self.page.fill("input[name='Password']", self.pw)
            # Wait briefly to ensure values are registered
            await self.page.wait_for_timeout(500)
            login_button = self.page.locator("form button:has-text('Login')")
            # hit enter
            await self.page.locator("input[name='Password']").press("Enter")
            await self.page.wait_for_timeout(500) # allow population
            login_error = False
            try:
                login_error = await self.page.get_by_text("Login Not Found").count() > 0
            except: # not a failed login if this errors
                pass
            await self.raise_login_error_if(login_error)
            # Wait until the URL contains 'PortalMainPage' indicating successful login
            await self.page.wait_for_url(lambda url: "PortalMainPage" in url, timeout=20_000)
            # Wait for network to be idle to ensure the home page has loaded
            await self.page.wait_for_load_state("networkidle")
        except Exception as e:
            print(e)
            raise
        finally:
            # Stop tracing after login
            await self.page.context.tracing.stop()
            # await self.page.pause()
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """
        Scrape the Pulse table on PortalMainPage and return:
        {"parsed_grades": {"COURSE NAME": 93.4 or "A", ...}}
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
            raise
            print(e)
        finally:
            pass
            # await self.page.pause()
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
            grade = course.find('td').text.strip() # may be a pair of letter grade and percentage, or just a letter grade
            grade_content = grade.split('(')
            letter_grade_idx = 0
            percent_grade_idx = 1
            percent_grade = None
            if len(grade_content) < 2:
                percent_grade = self.percent_from_letter_grade(grade_content[letter_grade_idx])
            else:
                percent_grade = grade_content[percent_grade_idx].replace('%', '').replace(')', '')
            print(grade_content)
            print(course_name, percent_grade)
            parsed[course_name] = percent_grade

        # await self.page.pause()
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
            html = await self.page.content()
            print("Pulse table had no rows. Dumping snippet for debug…")
            print(html[:4000])
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
        idx_term = col_idx("Term")
        idx_pct = col_idx("Pct")
        idx_letter = col_idx("CurrentGrade")

        if idx_class is None or (idx_pct is None and idx_letter is None):
            html = await self.page.content()
            print("Missing expected headers. Headers seen:", header_texts)
            print(html[:4000])
            return {}

        # Extract rows
        rows = self.page.locator("#SP-Pulse tbody tr")
        n = await rows.count()
        parsed: Dict[str, str] = {}

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
            term = await safe_text(idx_term) if idx_term is not None else ""
            pct_s = await safe_text(idx_pct) if idx_pct is not None else ""
            letter = await safe_text(idx_letter) if idx_letter is not None else ""

            # Normalize percentage: "82.0%" → 82.0
            value: Any
            if pct_s:
                pct_norm = pct_s.replace("%", "").replace("(", "").replace(")", "").strip()
                try:
                    value = float(pct_norm)
                except ValueError:
                    value = pct_s  # unexpected formatting, keep raw
            elif letter:
                value = letter
            else:
                continue

            if course:
                parsed[course] = value

        return parsed