from __future__ import annotations
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import re
from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from scraper.portals.utils import universal_login_flow, wait_after_nav


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
                '',
                '',
                google_callback=self.google_login,
                sso_login_selector=sso_login_selector
            )
            await wait_after_nav(self.page, wait_after_load=4000)

            await self.page.locator("#data-tab").get_by_role("button", name="Grades").click() # this button on the front page takes us to the grades page
            await self.page.wait_for_timeout(3000)
        except Exception as e:
            print(e)
            raise
        finally:
            # await self.page.pause()
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
            courses = course_table.find_all('tr')
            print(f'found {len(courses)} courses')
            for course in courses:
                columns = course.find_all('td')
                if len(columns) >= 2:
                    title = columns[0].text
                    # format title like [title - Mr./Ms. teacher]
                    index = len(title)
                    if '- Ms' in title:
                        index = title.find('- Ms')
                    elif '- Mr' in title:
                        index = title.find('- Mr')
                    if index > 0:
                        title = title[:index - 1]

                    # format grade like [letter percent] or [letter]
                    grade = columns[1].text
                    grade = grade[2:6]
                    # print(f'found {title}\n\tgrade: {grade}')
                    try:
                        grade = float(grade)
                        parsed[title] = grade
                    except ValueError: # NaN Grade
                        continue
        except Exception as e:
            print(e)
        finally:
            print(parsed)
            # await self.page.pause()
            return parsed
