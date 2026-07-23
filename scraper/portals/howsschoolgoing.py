from __future__ import annotations
from typing import Any, Dict, Optional
from bs4.element import Tag
from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from scraper.portals.utils import universal_login_flow, wait_after_nav, truncate_title, canonicalize_grade


@register_portal("howsschoolgoing")
class HowsSchoolGoing(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            sso_login_selector = 'a[href*="google"]'
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector='',
                password_selector='',
                google_callback=self.google_login,
                sso_login_selector=sso_login_selector
            )
            await wait_after_nav(self.page, wait_after_load=4000)

            await self.page.locator("#data-tab").get_by_role("button", name="Grades").click() # this button on the front page takes us to the grades page
            await self.page.wait_for_timeout(3000)
        except Exception as e:
            self.logger.error(
                "portal.login.failed", extra={"exception_type": type(e).__name__}
            )
            raise
        finally:
            pass
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        if 'grades' not in self.page.url:
            raise self.LoginError('No grades page')
        parsed = {}
        try:
            soup = await self.get_soup()
            course_table = soup.find("div", class_="dataSource_Common_StudentProfile_Grades_GradesTable")
            if not isinstance(course_table, Tag):
                self.logger.warning("portal.parse.grade_table_missing")
                return {}
            courses = course_table.find_all('tr') # possible 
            self.logger.debug(
                "portal.fetch.courses_found", extra={"course_count": len(courses)}
            )
            for course in courses:
                if not isinstance(course, Tag):
                    continue
                columns = course.find_all('td')
                if len(columns) >= 2:
                    title = columns[0].get_text(strip=True)
                    grade_text = columns[1].get_text(strip=True)

                    # format title like [title - Mr./Ms. teacher]
                    title = truncate_title(title, '-Ms', False)
                    title = truncate_title(title, '-Mr', False)
                    # format grade like [letter percent] or [letter]
                    grades = grade_text.split(' ')
                    if len(grades) == 2:
                        grade = canonicalize_grade(grades[1])
                    else:
                        grade = canonicalize_grade(grades[0])
                    if grade:
                        parsed[title] = grade
        except Exception as e:
            self.logger.error(
                "portal.fetch.failed", extra={"exception_type": type(e).__name__}
            )
        finally:
            self.logger.info(
                "portal.fetch.completed", extra={"course_count": len(parsed)}
            )
            return parsed
