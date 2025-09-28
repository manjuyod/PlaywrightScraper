from __future__ import annotations

import re
from datetime import datetime, timedelta  # ← added timedelta
from typing import Any, Dict, List, Optional, Tuple

import bs4
from bs4 import BeautifulSoup
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential, retry_if_not_exception_type, RetryError)
from .base import PortalEngine
from . import register_portal, LoginError
from playwright.async_api import Locator, Dialog, TimeoutError, Page

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
        retry=retry_if_not_exception_type(LoginError),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the Aeries parent portal."""
        try:
            await self.page.context.tracing.start(screenshots=True, snapshots=True)
            await self.page.goto(self.login_url, wait_until="domcontentloaded", timeout=10000)
            # Username
            await self.page.fill("input#portalAccountUsername", self.sid)
            await self.page.wait_for_timeout(2000)
            await self.page.locator("#next").click()

            if 'nmusd' in self.login_url: #newport mesa needs sign in with google
                await self.page.locator("#LoginButton").click()
                await self.google_signin()
                await self.page.wait_for_timeout(5000)
                await self.page.wait_for_url('**/Dashboard**', timeout=10000)
            else:
                # Password
                await self.page.fill("input#portalAccountPassword", self.pw)
                await self.page.wait_for_timeout(2000)
                await self.page.locator("#LoginButton").click()
                #handle failed login
                error_box: Locator = self.page.locator("#errorContainer")
                if await error_box.is_visible():
                    error_msg = await self.page.locator("#errorMessage").inner_text()
                    print(f"Login Error: {error_msg}")
                    raise LoginError(error_msg)
            await self.page.wait_for_load_state('load', timeout=45000)
        except Exception as e:
            print(e)
        finally:
            await self.page.context.tracing.stop()
            print("stopped tracing")
    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(TimeoutError),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Stay on HOME and scrape notifications; return latest Semester Grade per subject."""
        print("\nfetching grades")
        # ensure we have reached the next page
        if "Dashboard" not in self.page.url:
            raise LoginError
        await self.page.wait_for_timeout(3000) # wait some to allow population

        soup = await self.get_soup()
        courses_dict = {}

        # get class table
        class_table = soup.find('div', id="divClass")
        if 'nmusd' not in self.login_url:  # for all aeries except nmusd, nav to the students alternate profile
            if class_table is None: # failed to find class table
                await self.page.click("#StudentNameDropDown")
                await self.page.click("#StudentNameDropDownMenu")
                await self.page.wait_for_load_state()
                await self.page.wait_for_timeout(3000)
                # try again with new page
                soup = await self.get_soup()
                class_table = soup.find('div', id='divClass')
            # parse the class table
            class_cards = class_table.select('div.Card.CardWithPeriod')
            for card in class_cards:  # parse the cards
                # course name
                class_link = card.find("a", class_="TextHeading")
                course_name: str = class_link.text.strip()
                # grade
                grade_div = card.find("div", class_="Grade")
                grade_span = grade_div.find("span")
                if grade_span is not None:
                    grade_str: str | None = grade_span.text.strip() if grade_span is not None else None
                    grade_str = grade_str.replace("(", "").replace(")", "").replace("%","") if grade_str is not None else None
                    grade = grade_str
                    # add to dictionary
                    courses_dict[course_name.upper()] = grade
        else: # newport portals are weird
            await self.page.click('#NavMainGrades')
            await self.page.click('#NavSubGrades')
            await self.page.wait_for_url('**/Grades**', timeout=10000)
            soup = await self.get_soup()
            # find the table first
            table = soup.find("table", attrs={"id": "ctl00_MainContent_subGRD_tblEverything"})
            # classes
            course_table = table.select("tr[id$='ReadRow1']")
            print(course_table)
            for course in course_table:
                course_name = course.find("td", {'data-tcfc': 'CRS.CO'}) # course name
                course_name.find('label').decompose()
                course_name = course_name.get_text(strip=True)

                course_letter = course.find('td', {'data-tcfc': 'GRD.M1'}) # course letter
                course_letter.find('label').decompose()
                course_letter = course_letter.get_text(strip=True)
                print(course_name, course_letter)
                grade = float(self.percent_from_letter_grade(course_letter)) # nmusd uses letter grades, no numbers, so we parse here
                if grade > 0:
                    # add to dictionary
                    courses_dict[course_name.upper()] = grade
        print(f"[AERIES] found {len(courses_dict)}: {courses_dict}")
                # await self.page.pause()
        return {"parsed_grades": courses_dict}

    # helper
    @staticmethod
    def percent_from_letter_grade(letter_grade: str):
        minus = letter_grade.endswith("-")
        plus = letter_grade.endswith("+")
        modifier = -5 if minus else 5 if plus else 0
        grade = 95
        if modifier != 0:
            letter_grade = letter_grade.replace("-", "")
            letter_grade = letter_grade.replace("+", "")
        match letter_grade:
            case 'A': pass
            case 'B': grade -= 11 # 89
            case 'C': grade -= 21 # 79
            case 'D': grade -= 31 # 69
            case 'F': grade -= 40 # 60
            case _: grade = -1
        return grade + modifier if grade > 0 else grade


    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        # await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
