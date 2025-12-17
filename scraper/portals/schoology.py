from __future__ import annotations
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import re
from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import *
@register_portal("schoology")
class Schoology(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            username_selector = '#edit-mail'
            password_selector = '#edit-pass'
            sso_login_selector = 'a[href*="Student"]'
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                password_selector,
                microsoft_callback=self.microsoft_login,
                sso_login_selector=sso_login_selector
            )
            await wait_after_nav(self.page, wait_after_load=5000)

            # navigate to the grades page
            await self.page.get_by_role('button', name='Grades').click()
            await self.page.wait_for_timeout(1000) # second delay to prevent 'Too many requests' error
            await self.page.get_by_text('Grade Report').click()
            await self.page.wait_for_timeout(3000)
        except Exception as e:
            print(e)
            await self.page.pause()
            raise

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
            courses = soup.find_all("div", id=re.compile("^s-js-gradebook-course"))
            print(f'found {len(courses)} courses')
            for course in courses:
                title = course.find(class_="gradebook-course-title").text
                grade = course.find(class_="course-grade-value").text
                # format course title (like 'title: term - period - room')
                if ':' in title:
                    title = title[:title.index(':')]
                # format grade (like letter (%) or %
                if grade == 'N/A':
                    continue
                if '(' in grade:
                    grade = grade[grade.index('(')+1:]
                grade = grade[:grade.index('%')]
                print(f'found {title}\n\tgrade: {grade}')
                parsed[title] = grade
        finally:
            print(parsed)
            return parsed
