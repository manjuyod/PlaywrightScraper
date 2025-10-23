from __future__ import annotations
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import re
from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@register_portal("schoology")
class Schoology(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        # 1) Load login page
        await self.page.goto(self.login_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(500)

        # Are we on a login page?
        if "login" in self.page.url:
        # If so complete normal flow
            # 2) Fill & submit
            await self.page.fill("#edit-mail", self.sid)
            await self.page.fill("#edit-pass", self.pw)
            await self.page.click("#edit-submit")
            # 3) Give it time to load the gradebook table
            await self.page.wait_for_timeout(8000)
        else:
        # If not there should be a "Students" option to select to move forward
            await self.page.get_by_role('link', name='Students', exact=True).click() # aria label [Students]
            await self.page.wait_for_timeout(5000)
            # in this case, the portal uses Microsoft SSO
            await self.microsoft_login()
        # navigate to the grades page
        await self.page.get_by_role('button', name='Grades').click()
        await self.page.wait_for_timeout(1000) # second delay to prevent 'Too many requests' error
        await self.page.get_by_text('Grade Report').click()
        await self.page.wait_for_timeout(3000)

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
