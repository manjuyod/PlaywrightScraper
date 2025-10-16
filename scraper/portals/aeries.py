from __future__ import annotations

from typing import Any, Dict, Optional

import bs4
from playwright.async_api import Locator
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

from . import register_portal
from .base import PortalEngine, PlaywrightTimeout


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
            await self.page.context.tracing.start(screenshots=True, snapshots=True)
            await self.page.goto(self.login_url, timeout=60000) # it should never take 60 seconds to reach the page, just for safety
            # Username
            await self.page.fill("input#portalAccountUsername", self.sid)
            await self.page.wait_for_timeout(2000)
            await self.page.locator("#next").click()
            # Password or Google Sign in
            try:
                await self.page.fill("input#portalAccountPassword", self.pw, timeout=3000)
                await self.page.wait_for_timeout(2000)
                await self.page.locator("#LoginButton").click()
            except PlaywrightTimeout: # password field DNE, must be Google signin
                await self.page.locator("#LoginButton").click()
                await self.google_signin()


            #handle failed login
            error_box: Locator = self.page.locator("#errorContainer")
            await self.raise_if_login_error(await error_box.is_visible())

            # if await error_box.is_visible():
            #     error_msg = await self.page.locator("#errorMessage").inner_text()
            #     print(f"Login Error: {error_msg}")
            #     raise LoginError(error_msg)

            await self.page.wait_for_url('**/Dashboard**', timeout=30000)
            await self.page.wait_for_load_state(timeout=45000)
        except Exception as e:
            print(e)
        finally:
            await self.page.context.tracing.stop()
            print("stopped tracing")
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
            await self.raise_if_login_error("Dashboard" not in self.page.url)
            await self.page.wait_for_timeout(3000) # wait some to allow population

            soup = await self.get_soup()
            courses_dict = {}

            # get class table
            class_table = soup.find('div', id="divClass")
            if 'nmusd' not in self.login_url: # for all aeries but newport mesa, follow this flow
                if class_table is None: # failed to find class table
                    await self.page.click("#StudentNameDropDown")
                    await self.page.click("#StudentNameDropDownMenu")
                    await self.page.wait_for_load_state()
                    await self.page.wait_for_timeout(3000)
                    # try again with new page
                    soup = await self.get_soup()
                    class_table = soup.find('div', id='divClass')
                # parse the class table
                class_cards = class_table.select('div.Card')
                print(f"[AERIES] found {len(class_cards)} courses")
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
                # print(course_table)
                for course in course_table:
                    course_name: bs4.Tag = course.find("td", {'data-tcfc': 'CRS.CO'}) # course name
                    course_name.find('label').decompose()
                    course_name = course_name.get_text(strip=True)

                    course_letter = course.find('td', {'data-tcfc': 'GRD.M1'}) # course letter
                    course_letter = course_letter.find('label')
                    if course_letter is None:
                        continue # skip the courses without a letter
                    course_letter = course_letter.get_text(strip=True)
                    print("course: ", course_name, "grade: ", course_letter)
                    grade = float(self.percent_from_letter_grade(course_letter)) # nmusd uses letter grades, no numbers, so we parse here
                    if grade > 0: # add to dictionary
                        courses_dict[course_name.upper()] = grade
            print(f"[AERIES] parsed {len(courses_dict)}: {courses_dict}")
            return {"parsed_grades": courses_dict}
        except Exception as e:
            print(e)
        finally:
            # await self.page.pause()
            await self.page.context.tracing.stop()



    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        # await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
