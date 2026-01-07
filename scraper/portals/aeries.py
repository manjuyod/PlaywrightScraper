from __future__ import annotations

from typing import Any, Dict, Optional

import bs4
from playwright.async_api import Locator
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from . import register_portal
from .base import PortalEngine, PlaywrightTimeout
from .utils import *


@register_portal("aeries")
class Aeries(PortalEngine):
    """Portal scraper for Aeries portal.

    The class uses Playwright to automate login and extract quarter grades
    for each course. Grades are returned as a list of course/grade
    dictionaries under the ``parsed_grades`` key.
    """
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the Aeries parent portal."""
        try:
            username_selector = 'input#portalAccountUsername'
            password_selector = 'input#portalAccountPassword'
            sso_login_selector = '#LoginButton'
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                password_selector,
                google_callback=self.google_login,
                alt_sso_callback = self.iusd_login,
                sso_login_selector=sso_login_selector
            )
            await wait_after_nav(self.page, pattern='**/Dashboard**', timeout=30000)
        except Exception as e:
            print(e)
            raise
        finally:
            await self.page.context.tracing.stop()
            print("stopped tracing")

    async def iusd_login(self):
        username_selector = '#input28'
        pw_selector = '#input62'
        await universal_login_flow(
            self.page,
            self.page.url,
            self.sid,
            self.pw,
            username_selector,
            pw_selector
        )
    async def nav_to_grades(self):
        main_grades_selector = '#NavMainGrades'
        sub_grades_selector = '#NavSubGrades'
        await self.page.click(main_grades_selector)
        await self.page.click(sub_grades_selector)  # sometimes this doesn't exist and we should bail early
        await wait_after_nav(self.page, pattern='**/Grades**', timeout=5000)
    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Stay on HOME and scrape notifications; return latest Semester Grade per subject."""
        print("\nfetching grades")
        try:
            # ensure we have reached the next page
            await self.raise_login_error_if("Dashboard" not in self.page.url)
            await self.page.wait_for_timeout(3000) # wait some to allow population
            await self.nav_to_grades()

            table_selector = "tr[id$='ReadRow1']"
            course_selector = "td[data-tcfc='CRS.CO']"
            grade_selector = "td[data-tcfc='GRD.M2']"

            courses_dict = await grades_table_to_dict(
                self.page,
                table_selector,
                course_selector,
                grade_selector,
                decompose_labels=True
            )
            # soup = await self.get_soup()
            # courses_dict = {}
            #
            # course_table = soup.select(table_selector)
            # # print(course_table)
            # for course in course_table:
            #     # print(course)
            #     course_elem: bs4.Tag | None = course.select_one(course_selector)  # course name
            #     if course_elem is None or course_elem.find('label') is None:
            #         continue
            #     course_elem.find('label').decompose()
            #     course_name = course_elem.get_text(strip=True)
            #
            #     grade_elem: bs4.Tag | None = course.select_one(grade_selector)  # course grade
            #     if grade_elem is None or grade_elem.find('label') is None:
            #         continue  # skip the courses without a letter
            #     grade_elem.find('label').decompose()
            #     course_grade = grade_elem.get_text(strip=True)
            #
            #     print(course_name, " grade: ", course_grade)
            #     grade = canonicalize_grade(course_grade)
            #     if grade is not None:  # add to dictionary
            #         courses_dict[course_name.upper()] = grade
            #
            print(f"[AERIES] parsed {len(courses_dict)}: {courses_dict}")
            return {"parsed_grades": courses_dict}
        except Exception as e:
            print(e)
            raise
        finally:
            await self.page.context.tracing.stop()

    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        # await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
