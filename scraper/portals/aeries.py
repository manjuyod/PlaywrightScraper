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

            soup = await self.get_soup()
            courses_dict = {}

            # get class table
            class_table = soup.find('div', id="divClass")
            # if 'nmusd' not in self.login_url: # for all aeries but newport mesa, follow this flow
            if class_table is None: # failed to find class table
                await self.page.click("#StudentNameDropDown")
                await self.page.click("#StudentNameDropDownMenu")
                await self.page.wait_for_load_state()
                await self.page.wait_for_timeout(3000)
                # try again with new page
                soup = await self.get_soup()
                class_table = soup.find('div', id='divClass')
            if class_table is not None and len(class_table.select('div.Card')) > 0:
                # parse the class table
                class_cards = class_table.select('div.Card')
                print(f"[AERIES] found {len(class_cards)}")
                for card in class_cards:  # parse the cards
                    # course name
                    class_link = card.find("a", class_="TextHeading")
                    course_name: str = class_link.text.strip()
                    # grade
                    grade_div = card.find("div", class_="Grade")
                    grade_span = grade_div.find("span")
                    if grade_span is not None: # as long as the grade exists
                        grade_str: str | None = grade_span.text.strip() if grade_span is not None else None
                        grade_str = grade_str.replace("(", "").replace(")", "").replace("%","") if grade_str is not None else None
                        courses_dict[course_name.upper()] = grade_str # add to dictionary
            else: # if we dont have grades on the home page
                await self.page.click('#NavMainGrades')
                await self.page.click('#NavSubGrades') # sometimes this doesn't exist and we should bail early
                await self.page.wait_for_url('**/Grades**', timeout=10000)
                soup = await self.get_soup()
                # find the table first
                table = soup.find("table", attrs={"id": "ctl00_MainContent_subGRD_tblEverything"})
                # classes
                course_table = table.select("tr[id$='ReadRow1']")
                # print(course_table)
                for course in course_table:
                    # print(course)
                    course_name: bs4.Tag | None = course.find("td", {'data-tcfc': 'CRS.CO'}) # course name
                    if course_name == None:
                        continue
                    course_name.find('label').decompose()
                    course_name = course_name.get_text(strip=True)
                    course_letter: bs4.Tag | str | None = course.find('td', {'data-tcfc': 'GRD.M2'}) # course letter
                    if course_letter == None:
                        continue # skip the courses without a letter
                    course_letter_label: bs4.Tag | None = course_letter.find('label')
                    if course_letter_label == None:
                        print("Current course letter", course_letter.get_text())
                        continue
                    course_letter_label.decompose()
                    course_letter = course_letter.get_text(strip=True)
                    print("course: ", course_name, "grade: ", course_letter)
                    grade = canonicalize_grade(course_letter)
                    if grade > 0: # add to dictionary
                        courses_dict[course_name.upper()] = grade
            print(f"[AERIES] parsed {len(courses_dict)}: {courses_dict}")
            return {"parsed_grades": courses_dict}
        except Exception as e:
            print(e)
            raise
        finally:
            # await self.page.pause()
            await self.page.context.tracing.stop()



    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        # await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
